#!/usr/bin/env python3
"""
backfill_missing_fields.py
==========================

Fills the gaps the original ingest pipeline left in the ``documents`` table.
Each pass is idempotent and reports counts; safe to re-run.

Passes
------
  canonical : party_name_canonical = upper(trim(party_name))           [SQL]
  filename  : fiscal_year, serial_no, doc_unit from filename pattern   [SQL]
  doc_month : derive month name from doc_date where month is NULL      [SQL]
  seaweed   : file_size (HEAD), content_hash + page_count (GET tiny    [HTTP]
              subset), extracted_text from processed/
  quality   : ocr_quality from chunks.quality_score                    [SQL]

Usage
-----
    python3 backfill_missing_fields.py                # all passes
    python3 backfill_missing_fields.py --only seaweed # one pass
    python3 backfill_missing_fields.py --limit 100    # smoke test
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import psycopg2
import psycopg2.extras
import requests

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("backfill")

PG_DSN = dict(
    host=os.getenv("PG_HOST", "192.168.10.10"),
    port=int(os.getenv("PG_PORT", "5433")),
    dbname=os.getenv("PG_DATABASE", "virchow_dev"),
    user=os.getenv("PG_USER", "postgres"),
    password=os.getenv("PG_PASSWORD", "Eppl$456!"),
)

SEAWEED_FILER = os.getenv("SEAWEEDFS_FILER_URL", "http://192.168.10.10:889").rstrip("/")
SEAWEED_BUCKET = os.getenv("SEAWEEDFS_BUCKET", "rag-docs")

MONTH_FROM_NUM = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def raw_url(file_name: str) -> str:
    return f"{SEAWEED_FILER}/buckets/{SEAWEED_BUCKET}/raw/{quote(file_name)}"


def processed_url(file_name: str) -> str:
    stem = Path(file_name).stem
    return f"{SEAWEED_FILER}/buckets/{SEAWEED_BUCKET}/processed/{quote(stem)}.json"


# ─── Pass: canonical ─────────────────────────────────────────────────────────

def pass_canonical(conn) -> int:
    """party_name_canonical = upper(trim(party_name)) where canonical is null."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE documents "
            "SET party_name_canonical = upper(trim(party_name)) "
            "WHERE party_name IS NOT NULL "
            "  AND party_name_canonical IS NULL"
        )
        n = cur.rowcount
    conn.commit()
    log.info("[canonical] updated=%d", n)
    return n


# ─── Pass: doc_month from doc_date ───────────────────────────────────────────

def pass_doc_month_from_date(conn) -> int:
    """Derive doc_month name from doc_date for rows that have a date but no month."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET doc_month = to_char(doc_date, 'FMMonth') "
            "WHERE doc_date IS NOT NULL AND doc_month IS NULL"
        )
        n = cur.rowcount
    conn.commit()
    log.info("[doc_month] updated=%d", n)
    return n


# ─── Pass: filename parsing ──────────────────────────────────────────────────

def pass_filename(conn, limit: Optional[int]) -> int:
    """Use the existing filename parser for fiscal_year, serial_no, doc_unit.
    Only updates columns that are currently NULL — never overwrites.
    Does NOT touch doc_type (the corpus shows the filename code is not a
    reliable predictor: PUR maps to PO, DC, Tax Invoice, ...)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "ingest"))
    from src.ingestion.metadata.filename_parser import parse_filename_metadata  # noqa

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, file_name FROM documents "
            "WHERE fiscal_year IS NULL OR serial_no IS NULL OR doc_unit IS NULL "
            "ORDER BY file_name"
            + (f" LIMIT {int(limit)}" if limit else "")
        )
        rows = cur.fetchall()

    updated = 0
    for doc_id, fname in rows:
        meta = parse_filename_metadata(fname) or {}
        fy = meta.get("fiscal_year")
        sn = meta.get("serial_no")
        du = meta.get("doc_unit")
        if not any([fy, sn, du]):
            continue
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET "
                "  fiscal_year = COALESCE(fiscal_year, %s), "
                "  serial_no   = COALESCE(serial_no, %s), "
                "  doc_unit    = COALESCE(doc_unit, %s) "
                "WHERE id = %s",
                (fy, sn, du, doc_id),
            )
            if cur.rowcount:
                updated += 1
        if updated % 500 == 0 and updated:
            conn.commit()
    conn.commit()
    log.info("[filename] scanned=%d updated=%d", len(rows), updated)
    return updated


# ─── Pass: seaweed (file_size + content_hash + page_count + extracted_text) ─

