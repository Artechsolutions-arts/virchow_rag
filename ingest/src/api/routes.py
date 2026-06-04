"""
Ingestion API Routes
====================
Single-purpose API for the on-prem RAG ingestion pipeline.

Endpoints
---------
POST /ingest                          Upload PDFs → OCR → Chunk → Embed → pgvector + SeaweedFS
GET  /ingest/progress/{session_id}    SSE stream of per-file ingestion progress
GET  /health                          Service health (postgres, redis, rabbitmq, seaweedfs)
GET  /storage/jobs/{job_id}/files     List SeaweedFS objects for a job
DELETE /storage/jobs/{job_id}/files   Remove SeaweedFS artefacts for a job
"""

import json
import time
import uuid
import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import (
    FALLBACK_DEPT_ID, FALLBACK_USER_ID,
    MQ_QUEUE_DEAD, MQ_QUEUE_LARGE, MQ_QUEUE_NORMAL, MQ_QUEUE_PRIORITY,
    UPLOAD_DIR, cfg,
)
from src.models.schemas import BatchSession, FileProgress, JobPayload

logger = logging.getLogger(__name__)


def _safe_basename(name: str) -> str:
    """Strip any directory component from an untrusted filename. The
    browser shouldn't be able to write outside ``UPLOAD_DIR`` by
    sending ``../../evil.pdf`` — we collapse the path to its basename
    and reject empty results."""
    base = os.path.basename(name or "").replace("\\", "/").split("/")[-1]
    if not base or base in {".", ".."}:
        raise HTTPException(400, "invalid filename")
    return base


