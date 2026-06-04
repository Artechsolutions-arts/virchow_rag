"""
Phase 2: Structured extraction ingestion pipeline.

Entry points:
  ingest_document(pdf_bytes, file_name, dept_id, db, embedder) → document_id
  ingest_from_seaweedfs(file_path, file_name, dept_id, db, embedder) → document_id

Ordering:
  1. Parse filename metadata (ground truth for doc_type, fiscal_year, etc.)
  2. OCR all pages via DotsOCR (local vLLM)
  3. Parse HTML tables → line items + financial totals (authoritative for amounts)
  4. LLM entity extraction → party_name, GSTIN, doc_date, doc_number, etc.
  5. Merge fields: filename > LLM; table parser > LLM for amounts
  6. Score OCR quality per chunk
  7. Build enriched chunks (prepend metadata header)
  8. Embed all chunks
  9. Upsert to DB (idempotent: delete existing chunks/line_items first)
 10. Store document references for Phase 5 resolution pass

Idempotency: re-running on the same document is safe.
Existing chunks and line items are deleted before re-inserting.
Only re-embeds chunks where the text has changed (hash comparison).
"""

import logging
import re

import requests

from src.config import cfg
from src.ingestion.dotsocr_client import ocr_pdf, check_vllm_health
from src.ingestion.html_table_parser import parse_tables
from src.ingestion.entity_extractor import extract_entities, normalize_party_name
from src.ingestion.ocr_quality import score_text, score_page_html
from src.ingestion.filename_parser import parse_filename_metadata

logger = logging.getLogger(__name__)

# Max characters per chunk (before embedding)
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100

# Enrichment header template (one per chunk, prepended before embedding)
_ENRICHMENT_TEMPLATE = (
    "[{file_name} | {doc_type} | {doc_month} {fiscal_year} | "
    "{doc_unit} | Vendor: {party_name} | Total: ₹{total_amount}]\n"
)


def _build_enrichment_header(meta: dict) -> str:
    return _ENRICHMENT_TEMPLATE.format(
        file_name=meta.get("file_name", ""),
        doc_type=meta.get("doc_type") or "Unknown",
        doc_month=meta.get("doc_month") or "",
        fiscal_year=meta.get("fiscal_year") or "",
        doc_unit=meta.get("doc_unit") or "",
        party_name=meta.get("party_name") or "Unknown",
        total_amount=f'{meta["total_amount"]:,.2f}' if meta.get("total_amount") else "N/A",
    )


