#!/usr/bin/env python3
"""
Virchow RAG — Live Pipeline Monitor
=====================================
Shows real-time status of all services, queue depths, document processing
stats, and recent per-file progress.

Run:
    /Users/macai/Desktop/virchow_rag/ingest/venv_native/bin/python3 monitor.py
"""

import time
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import requests
import psycopg2
import redis as redis_lib
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich import box

# ── Config ────────────────────────────────────────────────────────────────────
PG_CONN = dict(host="192.168.10.10", port=5433, dbname="virchow_dev",
               user="postgres", password="Eppl$456!")
REDIS_HOST = "localhost"
REDIS_PORT = 6379
RABBIT_API = "http://localhost:15672/api"
RABBIT_AUTH = ("guest", "guest")
INGEST_URL  = "http://localhost:8000"
RETRIEVAL_URL = "http://localhost:8080"
OLLAMA_URL  = "http://localhost:11434"
SEAWEEDFS_MASTER = "http://192.168.10.10:9333"

REFRESH_SECONDS = 5

console = Console()


# ── Data collectors ───────────────────────────────────────────────────────────

def _get(url, auth=None, timeout=3):
    try:
        r = requests.get(url, auth=auth, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def collect_services():
    rows = []
    checks = [
        ("ingest-api",   f"{INGEST_URL}/health"),
        ("retrieval",    f"{RETRIEVAL_URL}/health"),
        ("web",          "http://localhost:3000"),
        ("ollama",       f"{OLLAMA_URL}/api/tags"),
        ("seaweedfs",    f"{SEAWEEDFS_MASTER}/cluster/status"),
    ]
    for name, url in checks:
        try:
            r = requests.get(url, timeout=2)
            ok = r.status_code < 400
        except Exception:
            ok = False
        rows.append((name, ok))

    # Redis
    try:
        rc = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)
        rc.ping()
        rows.append(("redis", True))
    except Exception:
        rows.append(("redis", False))

    # RabbitMQ
    data = _get(f"{RABBIT_API}/overview", auth=RABBIT_AUTH)
    rows.append(("rabbitmq", data is not None))

    return rows


def collect_queues():
    data = _get(f"{RABBIT_API}/queues/%2F", auth=RABBIT_AUTH)
    if not data:
        return []
    result = []
    wanted = {"rag.q.priority", "rag.q.normal", "rag.q.large", "rag.q.dead"}
    for q in data:
        if q["name"] in wanted:
            result.append({
                "name":    q["name"],
                "ready":   q.get("messages_ready", 0),
                "unacked": q.get("messages_unacknowledged", 0),
                "total":   q.get("messages", 0),
                "rate":    q.get("messages_details", {}).get("rate", 0.0),
            })
    result.sort(key=lambda x: x["name"])
    return result


def collect_doc_stats():
    try:
        conn = psycopg2.connect(**PG_CONN)
        cur = conn.cursor()
        cur.execute("""
            SELECT embed_status, COUNT(*) as n
            FROM documents
            GROUP BY embed_status
            ORDER BY embed_status
        """)
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM chunks")
        chunks = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM embeddings")
        embeddings = cur.fetchone()[0]
        # table may be colpali_page_embeddings or colpali_embeddings depending on migration
        try:
            cur.execute("SELECT COUNT(*) FROM colpali_page_embeddings")
        except Exception:
            conn.rollback()
            try:
                cur.execute("SELECT COUNT(*) FROM colpali_embeddings")
            except Exception:
                conn.rollback()
                cur.execute("SELECT 0")
        colpali = cur.fetchone()[0]
        conn.close()
        return {
            "status_counts": dict(rows),
            "chunks": chunks,
            "embeddings": embeddings,
            "colpali": colpali,
        }
    except Exception as e:
        return {"error": str(e)}


def collect_recent_docs():
    """Last 8 documents with their current status."""
    try:
        conn = psycopg2.connect(**PG_CONN)
        cur = conn.cursor()
        cur.execute("""
            SELECT file_name, embed_status,
                   COALESCE(last_embedded_at, created_at) as ts,
                   page_count, doc_type, doc_month
            FROM documents
            ORDER BY COALESCE(last_embedded_at, created_at) DESC
            LIMIT 8
        """)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def collect_redis_progress():
    """Active file_ids with their last stage update."""
    try:
        rc = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2, decode_responses=True)
        keys = rc.keys("stage:*")
        active = []
        for k in sorted(keys)[:8]:
            val = rc.hgetall(k)
            if val:
                active.append(val)
        return sorted(active, key=lambda x: x.get("ts", ""), reverse=True)[:6]
    except Exception:
        return []