def create_router(rsm, ids, pipeline, mq_conn):
    router = APIRouter()

    # ── Health Check ──────────────────────────────────────────────────────────

    @router.get("/health")
    async def health():
        """
        Returns the live status of all infrastructure components.
        Your query system can poll this before sending ingestion requests.
        """
        seaweedfs_ok = False
        if pipeline.storage:
            try:
                seaweedfs_ok = await pipeline.storage.health() is not None
            except Exception:
                seaweedfs_ok = False

        status = {
            "status": "ok",
            "postgres": True,   # If we reached this point postgres is up
            "redis":    bool(rsm and rsm.ping()),
            "rabbitmq": bool(mq_conn and mq_conn.is_open),
            "seaweedfs": seaweedfs_ok,
        }
        # Overall ok only if core services are up
        if not status["redis"] or not status["rabbitmq"]:
            status["status"] = "degraded"
        return JSONResponse(status)

    # ── Ingestion ─────────────────────────────────────────────────────────────

    @router.post("/ingest")
    async def ingest(
        files: List[UploadFile] = File(...),
        dept_id: Optional[str] = Form(None),
        user_id: Optional[str] = Form(None),
    ):
        """
        Upload one or more PDF files for ingestion.

        The pipeline runs asynchronously:
          1. DotsOCR  — VLM layout detection + text extraction
          2. Chunking — Markdown-aware, token-limited chunks
          3. Embedding — mxbai-embed-large-v1 (1024-dim)
          4. Storage  — chunks + vectors → PostgreSQL/pgvector
                        raw PDF + markdown → SeaweedFS

        Parameters
        ----------
        files    : PDF file(s) to ingest
        dept_id  : Department UUID to scope vectors under (optional).
                   Defaults to the system default department.
                   Your query system should filter by the same dept_id.
        user_id  : Uploader UUID (optional). Defaults to system user.

        Returns
        -------
        {
          "session_id": "uuid",          -- poll /ingest/progress/{session_id}
          "dept_id": "uuid",             -- use this in your query system for vector filtering
          "files": [
            {"file_id": "uuid", "filename": "doc.pdf", "size_kb": 123.4}
          ]
        }
        """
        if not rsm or not rsm.ping():
            raise HTTPException(503, "Redis is offline — cannot accept ingestion jobs")

        # Resolve dept/user — fall back to seeded system defaults from config.
        resolved_dept = dept_id or ids.get("dept_default") or FALLBACK_DEPT_ID
        resolved_user = user_id or ids.get("user_default") or FALLBACK_USER_ID

        # Route to admin_uploads if the uploader is an admin/super-admin.
        is_admin = pipeline.rbac.get_user_is_admin(str(resolved_user))
        effective_upload_type = "admin" if is_admin else "user"

        session_id = str(uuid.uuid4())
        session = BatchSession(
            session_id=session_id,
            total=len(files),
            user_id=str(resolved_user),
            dept_id=str(resolved_dept),
            upload_type=effective_upload_type,
        )
        rsm.create_session(session)

        ingested_files = []

        for f in files:
            safe_name = _safe_basename(f.filename)
            if not safe_name.lower().endswith(".pdf"):
                raise HTTPException(400, f"'{safe_name}' is not a PDF")
            # Assign this now so downstream refs (logs, DB rows, job payload)
            # all use the sanitized name rather than the user-supplied one.
            f_filename_safe = safe_name

            contents = await f.read()

            # Magic byte check — reject non-PDF content regardless of filename
            if not contents.startswith(b"%PDF-"):
                raise HTTPException(
                    400,
                    f"'{safe_name}' does not appear to be a valid PDF file",
                )

            file_id = str(uuid.uuid4())
            fpath = UPLOAD_DIR / f"{file_id}_{safe_name}"
            fpath.write_bytes(contents)

            # Register upload in the correct table based on user role.
            upload_id = None
            try:
                if is_admin:
                    upload_id = pipeline.rbac.register_admin_upload(
                        admin_user_id=str(resolved_user),
                        dept_id=str(resolved_dept),
                        file_name=f_filename_safe,
                        file_path=str(fpath),
                        file_size_bytes=len(contents),
                    )
                else:
                    upload_id = pipeline.rbac.register_user_upload(
                        user_id=str(resolved_user),
                        dept_id=str(resolved_dept),
                        file_name=f_filename_safe,
                        file_path=str(fpath),
                        file_size_bytes=len(contents),
                        chat_id=None,
                        upload_scope="dept",
                    )
            except Exception as e:
                logger.warning(
                    "Upload registration failed — file=%s file_id=%s: %s",
                    f_filename_safe, file_id, e,
                )

            # Create a pending document row immediately so the file appears in
            # the UI list while the worker is processing it, and so the pipeline
            # worker has a valid FK parent for chunk/embedding inserts.
            try:
                pipeline.rbac.create_document_pending(
                    file_name=f_filename_safe,
                    file_path=str(fpath),
                    dept_id=str(resolved_dept),
                    uploaded_by=str(resolved_user),
                    source_user_upload_id=upload_id if not is_admin else None,
                    source_admin_upload_id=upload_id if is_admin else None,
                    file_size=len(contents),
                )
            except Exception as e:
                logger.warning("Pending doc record failed — file=%s: %s", f_filename_safe, e)

            fp = FileProgress(
                file_id=file_id,
                session_id=session_id,
                filename=f_filename_safe,
                size_kb=len(contents) / 1024,
                started_at=time.time(),
            )
            rsm.register_file(session_id, fp)

            job = JobPayload(
                session_id=session_id,
                file_id=file_id,
                filename=f_filename_safe,
                file_path=str(fpath),
                file_size_kb=len(contents) / 1024,
                user_id=str(resolved_user),
                dept_id=str(resolved_dept),
                upload_type=effective_upload_type,
                upload_id=upload_id,
            )

            from src.database.rabbitmq_broker import publish_job
            publish_job(job)

            ingested_files.append({
                "file_id":  file_id,
                "filename": f_filename_safe,
                "size_kb":  round(len(contents) / 1024, 1),
            })

            logger.info(
                "Job queued — file=%s file_id=%s session=%s dept=%s",
                f_filename_safe, file_id, session_id, resolved_dept,
            )

        return JSONResponse({
            "session_id": session_id,
            "dept_id":    str(resolved_dept),
            "files":      ingested_files,
        })

    # ── Progress (SSE) ────────────────────────────────────────────────────────

    @router.get("/ingest/progress/{session_id}")
    async def ingest_progress(session_id: str):
        """
        Server-Sent Events stream for real-time ingestion progress.

        Each event has the shape:
          data: {"type": "file_progress", "data": {
            "file_id": "uuid",
            "filename": "doc.pdf",
            "stage": "ocr" | "chunking" | "embedding" | "storing" | "done" | "error",
            "pct": 0-100,
            "chunks": 42,
            "doc_id": "uuid",    -- available once stored; use for PG queries
            "error": null
          }}

        The stream closes when all files in the session reach a terminal stage
        (done / error / skipped).
        """
        if not rsm or not rsm.ping():
            raise HTTPException(503, "Redis is offline")

        async def _event_stream():
            # Immediately emit current state for any already-progressed files
            summary = rsm.session_summary(session_id)
            if summary:
                for f in summary.get("files", []):
                    yield f"data: {json.dumps({'type': 'file_progress', 'data': f})}\n\n"

            # stop_event is set when the client disconnects, signalling the
            # Redis subscription thread to exit and release its connection.
            stop_event = threading.Event()
            q    = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def _subscribe():
                try:
                    for event in rsm.subscribe_session(session_id,
                                                       stop_event=stop_event):
                        loop.call_soon_threadsafe(q.put_nowait, event)
                except Exception as e:
                    logger.warning("SSE subscribe error: %s", e)
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)

            thread = threading.Thread(target=_subscribe, daemon=True)
            thread.start()

            try:
                while True:
                    event = await q.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                # Client disconnected or session complete — release Redis connection
                stop_event.set()

        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    # ── SeaweedFS Storage Routes ───────────────────────────────────────────────

    @router.get("/storage/health")
    async def storage_health():
        if not pipeline.storage:
            return JSONResponse({"seaweedfs": "not configured"})
        return await pipeline.storage.health()

    @router.get("/storage/jobs/{job_id}/files")
    async def list_job_files(job_id: str):
        """List all SeaweedFS objects for a given job (raw PDF, extracted markdown)."""
        if not pipeline.storage:
            raise HTTPException(501, "Object storage not configured")
        return await pipeline.storage.list_job_files(job_id)

    @router.delete("/storage/jobs/{job_id}/files")
    async def delete_job_files(job_id: str):
        """Remove all SeaweedFS artefacts for a completed or failed job."""
        if not pipeline.storage:
            raise HTTPException(501, "Object storage not configured")
        deleted = await pipeline.storage.delete_job_artefacts(job_id)
        return {"deleted_count": deleted}

    @router.get("/storage/jobs/{job_id}/pdf-url")
    async def get_pdf_url(job_id: str, filename: str):
        """Return the SeaweedFS filer URL for the raw PDF of a job."""
        if not pipeline.storage:
            raise HTTPException(501, "Object storage not configured")
        return {"url": pipeline.storage.pdf_url(job_id, filename)}

    # ── Metrics & Admin ───────────────────────────────────────────────────────

    @router.get("/metrics")
    async def metrics():
        """Lightweight JSON snapshot for operators/dashboards.
        Queue depths from RabbitMQ, postgres doc/chunk counts, and stage
        pipeline internals when available."""
        metrics: dict = {"queues": {}, "db": {}, "stage": {}}

        # RabbitMQ queue depths — passive queue_declare returns the count
        try:
            import pika
            if mq_conn and mq_conn.is_open:
                ch = mq_conn.channel()
                for q in (MQ_QUEUE_PRIORITY, MQ_QUEUE_NORMAL,
                          MQ_QUEUE_LARGE, MQ_QUEUE_DEAD):
                    try:
                        res = ch.queue_declare(q, passive=True)
                        metrics["queues"][q] = res.method.message_count
                    except Exception as e:
                        metrics["queues"][q] = f"error: {e}"
                ch.close()
        except Exception as e:
            metrics["queues"]["_error"] = str(e)

        # Postgres counts — cheap aggregate over indexed tables
        try:
            if pipeline.rbac:
                conn = pipeline.rbac._get_conn()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT "
                                "(SELECT COUNT(*) FROM documents),"
                                "(SELECT COUNT(*) FROM chunks),"
                                "(SELECT COUNT(*) FROM embeddings),"
                                "(SELECT COUNT(*) FROM document_line_items),"
                                "(SELECT COUNT(*) FROM document_references)")
                    docs, chunks, embs, li, refs = cur.fetchone()
                    metrics["db"] = {
                        "documents":  docs,
                        "chunks":     chunks,
                        "embeddings": embs,
                        "line_items": li,
                        "references": refs,
                    }
                    cur.close()
                finally:
                    pipeline.rbac._put_conn(conn)
        except Exception as e:
            metrics["db"]["_error"] = str(e)

        # Stage pipeline internals — best-effort; private attrs, fine for ops
        try:
            sp = getattr(pipeline, "stage_pipeline", None) or getattr(
                pipeline, "_pipeline", None)
            if sp is not None:
                for q_name in ("_page_q", "_markdown_q", "_assembled_q",
                               "_chunk_q", "_store_q"):
                    q = getattr(sp, q_name, None)
                    if q is not None and hasattr(q, "qsize"):
                        metrics["stage"][q_name.lstrip("_")] = q.qsize()
        except Exception as e:
            metrics["stage"]["_error"] = str(e)

        return JSONResponse(metrics)

    @router.get("/admin/dlq")
    async def dlq_inspect(limit: int = 20):
        """Peek at the first ``limit`` messages on the dead-letter queue
        without consuming them. Handy for triaging failures."""
        if not (mq_conn and mq_conn.is_open):
            raise HTTPException(503, "RabbitMQ offline")
        messages = []
        ch = mq_conn.channel()
        try:
            for _ in range(max(1, min(limit, 100))):
                method, props, body = ch.basic_get(MQ_QUEUE_DEAD,
                                                    auto_ack=False)
                if method is None:
                    break
                try:
                    payload = json.loads(body.decode())
                except Exception:
                    payload = {"raw_body_len": len(body)}
                messages.append({
                    "delivery_tag": method.delivery_tag,
                    "headers":      dict(props.headers or {}),
                    "payload":      payload,
                })
                ch.basic_nack(method.delivery_tag, requeue=True)
        finally:
            ch.close()
        return {"count": len(messages), "messages": messages}

    @router.post("/admin/dlq/purge")
    async def dlq_purge():
        """Discard every message on the dead-letter queue. Irreversible —
        only call after triage."""
        if not (mq_conn and mq_conn.is_open):
            raise HTTPException(503, "RabbitMQ offline")
        ch = mq_conn.channel()
        try:
            res = ch.queue_purge(MQ_QUEUE_DEAD)
            purged = getattr(res.method, "message_count", None)
        finally:
            ch.close()
        return {"purged": purged}

    # ── Document List ─────────────────────────────────────────────────────────

    _STATUS_MAP = {
        "pending":    "IN PROGRESS",
        "processing": "IN PROGRESS",
        "completed":  "UPLOADED",
        "failed":     "FAILED TO UPLOAD",
    }

    @router.get("/documents")
    async def list_documents(
        dept_id: Optional[str] = None,
        limit: int = 10000,
    ):
        """List ingested documents with frontend-compatible shape.

        Returns id, name, path, size, upload timestamp, and a human-readable
        status string that the admin UI understands:
          - IN PROGRESS  (pending / processing)
          - UPLOADED     (completed)
          - FAILED TO UPLOAD (failed)
        """
        rows = pipeline.rbac.list_documents_for_dept(dept_id=dept_id, limit=limit)
        result = []
        for r in rows:
            amt = r.get("total_amount")
            result.append({
                "id":           r["id"],
                "name":         r["file_name"],
                "path":         r["file_name"],
                "type":         "application/pdf",
                "size":         r.get("file_size") or 0,
                "uploaded_by":  r.get("uploaded_by_email") or "",
                "uploaded_at":  r.get("uploaded_at") or "",
                "status":       _STATUS_MAP.get(r.get("embed_status", ""), "IN PROGRESS"),
                "version":      "1",
                "party_name":   r.get("party_name") or "",
                "party_gstin":  r.get("party_gstin") or "",
                "doc_type":     r.get("doc_type") or "",
                "doc_month":    r.get("doc_month") or "",
                "doc_unit":     r.get("doc_unit") or "",
                "doc_date":     str(r["doc_date"]) if r.get("doc_date") else "",
                "total_amount": float(amt) if amt is not None else None,
                "tax_amount":   float(r["tax_amount"]) if r.get("tax_amount") is not None else None,
                "net_amount":   float(r["net_amount"]) if r.get("net_amount") is not None else None,
            })
        return JSONResponse(result)

    # ── Single-file upload (frontend-compatible alias for /ingest) ────────────

    @router.post("/documents/upload")
    async def upload_document(
        file: UploadFile = File(...),
        dept_id: Optional[str] = Form(None),
        user_id: Optional[str] = Form(None),
    ):
        """Single-file upload endpoint matching the frontend's expected API.

        Identical pipeline to ``POST /ingest`` but accepts one file at a time
        via ``file=`` (not ``files=``) and returns 202 with the file_name so
        the admin UI can track progress against the document list.
        """
        if not rsm or not rsm.ping():
            raise HTTPException(503, "Redis is offline — cannot accept ingestion jobs")

        safe_name = _safe_basename(file.filename)
        if not safe_name.lower().endswith(".pdf"):
            raise HTTPException(400, f"'{safe_name}' is not a PDF")

        contents = await file.read()

        if not contents.startswith(b"%PDF-"):
            raise HTTPException(400, f"'{safe_name}' does not appear to be a valid PDF file")

        if len(contents) > 500 * 1024 * 1024:
            raise HTTPException(413, "File exceeds 500 MB limit")

        resolved_dept = dept_id or ids.get("dept_default") or FALLBACK_DEPT_ID
        resolved_user = user_id or ids.get("user_default") or FALLBACK_USER_ID
        session_id = str(uuid.uuid4())
        file_id    = str(uuid.uuid4())

        # Route to admin_uploads if the uploader is an admin/super-admin.
        is_admin = pipeline.rbac.get_user_is_admin(str(resolved_user))
        effective_upload_type = "admin" if is_admin else "user"

        from src.models.schemas import BatchSession, FileProgress, JobPayload
        session = BatchSession(
            session_id=session_id,
            total=1,
            user_id=str(resolved_user),
            dept_id=str(resolved_dept),
            upload_type=effective_upload_type,
        )
        rsm.create_session(session)

        fpath = UPLOAD_DIR / f"{file_id}_{safe_name}"
        fpath.write_bytes(contents)

        upload_id = None
        try:
            if is_admin:
                upload_id = pipeline.rbac.register_admin_upload(
                    admin_user_id=str(resolved_user),
                    dept_id=str(resolved_dept),
                    file_name=safe_name,
                    file_path=str(fpath),
                    file_size_bytes=len(contents),
                )
            else:
                upload_id = pipeline.rbac.register_user_upload(
                    user_id=str(resolved_user),
                    dept_id=str(resolved_dept),
                    file_name=safe_name,
                    file_path=str(fpath),
                    file_size_bytes=len(contents),
                    chat_id=None,
                    upload_scope="dept",
                )
        except Exception as e:
            logger.warning("Upload registration failed — file=%s: %s", safe_name, e)

        # Create a pending documents record immediately so the file appears
        # in the UI list while the worker is processing it.
        try:
            pipeline.rbac.create_document_pending(
                file_name=safe_name,
                file_path=str(fpath),
                dept_id=str(resolved_dept),
                uploaded_by=str(resolved_user),
                source_user_upload_id=upload_id if not is_admin else None,
                source_admin_upload_id=upload_id if is_admin else None,
                file_size=len(contents),
            )
        except Exception as e:
            logger.warning("Pending doc record failed — file=%s: %s", safe_name, e)

        fp = FileProgress(
            file_id=file_id,
            session_id=session_id,
            filename=safe_name,
            size_kb=len(contents) / 1024,
            started_at=time.time(),
        )
        rsm.register_file(session_id, fp)

        job = JobPayload(
            session_id=session_id,
            file_id=file_id,
            filename=safe_name,
            file_path=str(fpath),
            file_size_kb=len(contents) / 1024,
            user_id=str(resolved_user),
            dept_id=str(resolved_dept),
            upload_type=effective_upload_type,
            upload_id=upload_id,
        )
        from src.database.rabbitmq_broker import publish_job
        publish_job(job)

        logger.info("Job queued — file=%s file_id=%s session=%s dept=%s",
                    safe_name, file_id, session_id, resolved_dept)

        return JSONResponse(
            {"ok": True, "file_name": safe_name, "session_id": session_id},
            status_code=202,
        )

    # ── Document restart (re-queue a failed / completed document) ────────────

    @router.post("/documents/restart")
    async def restart_document(
        upload_ids: str = Form(...),
        dept_id: Optional[str] = Form(None),
    ):
        """Reset and re-ingest a document by its document UUID.

        Clears all derived data (chunks, embeddings) and publishes a new
        job to the ingestion queue using the original file on disk.
        """
        if not rsm or not rsm.ping():
            raise HTTPException(503, "Redis is offline — cannot accept ingestion jobs")

        doc_id = upload_ids.strip()
        doc = pipeline.rbac.get_document_by_id(doc_id, dept_id=dept_id)
        if not doc:
            raise HTTPException(404, "Document not found")

        file_path = doc.get("file_path") or ""
        if not file_path or not Path(file_path).exists():
            raise HTTPException(422, "Could not locate original file on disk")

        pipeline.rbac.reset_document_status(doc_id)

        resolved_dept = dept_id or str(doc.get("department_id") or FALLBACK_DEPT_ID)
        resolved_user = str(doc.get("uploaded_by") or FALLBACK_USER_ID)
        session_id    = str(uuid.uuid4())
        file_id       = str(uuid.uuid4())
        safe_name     = doc["file_name"]
        file_size_kb  = Path(file_path).stat().st_size / 1024

        from src.models.schemas import BatchSession, FileProgress, JobPayload
        session = BatchSession(
            session_id=session_id,
            total=1,
            user_id=resolved_user,
            dept_id=resolved_dept,
            upload_type="user",
        )
        rsm.create_session(session)

        fp = FileProgress(
            file_id=file_id,
            session_id=session_id,
            filename=safe_name,
            size_kb=file_size_kb,
            started_at=time.time(),
        )
        rsm.register_file(session_id, fp)

        job = JobPayload(
            session_id=session_id,
            file_id=file_id,
            filename=safe_name,
            file_path=file_path,
            file_size_kb=file_size_kb,
            user_id=resolved_user,
            dept_id=resolved_dept,
            upload_type="user",
            upload_id=None,
        )
        from src.database.rabbitmq_broker import publish_job
        publish_job(job)

        logger.info("Restart queued — doc_id=%s file=%s session=%s",
                    doc_id, safe_name, session_id)

        return JSONResponse(
            {"ok": True, "file_name": safe_name, "session_id": session_id},
            status_code=202,
        )

    return router
