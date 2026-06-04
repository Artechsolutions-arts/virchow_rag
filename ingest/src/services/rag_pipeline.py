"""
RAG Pipeline  —  ingestion facade
==================================
Wraps SequentialPipeline.  Each worker processes one document fully before
picking up the next.  OCR and embedding are served by Ollama (no local models
loaded in the worker process).

Architecture
------------
  routes.py / PDFWorker
       │
       ▼
  RAGPipeline.submit(DocJob)
       │
       ▼
  SequentialPipeline._doc_q
       │
       ▼  (per-doc: fitz → Ollama OCR → chunk → Ollama embed → PostgreSQL)
  PostgreSQL + pgvector
"""

import logging
import os

from src.database.postgres_db import RBACManager
from src.ingestion.pipeline.datatypes import DocJob
from src.ingestion.pipeline.sequential_pipeline import SequentialPipeline

logger = logging.getLogger(__name__)


class RAGPipeline:
    """
    Public facade for the ingestion pipeline.

    Holds shared infrastructure references (DB pool, Redis, SeaweedFS) and
    owns a SequentialPipeline that contains all processing threads.
    """

    def __init__(self, conn, rsm, storage=None):
        self.conn    = conn
        self.rsm     = rsm
        self.storage = storage
        self.rbac    = RBACManager(conn)

        _api_only = os.getenv("RUN_TYPE", "worker") == "api"
        self._stage_pipeline = SequentialPipeline(
            rsm=rsm,
            rbac=self.rbac,
            storage=storage,
            api_only=_api_only,
        )
        self.stage_pipeline = self._stage_pipeline
        logger.info("RAGPipeline (sequential Ollama ingestion) initialised.")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start all worker threads."""
        self._stage_pipeline.start()
        logger.info("SequentialPipeline started")

    def stop(self, timeout: float = 60.0):
        """Graceful shutdown."""
        self._stage_pipeline.stop(timeout=timeout)
        logger.info("SequentialPipeline stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(self, doc_job: DocJob):
        """
        Submit a document into the stage pipeline.
        Returns immediately — processing is async through the stage queues.
        """
        self._stage_pipeline.submit(doc_job)

    def process_pdf(
        self,
        file_path:   str,
        filename:    str,
        user_id:     str,
        dept_id:     str,
        file_id:     str,
        session_id:  str,
        upload_type: str = "user",
        upload_id:   str = None,
        raw_bytes:   bytes = None,   # ignored — pipeline reads from disk
        **kwargs,
    ):
        """Convenience wrapper: creates a DocJob and submits it."""
        doc_job = DocJob(
            file_id=file_id,
            session_id=session_id,
            filename=filename,
            file_path=file_path,
            user_id=user_id,
            dept_id=dept_id,
            upload_id=upload_id,
            upload_type=upload_type,
        )
        self.submit(doc_job)
        logger.info("[Pipeline] Submitted '%s' to sequential pipeline", filename)