def collect_ollama():
    data = _get(f"{OLLAMA_URL}/api/tags", timeout=3)
    if not data:
        return []
    return [m["name"] for m in data.get("models", [])]


def collect_workers():
    """Detect running native ingest worker processes."""
    try:
        out = subprocess.check_output(
            ["ps", "aux"], text=True, stderr=subprocess.DEVNULL
        )
        workers = []
        for line in out.splitlines():
            if "ingest.main" in line and "grep" not in line:
                parts = line.split()
                pid   = parts[1]
                cpu   = parts[2]
                mem   = parts[3]
                workers.append({"pid": pid, "cpu": cpu, "mem": mem})
        return workers
    except Exception:
        return []


def collect_throughput():
    """Docs completed in last 60 minutes."""
    try:
        conn = psycopg2.connect(**PG_CONN)
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM documents
            WHERE embed_status = 'completed'
              AND last_embedded_at >= NOW() - INTERVAL '60 minutes'
        """)
        last_hour = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM documents
            WHERE embed_status = 'completed'
              AND last_embedded_at >= NOW() - INTERVAL '10 minutes'
        """)
        last_10 = cur.fetchone()[0]
        conn.close()
        return {"last_hour": last_hour, "last_10min": last_10}
    except Exception:
        return {"last_hour": 0, "last_10min": 0}


# ── Render panels ─────────────────────────────────────────────────────────────

def render_services(services):
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Service", style="bold")
    t.add_column("Status")
    for name, ok in services:
        status = Text("● RUNNING", style="green bold") if ok else Text("● DOWN", style="red bold")
        t.add_row(name, status)
    return Panel(t, title="[bold cyan]Services[/]", border_style="cyan")


def render_queues(queues):
    t = Table(box=box.SIMPLE, padding=(0, 1))
    t.add_column("Queue", style="bold")
    t.add_column("Ready", justify="right")
    t.add_column("In-Flight", justify="right")
    t.add_column("Total", justify="right")
    t.add_column("Rate/s", justify="right")
    if not queues:
        t.add_row("[dim]no data[/]", "", "", "", "")
    for q in queues:
        name_short = q["name"].replace("rag.q.", "")
        style = "red" if name_short == "dead" and q["total"] > 0 else ""
        t.add_row(
            f"[{style}]{name_short}[/]" if style else name_short,
            str(q["ready"]),
            str(q["unacked"]),
            f"[bold]{q['total']}[/]",
            f"{q['rate']:.1f}",
        )
    return Panel(t, title="[bold cyan]RabbitMQ Queues[/]", border_style="cyan")


def render_doc_stats(stats, throughput):
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    if "error" in stats:
        t.add_row("[red]DB error[/]", stats["error"][:40])
    else:
        counts = stats.get("status_counts", {})
        total = sum(counts.values())
        t.add_row("Total documents", f"[bold]{total}[/]")
        status_colors = {
            "completed": "green", "processing": "yellow",
            "pending": "dim", "failed": "red",
        }
        for status, n in sorted(counts.items()):
            color = status_colors.get(status, "white")
            t.add_row(f"  [{color}]{status}[/]", f"[{color}]{n}[/]")
        t.add_row("Chunks", str(stats.get("chunks", 0)))
        t.add_row("Embeddings", str(stats.get("embeddings", 0)))
        t.add_row("ColPali pages", str(stats.get("colpali", 0)))
        t.add_row("─" * 18, "─" * 6)
        t.add_row("Completed / 10 min", str(throughput.get("last_10min", 0)))
        t.add_row("Completed / 1 hour", str(throughput.get("last_hour", 0)))
    return Panel(t, title="[bold cyan]Document Stats[/]", border_style="cyan")


def render_recent_docs(rows):
    t = Table(box=box.SIMPLE, padding=(0, 1))
    t.add_column("File", max_width=32)
    t.add_column("Status")
    t.add_column("Pages", justify="right")
    t.add_column("Type")
    t.add_column("Month")
    t.add_column("Updated")
    status_colors = {
        "completed": "green", "processing": "yellow bold",
        "pending": "dim", "failed": "red bold",
    }
    for file_name, status, ts, pages, doc_type, doc_month in rows:
        color = status_colors.get(status, "white")
        ts_str = ts.strftime("%H:%M:%S") if ts else ""
        t.add_row(
            file_name[:32],
            f"[{color}]{status}[/]",
            str(pages or ""),
            doc_type or "",
            doc_month or "",
            ts_str,
        )
    if not rows:
        t.add_row("[dim]no documents yet[/]", "", "", "", "", "")
    return Panel(t, title="[bold cyan]Recent Documents[/]", border_style="cyan")


