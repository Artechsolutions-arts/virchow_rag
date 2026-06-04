#!/usr/bin/env python3
"""
Virchow RAG — Autonomous Pipeline Supervisor
============================================
Runs 24/7 until all 2959 documents are processed.

Features:
  • Heartbeat every 45s — logs worker count + DB progress
  • Auto-restarts dead workers (target: 5 concurrent)
  • Auto-restarts Docker Desktop if daemon dies
  • Auto-restarts Redis + RabbitMQ containers
  • Resets documents stuck in 'processing' > 20 min
  • Prevents macOS system sleep via caffeinate
  • macOS notifications on crash, recovery, and completion
  • Self-registered as LaunchAgent so it survives terminal close

Stop it:  launchctl unload ~/Library/LaunchAgents/com.virchow.supervisor.plist
  or:     kill $(cat /private/tmp/virchow_supervisor.pid)
Log:      tail -f /private/tmp/virchow_supervisor.log
"""

import os
import sys
import time
import socket
import signal
import pathlib
import subprocess
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────────
TOTAL_DOCS      = None   # resolved from DB at startup
TARGET_WORKERS  = 3
WORKER_LOG      = "/private/tmp/native_worker.log"
SUPERVISOR_LOG  = "/private/tmp/virchow_supervisor.log"
SUPERVISOR_PID  = "/private/tmp/virchow_supervisor.pid"
CHECK_INTERVAL  = 45          # seconds between heartbeats
STUCK_MINUTES   = 600         # minutes before 'processing' doc is re-queued
                              # docs can wait 3+ hrs for OCR lock (4 threads × 148 min/doc)

PROJECT_DIR = "/Users/macai/Desktop/virchow_rag"
PYTHON      = "/Users/macai/Desktop/virchow_rag/ingest/venv_native/bin/python"
DOCKER      = "/usr/local/bin/docker"
OSASCRIPT   = "/usr/bin/osascript"

PG = dict(host="192.168.10.10", port=5433, dbname="virchow_dev",
          user="postgres", password="Eppl$456!")

RABBIT_API      = "http://localhost:15672"
RABBIT_MQ_USER  = "guest"
RABBIT_MQ_PASS  = "guest"


# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    # Write to file only when stdout is a terminal (direct run).
    # When launched via LaunchAgent, stdout IS the log file, so skip to avoid doubles.
    if sys.stdout.isatty():
        try:
            with open(SUPERVISOR_LOG, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ── macOS Notifications ────────────────────────────────────────────────────────
def notify(title, body, sound="Glass"):
    try:
        subprocess.run(
            [OSASCRIPT, "-e",
             f'display notification "{body}" with title "{title}" sound name "{sound}"'],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


# ── Service helpers ─────────────────────────────────────────────────────────────
def port_open(host, port, timeout=2):
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def docker_running():
    try:
        r = subprocess.run([DOCKER, "info"], capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def ensure_docker():
    if docker_running():
        return True
    log("Docker daemon not running — launching Docker Desktop", "WARN")
    notify("Virchow Supervisor ⚠️", "Docker Desktop down — restarting now")
    try:
        subprocess.Popen(["open", "-a", "Docker"])
    except Exception as e:
        log(f"Failed to open Docker: {e}", "ERROR")
        return False
    for i in range(30):
        time.sleep(5)
        if docker_running():
            log(f"Docker daemon ready after {(i + 1) * 5}s")
            return True
    log("Docker Desktop failed to start within 150s", "ERROR")
    notify("Virchow Supervisor ❌", "Docker Desktop failed to start!")
    return False


def ensure_services():
    rabbit_up = port_open("localhost", 5672)
    redis_up  = port_open("localhost", 6379)
    if rabbit_up and redis_up:
        return True

    log(f"Services down — RabbitMQ={'UP' if rabbit_up else 'DOWN'} Redis={'UP' if redis_up else 'DOWN'}", "WARN")
    if not ensure_docker():
        return False

    log("Starting redis + rabbitmq containers...")
    try:
        result = subprocess.run(
            [DOCKER, "compose", "up", "-d", "redis", "rabbitmq"],
            capture_output=True, text=True, cwd=PROJECT_DIR, timeout=90
        )
        out = (result.stdout + result.stderr).strip()
        if out:
            log(f"docker compose: {out}")
    except Exception as e:
        log(f"docker compose failed: {e}", "ERROR")
        return False

    for i in range(18):
        time.sleep(5)
        if port_open("localhost", 5672) and port_open("localhost", 6379):
            log(f"RabbitMQ + Redis up after {(i + 1) * 5}s")
            notify("Virchow Supervisor ✅", "Services restored — workers resuming")
            return True

    log("Services still down after 90s", "ERROR")
    notify("Virchow Supervisor ❌", "RabbitMQ/Redis failed to start!")
    return False


# ── Worker management ───────────────────────────────────────────────────────────
def get_worker_pids():
    """Return PIDs of live ingest worker processes (excludes supervisor itself)."""
    try:
        own_pid = os.getpid()
        out = subprocess.check_output(["ps", "aux"], text=True, stderr=subprocess.DEVNULL)
        pids = []
        for line in out.splitlines():
            # Match workers: python -m ingest.main  (NOT supervisor.py or queue_pending.py)
            if "-m ingest.main" in line and "grep" not in line and "supervisor" not in line:
                parts = line.split()
                pid = int(parts[1])
                if pid != own_pid:
                    pids.append(pid)
        return pids
    except Exception:
        return []


def start_worker():
    env = os.environ.copy()
    env["RUN_TYPE"]      = "worker"
    env["PIPELINE_MODE"] = "sequential"
    try:
        log_fh = open(WORKER_LOG, "a")
        proc   = subprocess.Popen(
            [PYTHON, "-m", "ingest.main"],
            stdout=log_fh, stderr=log_fh,
            cwd=PROJECT_DIR, env=env,
            start_new_session=True,   # own process group — survives supervisor restart
        )
        log(f"Started worker PID {proc.pid}")
        return proc.pid
    except Exception as e:
        log(f"Failed to start worker: {e}", "ERROR")
        return None


def ensure_workers():
    pids  = get_worker_pids()
    alive = len(pids)
    if alive < TARGET_WORKERS:
        deficit = TARGET_WORKERS - alive
        log(f"Workers alive={alive}/{TARGET_WORKERS} — spawning {deficit}", "WARN")
        if alive == 0:
            notify("Virchow Supervisor ⚠️", f"All workers crashed — restarting {deficit}")
        for _ in range(deficit):
            start_worker()
            time.sleep(5)
    return get_worker_pids()


# ── Database helpers ────────────────────────────────────────────────────────────
def get_progress():
    try:
        import psycopg2
        conn = psycopg2.connect(**PG, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("SELECT embed_status, COUNT(*) FROM documents GROUP BY embed_status")
        counts = dict(cur.fetchall())
        conn.close()
        return counts
    except Exception as e:
        log(f"DB error (progress): {e}", "WARN")
        return None


def queue_all_pending():
    """Publish every 'pending' DB document to RabbitMQ (idempotent — safe to re-run)."""
    script = pathlib.Path(PROJECT_DIR) / "queue_pending.py"
    if not script.exists():
        return
    try:
        r = subprocess.run(
            [PYTHON, str(script)],
            capture_output=True, text=True, cwd=PROJECT_DIR, timeout=300
        )
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output"
        log(f"queue_pending: {last}")
    except Exception as e:
        log(f"queue_pending failed: {e}", "WARN")


def fix_stuck_docs():
    """Reset documents stuck in 'processing' > STUCK_MINUTES back to pending."""
    try:
        import psycopg2
        conn = psycopg2.connect(**PG, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("""
            UPDATE documents
               SET embed_status = 'pending'
             WHERE embed_status = 'processing'
               AND COALESCE(processing_started_at, created_at)
                   < NOW() - INTERVAL '%s minutes'
            RETURNING file_name
        """, (STUCK_MINUTES,))
        stuck = cur.fetchall()
        conn.commit()
        conn.close()
        if stuck:
            names = [r[0] for r in stuck]
            log(f"Reset {len(stuck)} stuck doc(s) to pending: {names[:5]}{'...' if len(names)>5 else ''}", "WARN")
        return len(stuck)
    except Exception as e:
        log(f"DB error (stuck docs): {e}", "WARN")
        return 0


def mq_pending_count():
    """Return total ready+unacked messages across all rag.q.* queues, or -1 on error."""
    try:
        import urllib.request, base64, json
        creds = base64.b64encode(f"{RABBIT_MQ_USER}:{RABBIT_MQ_PASS}".encode()).decode()
        req = urllib.request.Request(
            f"{RABBIT_API}/api/queues/%2F",
            headers={"Authorization": f"Basic {creds}"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        return sum(
            q.get("messages_ready", 0) + q.get("messages_unacknowledged", 0)
            for q in data if q.get("name", "").startswith("rag.q.")
        )
    except Exception:
        return -1


# ── Keep system awake ───────────────────────────────────────────────────────────
_caffeinate_proc = None

def start_caffeinate():
    global _caffeinate_proc
    try:
        _caffeinate_proc = subprocess.Popen(
            ["caffeinate", "-i", "-s"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        log(f"caffeinate PID={_caffeinate_proc.pid} — system sleep disabled")
    except Exception as e:
        log(f"caffeinate failed: {e}", "WARN")


def ensure_caffeinate():
    global _caffeinate_proc
    if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
        log("caffeinate stopped — restarting sleep prevention", "WARN")
        start_caffeinate()


# ── Graceful shutdown ───────────────────────────────────────────────────────────
_shutdown = False

def _handle_signal(signum, frame):
    global _shutdown
    log(f"Received signal {signum} — shutting down gracefully")
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── Main loop ───────────────────────────────────────────────────────────────────
def main():
    # Write PID file
    with open(SUPERVISOR_PID, "w") as f:
        f.write(str(os.getpid()))

    # Resolve true total from DB
    global TOTAL_DOCS
    initial = get_progress()
    if initial:
        TOTAL_DOCS = sum(initial.values())
    if not TOTAL_DOCS:
        TOTAL_DOCS = 6341   # fallback

    log("=" * 64)
    log(f"Virchow RAG Supervisor v1.0 — PID {os.getpid()}")
    log(f"Target: {TARGET_WORKERS} workers, {TOTAL_DOCS} total docs")
    log(f"Check interval: {CHECK_INTERVAL}s  Stuck threshold: {STUCK_MINUTES}m")
    log("=" * 64)
    notify("Virchow Supervisor 🚀", f"Pipeline supervisor running — {TOTAL_DOCS} docs to process")

    start_caffeinate()

    # Queue pending docs only on very first startup (empty queue signal)
    # Workers' own recovery handles re-queuing on subsequent restarts
    _initial_progress = get_progress()
    _pending_count = (_initial_progress or {}).get("pending", 0)
    if _pending_count > 0:
        log(f"{_pending_count} pending docs detected — checking if queue needs priming...")
        # Only queue if genuinely no completions yet (fresh start) to avoid duplicates
        _completed_count = (_initial_progress or {}).get("completed", 0)
        if _completed_count == 0:
            log("Fresh start — publishing all pending docs to queue")
            queue_all_pending()
        else:
            log("Workers are mid-run — skipping queue_pending (recovery handles it)")

    last_completed      = 0
    check_count         = 0
    no_progress_ticks   = 0          # consecutive ticks with 0 new completions
    hourly_ticks        = max(1, 3600 // CHECK_INTERVAL)   # ~80 ticks/hour
    stuck_ticks         = max(1, 600  // CHECK_INTERVAL)   # check stuck every 10 min
    STALL_TICKS         = max(3, 10800 // CHECK_INTERVAL)  # ~3 hr stall → requeue (docs take 100+ min)
    IDLE_TICKS          = max(3, 1800  // CHECK_INTERVAL)  # ~30 min idle workers → reprime queue
    MQ_IDLE_TICKS       = max(3, 600   // CHECK_INTERVAL)  # ~10 min: reprime if MQ empty + no progress
    last_worker_pids    = set()

    while not _shutdown:
        check_count += 1
        try:
            # ── 1. Keep system awake ─────────────────────────────────────────
            ensure_caffeinate()

            # ── 2. Ensure Docker + queue services ────────────────────────────
            services_ok = ensure_services()
            if not services_ok:
                log("Services unavailable — waiting 60s before retry", "ERROR")
                time.sleep(60)
                continue

            # ── 3. Ensure workers ─────────────────────────────────────────────
            pids = ensure_workers()

            # Worker-respawn recovery: if the set of live PIDs changed (workers
            # died and were replaced), their claimed 'processing' docs are orphaned.
            # Reset docs processing for > 5 min immediately so new workers can claim.
            current_pids = set(pids)
            if last_worker_pids and current_pids != last_worker_pids:
                dead = last_worker_pids - current_pids
                log(f"Workers respawned — {len(dead)} old PID(s) gone: {dead}. Resetting orphaned docs.", "WARN")
                try:
                    import psycopg2
                    conn = psycopg2.connect(**PG, connect_timeout=5)
                    cur  = conn.cursor()
                    cur.execute("""
                        UPDATE documents SET embed_status='pending'
                         WHERE embed_status='processing'
                           AND COALESCE(processing_started_at, created_at)
                               < NOW() - INTERVAL '1 minute'
                        RETURNING file_name
                    """)
                    reset = cur.fetchall()
                    conn.commit(); conn.close()
                    if reset:
                        log(f"  Reset {len(reset)} orphaned doc(s) — re-priming queue")
                        queue_all_pending()
                    notify("Virchow Supervisor 🔄", f"Workers respawned — {len(reset)} orphaned doc(s) reset")
                except Exception as e:
                    log(f"Orphan-reset failed: {e}", "WARN")
            last_worker_pids = current_pids

            # ── 4. Reset any stuck docs (every 10 min) ────────────────────────
            if check_count % stuck_ticks == 0:
                n_stuck = fix_stuck_docs()
                if n_stuck:
                    notify("Virchow Supervisor 🔁", f"Reset {n_stuck} stuck doc(s) to pending")
                    queue_all_pending()   # ensure reset docs re-enter RabbitMQ

            # ── 5. Progress report ────────────────────────────────────────────
            counts = get_progress()
            if counts:
                completed  = counts.get("completed",  0)
                pending    = counts.get("pending",    0)
                processing = counts.get("processing", 0)
                failed     = counts.get("failed",     0)
                pct        = completed / TOTAL_DOCS * 100
                delta      = completed - last_completed

                log(
                    f"[Heartbeat #{check_count}] "
                    f"workers={len(pids)} "
                    f"done={completed}/{TOTAL_DOCS}({pct:.1f}%) "
                    f"pending={pending} active={processing} "
                    f"failed={failed} +{delta}new"
                )

                if delta > 0:
                    last_completed = completed
                    no_progress_ticks = 0
                else:
                    no_progress_ticks += 1

                # Stall detection: no completions for ~3 hrs AND pending docs exist
                # AND no docs actively processing (workers are genuinely idle, not just slow).
                # processing>0 means workers ARE working — docs just take 100+ min each.
                if no_progress_ticks >= STALL_TICKS and pending > 0 and processing == 0:
                    log(f"STALL DETECTED: {no_progress_ticks} ticks with no progress, {pending} pending, 0 active — re-queuing", "WARN")
                    notify("Virchow Supervisor 🔁", f"Pipeline stalled — re-queuing {pending} pending docs")
                    queue_all_pending()
                    no_progress_ticks = 0

                # Idle-worker check: workers alive but RabbitMQ queue drained (feeders
                # consumed all messages into in-memory queue, then workers restarted and
                # lost them). Re-prime sooner than the 3-hr stall threshold.
                elif (no_progress_ticks >= IDLE_TICKS and pending > 0
                        and processing == 0 and len(pids) > 0):
                    log(f"IDLE WORKERS: {no_progress_ticks} ticks, 0 active, {pending} pending, {len(pids)} workers — re-priming queue", "WARN")
                    notify("Virchow Supervisor 🔁", f"Workers idle — re-priming {pending} pending docs")
                    queue_all_pending()
                    no_progress_ticks = 0

                # MQ empty-queue check: RabbitMQ drained (auto-ack feeders consumed all
                # messages) but pending docs remain in DB and workers have gone idle.
                # Faster than the 30-min IDLE_TICKS — triggers after ~10 min with no progress.
                elif (no_progress_ticks >= MQ_IDLE_TICKS and pending > 0
                        and processing == 0 and len(pids) > 0):
                    mq_depth = mq_pending_count()
                    if mq_depth == 0:
                        log(f"MQ DRAIN DETECTED: {no_progress_ticks} ticks, 0 active, MQ=0, {pending} pending — re-priming queue", "WARN")
                        notify("Virchow Supervisor 🔁", f"MQ drained — re-priming {pending} pending docs")
                        queue_all_pending()
                        no_progress_ticks = 0

                # Warn about failures
                if failed > 0:
                    log(f"{failed} document(s) in failed state", "WARN")

                # Hourly progress notification
                if check_count % hourly_ticks == 0:
                    remaining = TOTAL_DOCS - completed
                    rate_per_hour = delta * hourly_ticks
                    eta = f"{remaining / rate_per_hour:.1f}h" if rate_per_hour > 0 else "unknown"
                    notify(
                        "Virchow Progress 📊",
                        f"{completed}/{TOTAL_DOCS} done ({pct:.0f}%) — ETA ~{eta}"
                    )

                # Re-check total in case new files were added
                new_total = sum(counts.values())
                if new_total > TOTAL_DOCS:
                    added = new_total - TOTAL_DOCS
                    log(f"Total docs grew: {TOTAL_DOCS} → {new_total} (+{added} new files — queuing new pending)")
                    TOTAL_DOCS = new_total
                    queue_all_pending()  # idempotent: only queues current 'pending' set

                # ── DONE ──────────────────────────────────────────────────────
                if completed >= TOTAL_DOCS:
                    log("=" * 64)
                    log("ALL DOCUMENTS COMPLETE! Pipeline finished.")
                    log("=" * 64)
                    notify("Virchow Pipeline ✅ DONE!", f"All {TOTAL_DOCS} documents processed!", sound="Hero")
                    # Gracefully stop workers
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass
                    break
            else:
                log(f"[Heartbeat #{check_count}] workers={len(pids)} — DB unavailable", "WARN")

        except Exception as e:
            log(f"Supervisor loop error: {e}", "ERROR")

        time.sleep(CHECK_INTERVAL)

    # Cleanup
    log("Supervisor exiting")
    if _caffeinate_proc and _caffeinate_proc.poll() is None:
        _caffeinate_proc.terminate()
    try:
        os.remove(SUPERVISOR_PID)
    except Exception:
        pass


if __name__ == "__main__":
    main()