def _head_size(file_name: str) -> Optional[int]:
    try:
        r = requests.head(raw_url(file_name), timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return None
        cl = r.headers.get("content-length")
        return int(cl) if cl else None
    except Exception:
        return None


def _fetch_pdf_bytes(file_name: str) -> Optional[bytes]:
    try:
        r = requests.get(raw_url(file_name), timeout=60)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _fetch_extracted_text(file_name: str) -> Optional[str]:
    try:
        r = requests.get(processed_url(file_name), timeout=30)
        if r.status_code != 200:
            return None
        import json
        data = json.loads(r.content)
        return data.get("text") or data.get("extracted_text") or None
    except Exception:
        return None


def pass_seaweed(conn, limit: Optional[int], workers: int = 16) -> None:
    """Backfill file_size (cheap HEAD), content_hash + page_count (GET, small
    subset), and extracted_text (processed/ JSON)."""
    cur = conn.cursor()

    # ── file_size: HEAD, parallel ────────────────────────────────────────────
    cur.execute(
        "SELECT id, file_name FROM documents WHERE file_size = 0 ORDER BY file_name"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    rows = cur.fetchall()
    cur.close()
    log.info("[seaweed.file_size] %d candidates", len(rows))
    if rows:
        ok = miss = 0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_head_size, f): (i, f) for i, f in rows}
            batch = []
            for fut in as_completed(futs):
                doc_id, fname = futs[fut]
                size = fut.result()
                if size is not None:
                    batch.append((size, doc_id))
                    ok += 1
                else:
                    miss += 1
                if len(batch) >= 200:
                    cur = conn.cursor()
                    psycopg2.extras.execute_batch(
                        cur,
                        "UPDATE documents SET file_size = %s WHERE id = %s",
                        batch,
                    )
                    cur.close()
                    conn.commit()
                    batch.clear()
                    log.info("  size progress ok=%d miss=%d elapsed=%.1fs",
                             ok, miss, time.time() - t0)
            if batch:
                cur = conn.cursor()
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE documents SET file_size = %s WHERE id = %s",
                    batch,
                )
                cur.close()
                conn.commit()
        log.info("[seaweed.file_size] ok=%d miss=%d in %.1fs",
                 ok, miss, time.time() - t0)

    # ── content_hash + page_count: full GET on the (small) gap ───────────────
    cur = conn.cursor()
    cur.execute(
        "SELECT id, file_name FROM documents "
        "WHERE content_hash IS NULL OR page_count = 0 "
        "ORDER BY file_name"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    rows = cur.fetchall()
    cur.close()
    log.info("[seaweed.hash+pages] %d candidates", len(rows))
    if rows:
        try:
            import fitz  # pymupdf
        except ImportError:
            log.warning("pymupdf not installed — skipping page_count, will only fill hash")
            fitz = None
        for doc_id, fname in rows:
            data = _fetch_pdf_bytes(fname)
            if not data:
                continue
            content_hash = hashlib.sha256(data).hexdigest()
            page_count = None
            if fitz:
                try:
                    with fitz.open(stream=data, filetype="pdf") as d:
                        page_count = d.page_count
                except Exception:
                    page_count = None
            cur = conn.cursor()
            if page_count is not None:
                cur.execute(
                    "UPDATE documents SET "
                    "  content_hash = COALESCE(content_hash, %s), "
                    "  page_count   = CASE WHEN page_count = 0 THEN %s ELSE page_count END, "
                    "  file_size    = CASE WHEN file_size = 0 THEN %s ELSE file_size END "
                    "WHERE id = %s",
                    (content_hash, page_count, len(data), doc_id),
                )
            else:
                cur.execute(
                    "UPDATE documents SET "
                    "  content_hash = COALESCE(content_hash, %s), "
                    "  file_size    = CASE WHEN file_size = 0 THEN %s ELSE file_size END "
                    "WHERE id = %s",
                    (content_hash, len(data), doc_id),
                )
            cur.close()
            conn.commit()

    # ── extracted_text: fetch from processed/ ────────────────────────────────
    cur = conn.cursor()
    cur.execute(
        "SELECT id, file_name FROM documents "
        "WHERE extracted_text IS NULL OR extracted_text = '' "
        "ORDER BY file_name"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    rows = cur.fetchall()
    cur.close()
    log.info("[seaweed.extracted_text] %d candidates", len(rows))
    ok = 0
    chunk_fallback = 0
    for doc_id, fname in rows:
        text = _fetch_extracted_text(fname)
        if not text:
            # Fall back: stitch from chunks
            cur = conn.cursor()
            cur.execute(
                "SELECT chunk_text FROM chunks WHERE document_id = %s "
                "ORDER BY chunk_index", (str(doc_id),)
            )
            parts = [r[0] for r in cur.fetchall()]
            cur.close()
            if parts:
                text = "\n\n".join(parts)
                chunk_fallback += 1
        if text:
            cur = conn.cursor()
            cur.execute(
                "UPDATE documents SET extracted_text = %s WHERE id = %s "
                "AND (extracted_text IS NULL OR extracted_text = '')",
                (text, doc_id),
            )
            cur.close()
            conn.commit()
            ok += 1
    log.info("[seaweed.extracted_text] ok=%d (chunk-fallback=%d)",
             ok, chunk_fallback)


# ─── Pass: quality ───────────────────────────────────────────────────────────

def pass_quality(conn, limit: Optional[int]) -> None:
    """Delegate to the existing pass_quality in ingest/src/ingestion/backfill.py.
    Computes chunk-level quality_score then rolls up doc-level ocr_quality."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "ingest"))
    from src.database.postgres_db import RBACManager, get_pg_pool
    from src.ingestion.backfill import pass_quality as _quality

    pool = get_pg_pool(minconn=1, maxconn=2)
    rbac = RBACManager(pool)
    _quality(rbac, limit or 0)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["canonical", "doc_month", "filename",
                                       "seaweed", "quality"],
                    help="Run a single pass")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap rows per pass (0 = no limit)")
    ap.add_argument("--workers", type=int, default=16,
                    help="Parallel HTTP workers for seaweed pass")
    args = ap.parse_args()

    conn = psycopg2.connect(**PG_DSN)
    conn.autocommit = False

    passes = {
        "canonical":  lambda: pass_canonical(conn),
        "doc_month":  lambda: pass_doc_month_from_date(conn),
        "filename":   lambda: pass_filename(conn, args.limit),
        "seaweed":    lambda: pass_seaweed(conn, args.limit, args.workers),
        "quality":    lambda: pass_quality(conn, args.limit),
    }

    todo = [args.only] if args.only else list(passes.keys())
    for name in todo:
        log.info("=== pass: %s ===", name)
        passes[name]()

    conn.close()


if __name__ == "__main__":
    main()