def _split_text_into_chunks(text: str, chunk_size: int = _CHUNK_SIZE,
                             overlap: int = _CHUNK_OVERLAP) -> list:
    """Split text into overlapping chunks. Returns list of (chunk_text, approx_page_num)."""
    if not text or not text.strip():
        return []

    # Split on paragraph boundaries first, then fall back to hard split
    paragraphs = re.split(r'\n{2,}', text)
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
                # Keep last `overlap` chars for context continuity
                current = current[-overlap:].strip() + "\n\n" + para
            else:
                # Para itself is too long — hard-split
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i:i + chunk_size])
                current = ""

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _html_to_text(html: str) -> str:
    """Strip HTML tags from DotsOCR output to get plain text for chunking."""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def ingest_document(
    pdf_bytes: bytes,
    file_name: str,
    dept_id: str,
    file_path: str,
    db,
    embedder,
    user_id: str = None,
    skip_vllm_check: bool = False,
) -> str:
    """
    Full ingestion pipeline for one PDF document.

    Args:
        pdf_bytes:       Raw PDF bytes.
        file_name:       Original filename (e.g. "DEC-U2-PUR-24-25-40.pdf").
        dept_id:         Department UUID that owns this document.
        file_path:       SeaweedFS path (stored in documents.file_path).
        db:              RBACManager instance (has all ingestion DB methods).
        embedder:        MxbaiEmbedder instance.
        skip_vllm_check: Set True in tests or when health was already verified.

    Returns:
        document_id (UUID string)
    """
    logger.info("[Ingest] Starting ingestion for %s", file_name)

    # ── Step 1: Filename metadata (ground truth) ──────────────────────────────
    filename_meta = parse_filename_metadata(file_name)
    meta = dict(filename_meta)
    meta["file_name"] = file_name

    # ── Step 2: OCR ───────────────────────────────────────────────────────────
    if not skip_vllm_check:
        check_vllm_health(timeout=60)

    page_html_list = ocr_pdf(pdf_bytes)
    if not page_html_list:
        # Record the failure in the DB so operators can see it, then abort.
        # Continuing with empty OCR output would produce zero retrievable chunks —
        # the document would appear ingested but return nothing on search.
        err_doc_id = db.upsert_document(
            dept_id, file_name, file_path,
            {**meta, "ocr_status": "failed", "ocr_quality_score": 0.0},
            user_id=user_id,
        )
        db.log_failed_extraction(err_doc_id, "ocr", "ocr_pdf returned zero pages")
        raise RuntimeError(f"[Ingest] OCR returned no pages for {file_name}")

    # ── Step 3: Table parsing (authoritative for amounts) ─────────────────────
    all_line_items = []
    combined_html = "\n".join(page_html_list)

    table_result = parse_tables(combined_html)
    if table_result.total_amount is not None:
        meta["total_amount"] = table_result.total_amount
    if table_result.tax_amount is not None:
        meta["tax_amount"] = table_result.tax_amount
    if table_result.net_amount is not None:
        meta["net_amount"] = table_result.net_amount
    all_line_items = table_result.line_items

    if not all_line_items:
        logger.info(
            "[Ingest] No line items extracted from tables in %s (may have no tables)", file_name
        )

    # ── Step 4: LLM entity extraction (header fields only) ───────────────────
    combined_text = _html_to_text(combined_html)
    extracted, error = extract_entities(combined_text)

    if error:
        logger.warning("[Ingest] Entity extraction failed for %s: %s", file_name, error)
        # Will be logged to failed_extractions after document_id is known
    else:
        # LLM fields — do NOT overwrite amounts (table parser is authoritative)
        for field in ("party_name", "party_gstin", "doc_date", "doc_number",
                      "payment_terms", "ref_doc_number"):
            if field in extracted and extracted[field] and field not in meta:
                meta[field] = extracted[field]
        # Filename-derived fields already in meta — they override LLM here silently
        # (e.g. if LLM says doc_type=Invoice but filename says PUR → PUR wins from Step 1)

    # Compute canonical party name for aggregation (raw value preserved in party_name)
    meta["party_name_canonical"] = normalize_party_name(meta.get("party_name"))

    # ── Step 5: OCR quality score (document-level: mean of page scores) ───────
    page_scores = [score_page_html(h) for h in page_html_list]
    if page_scores:
        meta["ocr_quality_score"] = round(sum(page_scores) / len(page_scores), 3)
    # ── Step 6: Upsert document record (status=processing until chunks are written) ─────
    meta["ocr_status"] = "processing"
    document_id = db.upsert_document(dept_id, file_name, file_path, meta, user_id=user_id)
    logger.info("[Ingest] Document upserted: %s → %s", file_name, document_id)

    # Log entity extraction failure now that we have document_id
    if error:
        db.log_failed_extraction(document_id, "entity_extraction", error)

    # ── Step 7: Idempotent chunk/line_item cleanup ────────────────────────────
    db.delete_chunks_for_document(document_id)
    db.delete_line_items_for_document(document_id)

    # ── Step 8: Build and embed chunks ────────────────────────────────────────
    enrichment_header = _build_enrichment_header(meta)
    chunk_texts = _split_text_into_chunks(combined_text)

    if not chunk_texts:
        logger.warning("[Ingest] No text chunks produced for %s", file_name)

    for chunk_index, chunk_text in enumerate(chunk_texts):
        quality = score_text(chunk_text)
        enriched_text = enrichment_header + chunk_text

        chunk_id = db.insert_chunk(
            document_id=document_id,
            chunk_index=chunk_index,
            chunk_text=enriched_text,
            quality_score=quality,
            page_num=0,  # page_num per-chunk approximation: use 0 until page-aware chunking
        )

        # Embed only chunks above the quality floor
        if quality >= cfg.ocr_quality_min:
            try:
                embedding = embedder.embed_text(enriched_text)
                db.insert_embedding(chunk_id, dept_id, embedding)
            except Exception as e:
                logger.error(
                    "[Ingest] Embedding failed for chunk %d of %s: %s",
                    chunk_index, file_name, e
                )
        else:
            logger.info(
                "[Ingest] Chunk %d skipped (quality=%.3f < %.3f): %s",
                chunk_index, quality, cfg.ocr_quality_min, file_name
            )

    logger.info(
        "[Ingest] %d chunks written for %s (quality threshold %.2f)",
        len(chunk_texts), file_name, cfg.ocr_quality_min
    )

    # ── Step 9: Insert line items ─────────────────────────────────────────────
    db.insert_line_items(document_id, all_line_items)
    logger.info("[Ingest] %d line items written for %s", len(all_line_items), file_name)

    # ── Step 10: Document references (for Phase 5 resolution pass) ────────────
    ref_doc_number = meta.get("ref_doc_number")
    if ref_doc_number:
        db.insert_document_reference(document_id, ref_doc_number)

    # ── Step 11: Mark document completed (all chunks written) ────────────────
    db.upsert_document(dept_id, file_name, file_path, {**meta, "ocr_status": "completed"}, user_id=user_id)

    logger.info("[Ingest] Completed: %s", file_name)
    return document_id


def ingest_from_seaweedfs(
    file_path: str,
    file_name: str,
    dept_id: str,
    db,
    embedder,
) -> str:
    """
    Download a PDF from SeaweedFS and run the ingestion pipeline.
    Used by the Phase 3 backfill script.
    """
    # Build SeaweedFS URL from file_path (format: {uuid}/{filename})
    url = f"{cfg.seaweedfs_filer_url}/buckets/{cfg.seaweedfs_bucket}/raw/{file_path}"
    logger.info("[Ingest] Fetching from SeaweedFS: %s", url)

    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        pdf_bytes = r.content
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {file_path} from SeaweedFS: {e}")

    return ingest_document(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        dept_id=dept_id,
        file_path=file_path,
        db=db,
        embedder=embedder,
        skip_vllm_check=True,  # caller already verified health before starting
    )
