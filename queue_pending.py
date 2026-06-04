#!/usr/bin/env python3
"""
One-shot: publish all 'pending' documents in the DB to RabbitMQ.
Run this whenever new files are added to the DB outside of the normal
upload API flow (e.g. bulk inserts).

Usage:
    cd /Users/macai/Desktop/virchow_rag
    /Users/macai/Desktop/virchow_rag/ingest/venv_native/bin/python3 queue_pending.py
"""
import sys
import pathlib
import uuid
import time

# Make ingest package importable
sys.path.insert(0, str(pathlib.Path(__file__).parent / "ingest"))

from src.database.rabbitmq_broker import publish_job
from src.models.schemas import JobPayload
import psycopg2
import psycopg2.extras

PG = dict(host="192.168.10.10", port=5433, dbname="virchow_dev",
          user="postgres", password="Eppl$456!")

UPLOAD_DIR = pathlib.Path("/Users/macai/Desktop/virchow_rag/ingest/uploads")

def run():
    conn = psycopg2.connect(**PG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT id::TEXT       AS doc_id,
               file_name,
               file_path,
               department_id::TEXT             AS dept_id,
               uploaded_by::TEXT               AS user_id,
               source_user_upload_id::TEXT      AS user_upload_id,
               source_admin_upload_id::TEXT     AS admin_upload_id
        FROM   documents
        WHERE  embed_status = 'pending'
        ORDER  BY created_at ASC
    """)
    pending = cur.fetchall()
    conn.close()

    print(f"Found {len(pending)} pending documents — publishing to RabbitMQ...")
    queued = skipped = 0

    for doc in pending:
        fpath = pathlib.Path(doc["file_path"])

        # Remap Docker-container paths to native uploads directory
        if not fpath.exists() and "/app/uploads/" in str(fpath):
            fpath = UPLOAD_DIR / fpath.name
        if not fpath.exists():
            # Try uploads dir by filename directly
            fpath = UPLOAD_DIR / pathlib.Path(doc["file_path"]).name
        if not fpath.exists():
            print(f"  SKIP (file missing): {doc['file_name']}")
            skipped += 1
            continue

        upload_type = "admin" if doc["admin_upload_id"] else "user"
        upload_id   = doc["admin_upload_id"] or doc["user_upload_id"]

        job = JobPayload(
            session_id   = str(uuid.uuid4()),
            file_id      = str(uuid.uuid4()),
            filename     = doc["file_name"],
            file_path    = str(fpath),
            file_size_kb = fpath.stat().st_size / 1024,
            user_id      = doc["user_id"],
            dept_id      = doc["dept_id"],
            upload_type  = upload_type,
            upload_id    = upload_id,
        )
        try:
            publish_job(job)
            queued += 1
            if queued % 100 == 0:
                print(f"  Queued {queued}/{len(pending)}...")
        except Exception as e:
            print(f"  ERROR queuing {doc['file_name']}: {e}")
            skipped += 1

    print(f"\nDone — queued={queued}  skipped={skipped}")

if __name__ == "__main__":
    run()
