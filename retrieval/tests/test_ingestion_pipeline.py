"""
Tests for src/ingestion/ingestion_pipeline.py

All external dependencies (OCR, LLM, DB, embedder, HTTP) are mocked.
Tests focus on the orchestration logic and branching in ingest_document().
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock, call
import pytest

from src.ingestion.ingestion_pipeline import (
    _build_enrichment_header,
    _split_text_into_chunks,
    _html_to_text,
    ingest_document,
    ingest_from_seaweedfs,
)
from src.ingestion.html_table_parser import TableParseResult


# ── Pure helpers ────────────────────────────────────────────────────────────

def test_html_to_text_strips_tags():
    result = _html_to_text("<p>Hello <b>world</b></p>")
    assert "<" not in result
    assert "Hello" in result
    assert "world" in result


def test_html_to_text_entities():
    result = _html_to_text("&amp; &lt; &gt; &nbsp;")
    assert "&" in result
    assert "<" in result
    assert ">" in result


def test_html_to_text_empty():
    assert _html_to_text("") == ""
    assert _html_to_text(None) == ""


def test_build_enrichment_header_with_amount():
    meta = {
        "file_name": "DEC-U2-PUR-24-25-40.pdf",
        "doc_type": "Purchase Order",
        "doc_month": "December",
        "fiscal_year": "FY 2024-2025",
        "doc_unit": "U2",
        "party_name": "Acme Ltd",
        "total_amount": 12345.67,
    }
    header = _build_enrichment_header(meta)
    assert "DEC-U2-PUR-24-25-40.pdf" in header
    assert "Purchase Order" in header
    assert "Acme Ltd" in header
    assert "12,345.67" in header


def test_build_enrichment_header_no_amount():
    meta = {"file_name": "test.pdf"}
    header = _build_enrichment_header(meta)
    assert "N/A" in header
    assert "Unknown" in header


# ── _split_text_into_chunks ─────────────────────────────────────────────────

def test_split_empty_text():
    assert _split_text_into_chunks("") == []
    assert _split_text_into_chunks("   ") == []


def test_split_short_text_single_chunk():
    text = "This is a short paragraph."
    chunks = _split_text_into_chunks(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_paragraph_boundaries():
    text = "Para one.\n\nPara two.\n\nPara three."
    chunks = _split_text_into_chunks(text, chunk_size=20, overlap=5)
    assert len(chunks) >= 2
    assert all(c.strip() for c in chunks)


def test_split_long_paragraph_hard_split():
    long_para = "A" * 2000
    chunks = _split_text_into_chunks(long_para, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500


# ── ingest_document orchestration ──────────────────────────────────────────

def _make_db():
    db = MagicMock()
    db.upsert_document.return_value = "doc-uuid-1234"
    db.insert_chunk.return_value = "chunk-uuid-1"
    return db


def _make_embedder():
    emb = MagicMock()
    emb.embed_text.return_value = [0.1] * 1024
    return emb


def _make_table_result(total=None, line_items=None):
    r = TableParseResult()
    r.total_amount = total
    r.line_items = line_items or []
    return r


MOCK_PAGE_HTML = "<p>Invoice from Acme Ltd dated 2024-04-01 PO-999.</p>"
MOCK_COMBINED_TEXT = "Invoice from Acme Ltd dated 2024-04-01 PO-999."


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme Ltd"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_happy_path(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result(total=5000.0, line_items=[{"description": "Item A"}])
    db = _make_db()
    embedder = _make_embedder()

    doc_id = ingest_document(
        pdf_bytes=b"%PDF-1.4",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/DEC-U2-PUR-24-25-40.pdf",
        db=db,
        embedder=embedder,
    )

    assert doc_id == "doc-uuid-1234"
    mock_health.assert_called_once()
    mock_ocr.assert_called_once()
    db.delete_chunks_for_document.assert_called_once()
    db.delete_line_items_for_document.assert_called_once()
    db.insert_line_items.assert_called_once()
    # Final upsert marks document completed
    last_call_meta = db.upsert_document.call_args_list[-1][0][3]
    assert last_call_meta["ocr_status"] == "completed"


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_skip_vllm_check(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="JAN-U1-INV-24-25-1.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=_make_embedder(),
        skip_vllm_check=True,
    )
    mock_health.assert_not_called()


@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_ocr_returns_empty(mock_health, mock_ocr):
    db = _make_db()
    with pytest.raises(RuntimeError, match="OCR returned no pages"):
        ingest_document(
            pdf_bytes=b"%PDF",
            file_name="BAD.pdf",
            dept_id="dept-1",
            file_path="uuid/BAD.pdf",
            db=db,
            embedder=_make_embedder(),
        )
    db.log_failed_extraction.assert_called_once()


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({}, "LLM timed out"))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_entity_extraction_failure_logged(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=_make_embedder(),
        skip_vllm_check=True,
    )
    db.log_failed_extraction.assert_called_once_with("doc-uuid-1234", "entity_extraction", "LLM timed out")


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.05)  # below threshold
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_chunk_below_quality_threshold_not_embedded(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    embedder = _make_embedder()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=embedder,
        skip_vllm_check=True,
    )
    # Chunks inserted to DB but not embedded
    assert db.insert_chunk.called
    embedder.embed_text.assert_not_called()


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_embedding_failure_continues(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    embedder = MagicMock()
    embedder.embed_text.side_effect = RuntimeError("GPU OOM")
    # Should not raise — embedding failure is logged but ingestion continues
    doc_id = ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=embedder,
        skip_vllm_check=True,
    )
    assert doc_id == "doc-uuid-1234"


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme", "ref_doc_number": "PO-007"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_inserts_document_reference(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=_make_embedder(),
        skip_vllm_check=True,
    )
    db.insert_document_reference.assert_called_once_with("doc-uuid-1234", "PO-007")


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_no_ref_doc_number_no_reference_insert(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=_make_embedder(),
        skip_vllm_check=True,
    )
    db.insert_document_reference.assert_not_called()


@patch("src.ingestion.ingestion_pipeline.score_page_html", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.score_text", return_value=0.9)
@patch("src.ingestion.ingestion_pipeline.extract_entities", return_value=({"party_name": "Acme"}, None))
@patch("src.ingestion.ingestion_pipeline.parse_tables")
@patch("src.ingestion.ingestion_pipeline.ocr_pdf", return_value=[MOCK_PAGE_HTML])
@patch("src.ingestion.ingestion_pipeline.check_vllm_health")
def test_ingest_document_processing_state_before_completed(
    mock_health, mock_ocr, mock_tables, mock_entities, mock_score_text, mock_score_page
):
    mock_tables.return_value = _make_table_result()
    db = _make_db()
    ingest_document(
        pdf_bytes=b"%PDF",
        file_name="DEC-U2-PUR-24-25-40.pdf",
        dept_id="dept-1",
        file_path="uuid/f.pdf",
        db=db,
        embedder=_make_embedder(),
        skip_vllm_check=True,
    )
    calls = db.upsert_document.call_args_list
    # First non-failure call sets processing status
    processing_call = next(c for c in calls if c[0][3].get("ocr_status") == "processing")
    assert processing_call is not None
    # Last call sets completed
    last_meta = calls[-1][0][3]
    assert last_meta["ocr_status"] == "completed"


# ── ingest_from_seaweedfs ───────────────────────────────────────────────────

@patch("src.ingestion.ingestion_pipeline.ingest_document", return_value="doc-555")
@patch("src.ingestion.ingestion_pipeline.requests.get")
def test_ingest_from_seaweedfs_success(mock_get, mock_ingest):
    mock_resp = MagicMock()
    mock_resp.content = b"%PDF-1.4"
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = ingest_from_seaweedfs(
        file_path="uuid/test.pdf",
        file_name="test.pdf",
        dept_id="dept-1",
        db=MagicMock(),
        embedder=MagicMock(),
    )
    assert result == "doc-555"
    mock_ingest.assert_called_once()
    _, kwargs = mock_ingest.call_args
    assert kwargs.get("skip_vllm_check") is True


@patch("src.ingestion.ingestion_pipeline.requests.get")
def test_ingest_from_seaweedfs_http_failure(mock_get):
    mock_get.side_effect = Exception("connection refused")
    with pytest.raises(RuntimeError, match="Failed to fetch"):
        ingest_from_seaweedfs(
            file_path="uuid/bad.pdf",
            file_name="bad.pdf",
            dept_id="dept-1",
            db=MagicMock(),
            embedder=MagicMock(),
        )
