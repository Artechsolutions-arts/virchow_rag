"""
Backfill: upload all completed/processing PDFs to SeaweedFS via Filer HTTP API.
Uses /buckets/rag-docs/raw/{filename} path on the filer.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import aiohttp
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("backfill")

# ── Config ────────────────────────────────────────────────────────────────────
PG_DSN      = "host=192.168.10.10 port=5433 dbname=virchow_dev user=postgres password=Eppl$456!"
FILER_URL   = "http://192.168.10.10:8889"
FILER_PATH  = "/buckets/rag-docs/raw"   # destination dir on filer
UPLOADS_DIR = Path("/Users/macai/Desktop/virchow_rag/ingest/uploads")
CONCURRENCY = 8


def resolve_local_path(file_path: str, file_name: str) -> Path | None:
    p = Path(file_path)
    if str(p).startswith("/app/uploads/"):
        p = UPLOADS_DIR / p.name
    if p.exists():
        return p
    candidates = list(UPLOADS_DIR.glob(f"*_{file_name}"))
    if candidates:
        return candidates[0]
    direct = UPLOADS_DIR / file_name
    if direct.exists():
        return direct
    return None


async def get_existing_filenames(session: aiohttp.ClientSession) -> set[str]:
    """List all filenames already in /buckets/rag-docs/raw/ via filer JSON API."""
    existing = set()
    last = ""
    while True:
        url = f"{FILER_URL}{FILER_PATH}/?limit=100000"
        if last:
            url += f"&lastFileName={last}"
        async with session.get(url, headers={"Accept": "application/json"}) as resp:
            data = await resp.json()
        entries = data.get("Entries") or []
        for e in entries:
            existing.add(Path(e["FullPath"]).name)
        if not data.get("ShouldDisplayLoadMore") or not entries:
            break
        last = entries[-1]["FullPath"].split("/")[-1]
    log.info("SeaweedFS already has %d files in %s", len(existing), FILER_PATH)
    return existing


async def upload_one(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                     local_path: Path, file_name: str, idx: int, total: int) -> bool:
    async with sem:
        url = f"{FILER_URL}{FILER_PATH}/{file_name}"
        try:
            with open(local_path, "rb") as fh:
                data = fh.read()
            async with session.put(
                url, data=data,
                headers={"Content-Type": "application/pdf"},
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status in (200, 201):
                    if idx % 200 == 0 or idx <= 3:
                        log.info("[%d/%d] OK  %s", idx, total, file_name)
                    return True
                else:
                    body = await resp.text()
                    log.warning("[%d/%d] HTTP %d for %s: %s", idx, total, resp.status, file_name, body[:100])
                    return False
        except Exception as e:
            log.warning("[%d/%d] ERROR %s: %s", idx, total, file_name, e)
            return False


async def main():
    # 1. Fetch docs from DB
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT file_name, file_path
        FROM documents
        WHERE embed_status IN ('completed', 'processing')
        ORDER BY file_name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    log.info("DB: %d completed/processing docs", len(rows))

    connector = aiohttp.TCPConnector(limit=CONCURRENCY + 4)
    async with aiohttp.ClientSession(connector=connector) as session:

        # 2. Get existing filenames from filer
        existing = await get_existing_filenames(session)

        # 3. Build upload queue
        to_upload = []
        missing_local = []
        already_there = 0

        for file_name, file_path in rows:
            if file_name in existing:
                already_there += 1
                continue
            local = resolve_local_path(file_path, file_name)
            if local is None:
                missing_local.append(file_name)
                continue
            to_upload.append((local, file_name))

        log.info("Already in SeaweedFS : %d", already_there)
        log.info("Local file not found : %d", len(missing_local))
        log.info("To upload            : %d", len(to_upload))

        if missing_local:
            log.warning("Missing local (first 10): %s", missing_local[:10])

        if not to_upload:
            log.info("Nothing to upload. Done.")
            return

        # 4. Upload concurrently
        sem = asyncio.Semaphore(CONCURRENCY)
        total = len(to_upload)
        tasks = [
            upload_one(session, sem, local, fname, i + 1, total)
            for i, (local, fname) in enumerate(to_upload)
        ]
        results = await asyncio.gather(*tasks)

    ok   = sum(results)
    fail = total - ok
    log.info("=== DONE === Uploaded: %d  Failed: %d  Missing-local: %d  Already-had: %d",
             ok, fail, len(missing_local), already_there)


if __name__ == "__main__":
    asyncio.run(main())
