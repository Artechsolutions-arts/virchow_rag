"""
Sequential Pipeline  —  DotsOCR + Ollama embedding
===================================================
Replaces the 8-stage parallel StagePipeline with a simple sequential flow.
Each document is processed fully before the worker picks up the next one.

Flow per document
-----------------
  read PDF bytes
      → SHA-256 dedup
      → fitz → PIL images (one per page)
      → DotsOCR (HuggingFace, MPS)  (OCR each page → markdown)
      → assemble pages
      → DocumentChunker              (markdown → chunk list)
      → Ollama qwen3-embedding:8b    (chunks → vectors)
      → PostgreSQL store             (chunks + embeddings)
      → Redis status update

Worker count is configurable; each worker loads DotsOCR once and processes
documents sequentially so there are never partial documents sitting in queues.
"""

import asyncio
import hashlib
import itertools
import logging
import os
import re
import threading
import time
from pathlib import Path
from queue import PriorityQueue, Empty
from typing import Optional

import requests

from src.config import cfg
from src.ingestion.chunking.chunker import DocumentChunker
from src.ingestion.pipeline.datatypes import DocJob
from src.ingestion.metadata.filename_parser import parse_filename_metadata
from src.ingestion.metadata.ocr_extractor import extract_from_ocr

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "qwen3-embedding:8b")

_WORKER_COUNT  = int(os.getenv("N_SEQ_WORKERS",  "4"))
_DOC_Q_MAXSIZE = int(os.getenv("SEQ_QUEUE_SIZE", "0"))  # 0 = unlimited

# Ollama is single-threaded per model — serialise embedding calls so workers
# don't pile up concurrent requests causing read timeouts.
_embed_lock = threading.Lock()


