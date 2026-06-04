#!/usr/bin/env python3
"""
Phase 3 Backfill Script — re-process existing documents.

Downloads each PDF from SeaweedFS, runs the full ingestion pipeline
(OCR → table parse → entity extraction → embed → store), and updates
the document record.

Usage:
    # Dry run — shows what would be processed, touches nothing
    python scripts/backfill.py --dry-run

    # Process all documents, batch of 15
    python scripts/backfill.py --batch-size 15

    # Re-process even already-completed docs (e.g. after model upgrade)
    python scripts/backfill.py --force

    # Process only the first 50 docs (useful for a staged rollout)
    python scripts/backfill.py --max-docs 50

    # Resume from a specific offset (if a previous run was interrupted)
    python scripts/backfill.py --offset 120

Exit codes:
    0 — all processed successfully (or dry run completed)
    1 — one or more documents failed ingestion
"""

import argparse
import logging
import os
import sys
import time

# ── make project root importable ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config import cfg, PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD  # noqa: E402
from src.database.postgres_db import RBACManager                                  # noqa: E402
from src.database.migrations import run_all_migrations                            # noqa: E402
from src.ingestion.dotsocr_client import check_vllm_health                       # noqa: E402
from src.ingestion.ingestion_pipeline import ingest_from_seaweedfs                # noqa: E402
from src.ingestion.embedding.embedder import MxbaiEmbedder                       # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill")

# Documents already ingested via the new pipeline are ocr_status='completed'.
# Without --force, we skip those to avoid re-billing OCR compute.
_SKIP_STATUSES = {"completed"}

# Seconds to wait between batches — lets the DB breathe and avoids hammering vLLM.
_INTER_BATCH_SLEEP = 2


def _build_pool():
    import psycopg2
    from psycopg2 import pool as pg_pool

    return pg_pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=4,
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _run_migrations(pool):
    import psycopg2.extras
    conn = pool.getconn()
    try:
        run_all_migrations(conn)
    finally:
        pool.putconn(conn)


def _process_batch(
    batch: list,
    db: RBACManager,
    embedder: MxbaiEmbedder,
    dry_run: bool,
    force: bool,
) -> tuple:
    """
    Process one batch of document rows.
    Returns (n_success, n_skipped, n_error, list_of_failed_names).
    """
    n_success = n_skipped = n_error = 0
    failed = []

    for doc in batch:
        doc_id    = doc["id"]
        file_name = doc["file_name"]
        file_path = doc["file_path"]
        dept_id   = doc["department_id"]
        status    = doc.get("ocr_status") or "pending"

        # Skip docs that already completed ingestion (unless --force)
        if not force and status in _SKIP_STATUSES:
            logger.debug("  SKIP  %-50s  (status=%s)", file_name, status)
            n_skipped += 1
            continue

        if not file_path:
            logger.warning("  SKIP  %-50s  (no file_path — cannot download)", file_name)
            n_skipped += 1
            continue

        if dry_run:
            logger.info("  [DRY]  Would process: %s  (status=%s)", file_name, status)
            n_success += 1
            continue

        logger.info("  START  %-50s  (status=%s)", file_name, status)
        t0 = time.monotonic()
        try:
            ingest_from_seaweedfs(
                file_path=file_path,
                file_name=file_name,
                dept_id=dept_id,
                db=db,
                embedder=embedder,
            )
            elapsed = time.monotonic() - t0
            logger.info("  OK     %-50s  (%.1fs)", file_name, elapsed)
            n_success += 1
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error("  FAIL   %-50s  (%.1fs) — %s", file_name, elapsed, exc)
            n_error += 1
            failed.append(file_name)

    return n_success, n_skipped, n_error, failed


def main():
    parser = argparse.ArgumentParser(description="Phase 3 backfill: re-ingest all documents")
    parser.add_argument("--batch-size", type=int,  default=10,
                        help="Documents per batch (default: 10)")
    parser.add_argument("--max-docs",   type=int,  default=0,
                        help="Stop after processing this many docs (0 = no limit)")
    parser.add_argument("--offset",     type=int,  default=0,
                        help="Start from this row offset in the documents table")
    parser.add_argument("--force",      action="store_true",
                        help="Re-process even already-completed documents")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print what would be processed but make no changes")
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Virchow Phase 3 Backfill")
    logger.info("  batch_size = %d", args.batch_size)
    logger.info("  max_docs   = %s", args.max_docs or "unlimited")
    logger.info("  offset     = %d", args.offset)
    logger.info("  force      = %s", args.force)
    logger.info("  dry_run    = %s", args.dry_run)
    logger.info("=" * 70)

    # ── Infrastructure setup ───────────────────────────────────────────────────
    try:
        pool = _build_pool()
    except Exception as e:
        logger.error("Cannot connect to PostgreSQL: %s", e)
        sys.exit(1)

    db = RBACManager(pool)

    # Run migrations so the schema is up-to-date before touching data
    logger.info("Running schema migrations…")
    try:
        _run_migrations(pool)
        logger.info("Migrations OK.")
    except Exception as e:
        logger.error("Migration failed: %s", e)
        sys.exit(1)

    # Verify DotsOCR vLLM is alive before starting (avoids burning through
    # the document list only to fail on every OCR call)
    if not args.dry_run:
        logger.info("Checking DotsOCR vLLM health at %s …", cfg.dotsocr_vllm_url)
        try:
            check_vllm_health(timeout=30)
            logger.info("DotsOCR vLLM is healthy.")
        except Exception as e:
            logger.error("DotsOCR vLLM is not reachable: %s", e)
            logger.error("Start the vLLM server and retry, or use --dry-run to test without OCR.")
            sys.exit(1)

    # Load embedder once — avoids reloading the model per document
    logger.info("Loading embedding model…")
    embedder = MxbaiEmbedder()
    logger.info("Embedder ready.")

    # ── Count and paginate ─────────────────────────────────────────────────────
    total_in_db = db.get_document_count()
    logger.info("Total documents in DB: %d", total_in_db)

    total_success = total_skipped = total_error = 0
    all_failed: list = []
    offset = args.offset
    docs_seen = 0

    while True:
        batch = db.get_documents_for_backfill(limit=args.batch_size, offset=offset)
        if not batch:
            break

        logger.info("── Batch offset=%d  size=%d ──", offset, len(batch))

        # Respect --max-docs cap
        if args.max_docs > 0:
            remaining = args.max_docs - docs_seen
            if remaining <= 0:
                logger.info("Reached --max-docs limit (%d). Stopping.", args.max_docs)
                break
            batch = batch[:remaining]

        n_ok, n_skip, n_err, failed = _process_batch(
            batch, db, embedder, dry_run=args.dry_run, force=args.force
        )
        total_success += n_ok
        total_skipped += n_skip
        total_error   += n_err
        all_failed.extend(failed)
        docs_seen += len(batch)
        offset += len(batch)

        # Sleep between batches so we don't saturate the GPU / DB connection pool
        if len(batch) == args.batch_size and not args.dry_run:
            time.sleep(_INTER_BATCH_SLEEP)

    # ── Final report ───────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("Backfill complete.")
    logger.info("  Processed : %d", docs_seen)
    logger.info("  Success   : %d", total_success)
    logger.info("  Skipped   : %d  (already completed; use --force to re-run)", total_skipped)
    logger.info("  Errors    : %d", total_error)
    if all_failed:
        logger.info("  Failed documents:")
        for name in all_failed:
            logger.info("    - %s", name)
    logger.info("=" * 70)

    sys.exit(1 if total_error > 0 else 0)


if __name__ == "__main__":
    main()