def render_active_jobs(jobs):
    t = Table(box=box.SIMPLE, padding=(0, 1))
    t.add_column("File")
    t.add_column("Stage")
    t.add_column("Pct", justify="right")
    t.add_column("Updated")
    stage_colors = {
        "ocr": "yellow", "embedding": "blue", "storing": "magenta",
        "done": "green", "error": "red", "chunking": "cyan",
        "preprocessing": "dim",
    }
    for j in jobs:
        stage = j.get("stage", "")
        color = stage_colors.get(stage, "white")
        fname = j.get("filename", j.get("file_id", ""))[:30]
        pct = j.get("pct", "")
        ts = j.get("ts", "")
        t.add_row(fname, f"[{color}]{stage}[/]", f"{pct}%", ts[-8:] if ts else "")
    if not jobs:
        t.add_row("[dim]no active jobs[/]", "", "", "")
    return Panel(t, title="[bold cyan]Active Pipeline Jobs (Redis)[/]", border_style="cyan")


def render_workers(workers):
    t = Table(box=box.SIMPLE, padding=(0, 1))
    t.add_column("PID", style="bold", justify="right")
    t.add_column("CPU%", justify="right")
    t.add_column("MEM%", justify="right")
    if not workers:
        t.add_row("[red bold]NO WORKERS RUNNING[/]", "", "")
    for w in workers:
        cpu_val = float(w["cpu"])
        cpu_color = "green" if cpu_val > 10 else "yellow"
        t.add_row(w["pid"], f"[{cpu_color}]{w['cpu']}[/]", w["mem"])
    count = len(workers)
    color = "green bold" if count > 0 else "red bold"
    label = f"[{color}]{'● RUNNING' if count > 0 else '● STOPPED'}  ({count} worker{'s' if count != 1 else ''})[/]"
    return Panel(t, title=f"[bold cyan]Native Workers[/]  {label}", border_style="cyan")


def render_ollama(models):
    needed = {"qwen3-embedding:8b", "qwen3-vl:8b"}
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Model", style="bold")
    t.add_column("Status")
    for m in needed:
        ok = any(m in name for name in models)
        t.add_row(m, Text("● loaded", "green") if ok else Text("● missing", "red"))
    return Panel(t, title="[bold cyan]Ollama Models[/]", border_style="cyan")


def build_layout(services, queues, stats, throughput, recent_docs, active_jobs, ollama_models, workers):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", size=13),
        Layout(name="middle", size=12),
        Layout(name="bottom"),
    )

    layout["header"].update(Panel(
        f"[bold white]Virchow RAG — Live Pipeline Monitor[/]  "
        f"[dim]Refresh every {REFRESH_SECONDS}s   Last: {ts}   Ctrl+C to exit[/]",
        border_style="bright_blue",
    ))

    layout["top"].split_row(
        Layout(render_services(services), name="svc"),
        Layout(render_queues(queues), name="q"),
        Layout(render_workers(workers), name="workers"),
        Layout(render_ollama(ollama_models), name="ollama"),
    )

    layout["middle"].split_row(
        Layout(render_doc_stats(stats, throughput), name="stats"),
        Layout(render_active_jobs(active_jobs), name="jobs"),
    )

    layout["bottom"].update(render_recent_docs(recent_docs))

    return layout


# ── Main loop ─────────────────────────────────────────────────────────────────

def collect_all():
    services      = collect_services()
    queues        = collect_queues()
    stats         = collect_doc_stats()
    throughput    = collect_throughput()
    recent_docs   = collect_recent_docs()
    active_jobs   = collect_redis_progress()
    ollama_models = collect_ollama()
    workers       = collect_workers()
    return services, queues, stats, throughput, recent_docs, active_jobs, ollama_models, workers


def main():
    console.print("[bold bright_blue]Virchow RAG Monitor starting...[/]")
    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                data = collect_all()
                layout = build_layout(*data)
                live.update(layout)
            except KeyboardInterrupt:
                break
            except Exception as e:
                live.update(Panel(f"[red]Monitor error: {e}[/]", border_style="red"))
            time.sleep(REFRESH_SECONDS)

    console.print("[dim]Monitor stopped.[/]")


if __name__ == "__main__":
    main()