# ── Embedding (Ollama) ────────────────────────────────────────────────────────

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Call Ollama embedding model (serialised). Raises on failure — never returns zeros."""
    if not texts:
        return []
    payload = {"model": OLLAMA_EMBED_MODEL, "input": texts}
    with _embed_lock:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json=payload,
            timeout=600,
        )
        resp.raise_for_status()
        vecs = resp.json().get("embeddings", [])
    if not vecs:
        raise RuntimeError("Ollama returned empty embeddings list")
    logger.debug("[Embed] %d texts → %d vectors (dim=%d)",
                 len(texts), len(vecs), len(vecs[0]) if vecs else 0)
    return vecs


# ── PDF → images ──────────────────────────────────────────────────────────────

def _pdf_to_images(raw_bytes: bytes, dpi: int = 150):
    """Convert PDF bytes to list of PIL Images using fitz."""
    import fitz
    from PIL import Image as PILImage

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    images = []
    for page in doc:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat)
        img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    doc.close()
    return images


# ── Quality score ─────────────────────────────────────────────────────────────

def _quality_score(text: str) -> float:
    if not text:
        return 0.0
    words = text.split()
    if len(words) < 3:
        return 0.1
    alpha = sum(c.isalpha() for c in text)
    ratio = alpha / max(len(text), 1)
    return min(1.0, max(0.1, ratio))


# ── Main pipeline class ───────────────────────────────────────────────────────

class SequentialPipeline:
    """
    Drop-in replacement for StagePipeline with the same public interface:
      submit(doc_job) — enqueue a document for processing
      start()         — start worker threads
      stop(timeout)   — graceful shutdown
    """

    def __init__(self, rsm, rbac, storage=None, n_workers: int = _WORKER_COUNT,
                 api_only: bool = False):
        self.rsm      = rsm
        self.rbac     = rbac
        self.storage  = storage
        self.n_workers = n_workers
        self._api_only = api_only

        self._doc_q    = PriorityQueue(maxsize=_DOC_Q_MAXSIZE)
        self._submit_seq = itertools.count()   # tiebreaker — prevents DocJob comparison
        self._shutdown = threading.Event()
        self._threads: list[threading.Thread] = []
        self._chunker  = DocumentChunker(
            chunk_size=cfg.chunk_size,
            chunk_overlap=cfg.chunk_overlap,
        )

        # DotsOCR loaded once and shared across all workers.
        # Skipped in api_only mode (the API container never runs OCR).
        # OCR inference is NOT thread-safe on MPS — serialise with a lock so only
        # one worker runs a forward pass at a time.
        self._ocr_parser      = None if api_only else self._load_dots_ocr()
        self._ocr_lock        = threading.Lock()
        # ColPali inference shares the same MPS constraint — must be serialised.
        self._colpali_embed_lock = threading.Lock()
        # ColPali loaded in a background thread — pipeline doesn't wait for it.
        # _colpali is None until the background thread sets it; docs processed
        # before it's ready skip visual embeddings (they can be backfilled later).
        self._colpali      = None
        self._colpali_lock = threading.Lock()
        self._colpali_ready = threading.Event()
        t = threading.Thread(target=self._bg_load_colpali, daemon=True)
        t.start()

    # ── ColPali helpers ───────────────────────────────────────────────────────

    _COLPALI_WORD_THRESHOLD = int(os.getenv("COLPALI_WORD_THRESHOLD", "80"))

    def _needs_colpali(self, page_md: str) -> bool:
        """True if the page has too few words to rely on text retrieval alone."""
        cleaned = re.sub(r'!\[.*?\]\([^)]*\)', '', page_md)
        return len(cleaned.split()) < self._COLPALI_WORD_THRESHOLD

    def _bg_load_colpali(self):
        """Background thread — downloads/loads ColPali without blocking the pipeline."""
        try:
            from src.ingestion.embedding.colpali_embedder import ColPaliEmbedder
            logger.info("[ColPali] Background load starting…")
            embedder = ColPaliEmbedder()
            with self._colpali_lock:
                self._colpali = embedder
            self._colpali_ready.set()
            logger.info("[ColPali] Background load complete — visual embeddings enabled")
        except Exception as e:
            logger.error("[ColPali] Background load failed: %s", e)
            self._colpali_ready.set()  # unblock any waiters so they see None

    def _get_colpali(self):
        """Return ColPali embedder if ready, else None (non-blocking)."""
        return self._colpali

    # ── DotsOCR loader ────────────────────────────────────────────────────────

    def _load_dots_ocr(self):
        weights = cfg.dots_ocr_weights_path
        use_hf  = cfg.dots_ocr_use_hf
        logger.info("[SeqPipeline] Loading DotsOCR (use_hf=%s  weights=%s)", use_hf, weights)
        from dots_ocr.parser import DotsOCRParser
        parser = DotsOCRParser(
            model_name=weights,
            use_hf=use_hf,
        )
        logger.info("[SeqPipeline] DotsOCR ready")
        return parser

    # ── OCR one page (DotsOCR, no file I/O) ───────────────────────────────────

    def _ocr_page(self, image, page_idx: int, filename: str) -> str:
        """Run DotsOCR on a single PIL image, return markdown text.

        fetch_image smart-resizes to DotsOCR's MAX_PIXELS budget (11.28M).
        At 300 DPI an A4 page is ~8.7M pixels so it passes through unchanged.
        _inference_with_hf then downscales to its 672×672 inference budget
        (LANCZOS from the full-res source) and remaps bbox coords back to
        fetch_image dimensions. post_process_output maps those back to the
        original page dimensions.
        """
        from dots_ocr.utils.image_utils import fetch_image
        from dots_ocr.utils.prompts import dict_promptmode_to_prompt
        from dots_ocr.utils.layout_utils import post_process_output
        from dots_ocr.utils.format_transformer import layoutjson2md

        prompt_mode = cfg.dots_ocr_prompt_mode
        try:
            img_resized = fetch_image(image)
            prompt      = dict_promptmode_to_prompt[prompt_mode]

            with self._ocr_lock:
                response = self._ocr_parser._inference_with_hf(img_resized, prompt)

            if prompt_mode in ("prompt_layout_all_en", "prompt_layout_only_en"):
                cells, filtered = post_process_output(
                    response, prompt_mode, image, img_resized)
                if filtered:
                    md = cells  # cells is already a markdown string in fallback mode
                else:
                    md = layoutjson2md(image, cells, text_key="text", no_page_hf=True)
            else:
                md = response  # simple prompt modes return raw text

            # Strip base64-embedded picture regions — useless for text retrieval
            # and can blow the markdown up to 1MB+ per page.
            md = re.sub(r'!\[\]\([A-Za-z0-9+/=,;:]{80,}\)', '[image]', md)
            md = re.sub(r'!\[\]\(data:[^)]+\)', '[image]', md)

            logger.debug("[OCR] '%s' page %d → %d chars", filename, page_idx, len(md))
            return md
        except Exception as e:
            logger.warning("[OCR] '%s' page %d failed: %s", filename, page_idx, e)
            return ""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        logger.info("[SeqPipeline] Starting %d sequential worker(s) "
                    "(OCR=DotsOCR/%s  Embed=%s)",
                    self.n_workers, cfg.dots_ocr_prompt_mode, OLLAMA_EMBED_MODEL)
        for i in range(self.n_workers):
            t = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"seq-worker-{i}",
            )
            self._threads.append(t)
            t.start()

    def stop(self, timeout: float = 60.0):
        logger.info("[SeqPipeline] Stopping…")
        self._shutdown.set()
        for t in self._threads:
            t.join(timeout=timeout)
        logger.info("[SeqPipeline] Stopped")

    def submit(self, doc_job: DocJob):
        """Enqueue a document — smallest files are processed first."""
        try:
            self._doc_q.put((doc_job.file_size_kb, next(self._submit_seq), doc_job))
        except Exception as _e:
            logger.error("[SeqPipeline] Queue error, dropping '%s': %s", doc_job.filename, _e)

    # ── Worker loop ───────────────────────────────────────────────────────────

    def _worker_loop(self):
        name = threading.current_thread().name
        logger.info("[%s] Worker started", name)
        while not self._shutdown.is_set():
            try:
                _, _seq, doc = self._doc_q.get(timeout=1)
            except Empty:
                continue
            try:
                self._process(doc)
            except Exception as e:
                logger.error("[%s] Unhandled error processing '%s': %s",
                             name, doc.filename, e, exc_info=True)
                self._fail(doc, str(e))
        logger.info("[%s] Worker stopped", name)

    # ── SeaweedFS helper (async storage called from sync worker thread) ──────

    def _upload_to_seaweedfs(self, doc: DocJob) -> None:
        """Upload the raw PDF from disk to SeaweedFS. Swallows all errors so a
        storage outage never blocks ingestion."""
        if not self.storage:
            return
        try:
            loop = asyncio.new_event_loop()
            try:
                key = loop.run_until_complete(
                    self.storage.store_pdf_from_path(doc.session_id, doc.file_path, dest_filename=doc.filename)
                )
                logger.info("[SeqPipeline] '%s' stored to SeaweedFS → %s",
                            doc.filename, key)
            finally:
                loop.close()
        except Exception as e:
            logger.warning("[SeqPipeline] SeaweedFS upload failed for '%s': %s",
                           doc.filename, e)

    # ── Document processing ───────────────────────────────────────────────────

    def _process(self, doc: DocJob):
        t0 = time.time()
        logger.info("[SeqPipeline] Processing '%s'", doc.filename)

        # 0. Skip if already completed OR actively processing (guards against duplicate queue
        #    messages). Skipping "processing" is safe: if the first worker crashes,
        #    supervisor resets it to 'pending' after 20 min and it gets re-queued.
        if self.rbac and doc.upload_id:
            try:
                _early_id = self.rbac.find_document_by_upload_id(
                    doc.upload_id, upload_type=doc.upload_type)
                if _early_id:
                    doc.db_doc_id = _early_id
                    _early_status = self.rbac.get_document_status(_early_id)
                    if _early_status in ("completed", "processing"):
                        logger.info("[SeqPipeline] '%s' already %s — skipping duplicate",
                                    doc.filename, _early_status)
                        return
            except Exception:
                pass

        # 1. Read bytes ─────────────────────────────────────────────────────
        try:
            raw_bytes = Path(doc.file_path).read_bytes()
        except OSError as e:
            # File missing — check if already completed (duplicate submission after delete)
            if doc.db_doc_id and self.rbac:
                try:
                    st = self.rbac.get_document_status(doc.db_doc_id)
                    if st == "completed":
                        logger.info("[SeqPipeline] '%s' already completed, skipping duplicate",
                                    doc.filename)
                        return
                except Exception:
                    pass
            logger.error("[SeqPipeline] Cannot read '%s': %s", doc.filename, e)
            self._fail(doc, f"File read error: {e}")
            return

        # 1b. Upload raw PDF to SeaweedFS (non-blocking — errors are logged, not fatal)
        self._upload_to_seaweedfs(doc)

        # 2. SHA-256 + dedup ────────────────────────────────────────────────
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        doc.content_hash = content_hash

        if self.rsm:
            existing = self.rsm.check_dedup(content_hash)
            if existing:
                logger.info("[SeqPipeline] '%s' already processed — skipping", doc.filename)
                self._skip(doc, existing)
                return

        if self.rbac and doc.dept_id and doc.dept_id != "None":
            existing = self.rbac.find_doc_by_hash(content_hash, doc.dept_id)
            if existing:
                logger.info("[SeqPipeline] '%s' exists in DB (doc=%s) — skipping",
                            doc.filename, existing)
                if self.rsm:
                    self.rsm.set_dedup(content_hash, existing)
                self._skip(doc, existing)
                return

        # 3. Resolve db_doc_id from upload_id ───────────────────────────────
        self._update_stage(doc, "preprocessing", 5)
        if self.rbac and doc.upload_id:
            try:
                pid = self.rbac.find_document_by_upload_id(
                    doc.upload_id, upload_type=doc.upload_type)
                if pid:
                    doc.db_doc_id = pid
                    self.rbac.update_document_stage(
                        pid, "preprocessing", processing_started_at=t0)
            except Exception as e:
                logger.debug("[SeqPipeline] stage-db-update skipped: %s", e)

        # 3b. Atomic claim — only ONE worker proceeds even with duplicate queue messages.
        #     If the doc is already 'processing' or 'completed', another worker
        #     beat us to it; discard this copy immediately.
        if self.rbac and doc.db_doc_id:
            try:
                if not self.rbac.claim_document_for_processing(doc.db_doc_id):
                    status = self.rbac.get_document_status(doc.db_doc_id)
                    logger.info("[SeqPipeline] '%s' already %s — dropping duplicate",
                                doc.filename, status)
                    return
            except Exception as e:
                logger.debug("[SeqPipeline] claim skipped: %s", e)

        # 4. PDF → images ───────────────────────────────────────────────────
        self._update_stage(doc, "preprocessing", 10)
        try:
            images = _pdf_to_images(raw_bytes, dpi=300)
        except Exception as e:
            logger.error("[SeqPipeline] PDF decode failed '%s': %s", doc.filename, e)
            self._fail(doc, f"PDF decode error: {e}")
            return
        finally:
            del raw_bytes

        n_pages = len(images)
        logger.info("[SeqPipeline] '%s' → %d pages", doc.filename, n_pages)

        # 5. OCR each page via DotsOCR ────────────────────────────────────────
        self._update_stage(doc, "ocr", 20, extra={"pages": n_pages})
        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_stage(
                    doc.db_doc_id, "ocr",
                    ocr_total_pages=n_pages,
                    ocr_current_page=0,
                )
            except Exception:
                pass

        page_markdowns = []
        for i, img in enumerate(images):
            if self._shutdown.is_set():
                return
            pct = 20 + int(50 * (i + 1) / n_pages)
            self._update_stage(doc, "ocr", pct)
            if self.rbac and doc.db_doc_id:
                try:
                    self.rbac.update_document_stage(
                        doc.db_doc_id, "ocr", ocr_current_page=i + 1)
                except Exception:
                    pass
            md = self._ocr_page(img, i, doc.filename)
            page_markdowns.append(md)

        full_markdown = "\n\n---\n\n".join(
            f"<!-- page {i+1} -->\n{md}" for i, md in enumerate(page_markdowns)
        )
        logger.info("[SeqPipeline] '%s' OCR done (%d chars)", doc.filename, len(full_markdown))

        # Persist page_count + content_hash + filename metadata ─────────────
        fname_meta = parse_filename_metadata(doc.filename)
        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_from_pipeline(
                    doc_id=doc.db_doc_id,
                    file_path=doc.file_path,
                    content_hash=content_hash,
                    page_count=n_pages,
                    ocr_used=True,
                    fname_meta=fname_meta,
                    file_size=0,
                )
            except Exception as e:
                logger.debug("[SeqPipeline] update_document_from_pipeline skipped: %s", e)

        # Extract structured fields from OCR text and persist ────────────────
        if self.rbac and doc.db_doc_id:
            try:
                ocr_meta = extract_from_ocr(full_markdown)
                if ocr_meta:
                    self.rbac.update_document_extraction(doc.db_doc_id, ocr_meta)
                    logger.info("[SeqPipeline] '%s' metadata extracted: %s",
                                doc.filename,
                                {k: v for k, v in ocr_meta.items() if k != "extracted_text"})
            except Exception as e:
                logger.warning("[SeqPipeline] OCR metadata extraction failed for '%s': %s",
                               doc.filename, e)

        # 5b. ColPali visual embeddings — runs on ALL pages after assembly ────────
        visual_page_nums = list(range(len(page_markdowns)))
        if visual_page_nums and doc.db_doc_id:
            logger.info("[ColPali] '%s' — %d visual pages: %s",
                        doc.filename, len(visual_page_nums), visual_page_nums[:10])
            colpali = self._get_colpali()
            if colpali:
                try:
                    BATCH = 4
                    for b in range(0, len(visual_page_nums), BATCH):
                        batch_nums = visual_page_nums[b:b+BATCH]
                        batch_imgs = [images[pg] for pg in batch_nums if pg < len(images)]
                        if not batch_imgs:
                            continue
                        with self._colpali_embed_lock:
                            vecs = colpali.embed_pages(batch_imgs)
                        for pg, vec in zip(batch_nums, vecs):
                            try:
                                self.rbac.store_colpali_embedding(
                                    doc_id=doc.db_doc_id,
                                    dept_id=doc.dept_id,
                                    page_num=pg,
                                    embedding=vec,
                                )
                            except Exception as ce:
                                logger.warning("[ColPali] store page %d failed: %s", pg, ce)
                    logger.info("[ColPali] '%s' — stored %d visual embeddings",
                                doc.filename, len(visual_page_nums))
                except Exception as e:
                    logger.error("[ColPali] '%s' failed: %s", doc.filename, e)

        del images

        # 6. Chunk ───────────────────────────────────────────────────────────
        self._update_stage(doc, "chunking", 72)
        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_stage(doc.db_doc_id, "chunking")
            except Exception:
                pass

        chunk_dicts = self._chunker.chunk_document(full_markdown)
        if not chunk_dicts:
            logger.warning("[SeqPipeline] '%s' produced no chunks", doc.filename)
            self._fail(doc, "No chunks produced")
            return

        chunk_texts = [c.get("content", c.get("text", "")) for c in chunk_dicts]
        logger.info("[SeqPipeline] '%s' → %d chunks", doc.filename, len(chunk_texts))

        # Push chunk count to Redis so the UI can display it
        self._update_stage(doc, "chunking", 75, extra={"chunks": len(chunk_texts)})

        # 7. Embed via Ollama ────────────────────────────────────────────────
        self._update_stage(doc, "embedding", 80)
        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_stage(doc.db_doc_id, "embedding")
            except Exception:
                pass

        all_embeddings = []
        batch_size = 128
        for start in range(0, len(chunk_texts), batch_size):
            if self._shutdown.is_set():
                return
            batch = chunk_texts[start:start + batch_size]
            vecs  = _embed_texts(batch)
            all_embeddings.extend(vecs)

        if len(all_embeddings) != len(chunk_texts):
            logger.error("[SeqPipeline] Embedding count mismatch: %d texts vs %d vecs",
                         len(chunk_texts), len(all_embeddings))
            self._fail(doc, "Embedding count mismatch")
            return

        # 8. Store ───────────────────────────────────────────────────────────
        self._update_stage(doc, "storing", 85)

        # If db_doc_id is still None (e.g. /ingest API skipped create_document_pending,
        # or find_document_by_upload_id returned None due to a race), create the
        # document record now so chunks have a valid FK parent.
        if self.rbac and not doc.db_doc_id:
            try:
                u_id_for_doc = doc.upload_id if doc.upload_type == "user"  else None
                a_id_for_doc = doc.upload_id if doc.upload_type == "admin" else None
                doc.db_doc_id = self.rbac.create_document(
                    file_name=doc.filename,
                    file_path=doc.file_path,
                    dept_id=doc.dept_id,
                    uploaded_by=doc.user_id,
                    content_hash=content_hash,
                    page_count=n_pages,
                    ocr_used=True,
                    source_user_upload_id=u_id_for_doc,
                    source_admin_upload_id=a_id_for_doc,
                    fname_meta=parse_filename_metadata(doc.filename),
                    file_size=0,
                )
                logger.info("[SeqPipeline] Created missing doc record for '%s' → %s",
                            doc.filename, doc.db_doc_id)
            except Exception as e:
                logger.error("[SeqPipeline] Could not create doc record for '%s': %s",
                             doc.filename, e)
                self._fail(doc, f"Document record creation failed: {e}")
                return

        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_stage(doc.db_doc_id, "storing")
            except Exception:
                pass

        doc_id = doc.db_doc_id
        u_id = doc.upload_id if doc.upload_type == "user"  else None
        a_id = doc.upload_id if doc.upload_type == "admin" else None

        try:
            for idx, (chunk_dict, embedding) in enumerate(
                    zip(chunk_dicts, all_embeddings)):
                text        = chunk_texts[idx]
                token_count = len(text.split())
                page_num    = chunk_dict.get("metadata", {}).get("page", 0)
                q_score     = _quality_score(text)

                try:
                    chunk_id = self.rbac.add_chunk(
                        doc_id=doc_id,
                        chunk_index=idx,
                        chunk_text=text,
                        chunk_token_count=token_count,
                        page_num=page_num,
                        source_user_upload_id=u_id,
                        source_admin_upload_id=a_id,
                        quality_score=q_score,
                    )
                except Exception:
                    chunk_id = self.rbac.add_chunk(
                        doc_id=doc_id,
                        chunk_index=idx,
                        chunk_text=text,
                        chunk_token_count=token_count,
                        page_num=page_num,
                        quality_score=q_score,
                    )

                try:
                    self.rbac.store_embedding(
                        chunk_id=chunk_id,
                        dept_id=doc.dept_id,
                        embedding=embedding,
                        source_user_upload_id=u_id,
                        source_admin_upload_id=a_id,
                    )
                except Exception:
                    self.rbac.store_embedding(
                        chunk_id=chunk_id,
                        dept_id=doc.dept_id,
                        embedding=embedding,
                    )

        except Exception as e:
            logger.error("[SeqPipeline] Store failed for '%s': %s",
                         doc.filename, e, exc_info=True)
            self._fail(doc, f"Store error: {e}")
            return

        # 9. Finalise ────────────────────────────────────────────────────────
        try:
            if self.rbac and doc_id:
                self.rbac.update_document_status(doc_id, "completed")
                if doc.upload_id:
                    self.rbac.update_upload_status(
                        doc.upload_id, doc.upload_type, "completed")
        except Exception as e:
            logger.warning("[SeqPipeline] Status finalise failed for '%s': %s",
                           doc.filename, e)

        if self.rsm and content_hash and doc_id:
            try:
                self.rsm.set_dedup(content_hash, doc_id)
            except Exception:
                pass

        elapsed = time.time() - t0
        self._update_stage(doc, "done", 100, extra={
            "doc_id":   doc_id or "",
            "pages":    n_pages,
            "chunks":   len(chunk_texts),
            "duration": f"{elapsed:.1f}",
        })
        logger.info("[SeqPipeline] '%s' done in %.1f s (%d pages, %d chunks)",
                    doc.filename, elapsed, n_pages, len(chunk_texts))

        try:
            fpath = Path(doc.file_path)
            if fpath.exists():
                fpath.unlink()
        except Exception as e:
            logger.warning("[SeqPipeline] Could not delete '%s': %s", doc.file_path, e)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_stage(self, doc: DocJob, stage: str, pct: int, extra: dict = None):
        if self.rsm:
            try:
                self.rsm.update_stage(doc.file_id, doc.session_id, stage, pct, extra=extra)
            except Exception:
                pass

    def _fail(self, doc: DocJob, error: str):
        logger.error("[SeqPipeline] FAILED '%s': %s", doc.filename, error)
        if self.rsm:
            try:
                self.rsm.update_stage(doc.file_id, doc.session_id, "error", 0,
                                      extra={"error": error})
                self.rsm.incr_stat("total_failed")
            except Exception:
                pass
        if self.rbac and doc.db_doc_id:
            try:
                self.rbac.update_document_status(doc.db_doc_id, "failed")
            except Exception:
                pass

    def _skip(self, doc: DocJob, existing_doc_id: str):
        if self.rsm:
            try:
                self.rsm.update_stage(doc.file_id, doc.session_id, "done", 100,
                                      extra={"doc_id": existing_doc_id or ""})
            except Exception:
                pass
        # Mark the pending DB doc as completed so it doesn't stay stuck at 'pending'
        target_id = doc.db_doc_id or existing_doc_id
        if self.rbac and target_id:
            try:
                self.rbac.update_document_status(target_id, "completed")
            except Exception:
                pass
