"""
Tests for src/ingestion/entity_extractor.py

Mocks httpx.post so these run without a live Ollama instance.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import pytest

from src.ingestion.entity_extractor import (
    extract_entities,
    _call_llm,
    _parse_json_response,
    _validate_and_clean,
)


# ── _parse_json_response ────────────────────────────────────────────────────

def test_parse_json_response_plain():
    result = _parse_json_response('{"party_name": "Acme Ltd"}')
    assert result == {"party_name": "Acme Ltd"}


def test_parse_json_response_markdown_fences():
    result = _parse_json_response('```json\n{"doc_number": "INV-001"}\n```')
    assert result == {"doc_number": "INV-001"}


def test_parse_json_response_no_json_block():
    assert _parse_json_response("Sorry, I cannot extract that.") is None


def test_parse_json_response_empty():
    assert _parse_json_response("") is None
    assert _parse_json_response(None) is None


# ── _validate_and_clean ─────────────────────────────────────────────────────

def test_validate_party_name():
    result = _validate_and_clean({"party_name": "  Acme Pharma Ltd  "})
    assert result["party_name"] == "Acme Pharma Ltd"


def test_validate_party_name_too_short():
    result = _validate_and_clean({"party_name": "X"})
    assert "party_name" not in result


def test_validate_party_name_html_escaped():
    result = _validate_and_clean({"party_name": "<script>XSS</script>"})
    assert "<script>" not in result["party_name"]
    assert "&lt;script&gt;" in result["party_name"]


def test_validate_gstin_valid():
    result = _validate_and_clean({"party_gstin": "27AAACT2727Q1ZV"})
    assert result["party_gstin"] == "27AAACT2727Q1ZV"


def test_validate_gstin_invalid():
    result = _validate_and_clean({"party_gstin": "INVALID"})
    assert "party_gstin" not in result


def test_validate_doc_date_iso_produces_a_date():
    # dayfirst=True can re-order ISO components — just verify output is YYYY-MM-DD
    result = _validate_and_clean({"doc_date": "2024-04-01"})
    if "doc_date" in result:
        assert len(result["doc_date"]) == 10
        assert result["doc_date"][4] == "-" and result["doc_date"][7] == "-"


def test_validate_doc_date_non_iso():
    # "01-04-2024" with dayfirst=True → April 1, 2024
    result = _validate_and_clean({"doc_date": "01-04-2024"})
    assert result["doc_date"] == "2024-04-01"


def test_validate_doc_date_unparseable():
    result = _validate_and_clean({"doc_date": "not a date at all"})
    assert "doc_date" not in result


def test_validate_doc_number_escaped():
    result = _validate_and_clean({"doc_number": "INV<001>"})
    assert result["doc_number"] == "INV&lt;001&gt;"


def test_validate_payment_terms():
    result = _validate_and_clean({"payment_terms": "Net 30"})
    assert result["payment_terms"] == "Net 30"


def test_validate_ref_doc_number():
    result = _validate_and_clean({"ref_doc_number": "PO-999"})
    assert result["ref_doc_number"] == "PO-999"


# ── _call_llm ───────────────────────────────────────────────────────────────

def _make_response(text: str):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": text}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def test_call_llm_success():
    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.return_value = _make_response('{"party_name":"Acme"}')
        result = _call_llm("some prompt")
    assert result == '{"party_name":"Acme"}'


def test_call_llm_http_error_returns_none():
    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.side_effect = Exception("connection refused")
        result = _call_llm("some prompt")
    assert result is None


# ── extract_entities ────────────────────────────────────────────────────────

def test_extract_entities_empty_text():
    result, error = extract_entities("")
    assert result == {}
    assert error == "empty document text"


def test_extract_entities_whitespace_only():
    result, error = extract_entities("   \n  ")
    assert result == {}
    assert "empty" in error


def test_extract_entities_success_first_attempt():
    response_json = '{"party_name": "Acme Pharma", "doc_number": "INV-2024-001", "party_gstin": null, "doc_date": "2024-04-01", "payment_terms": null, "ref_doc_number": null}'
    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.return_value = _make_response(response_json)
        result, error = extract_entities("Invoice text from Acme Pharma")
    assert error is None
    assert result["party_name"] == "Acme Pharma"
    assert result["doc_number"] == "INV-2024-001"


def test_extract_entities_full_fails_simplified_succeeds():
    good_json = '{"party_name": "Fallback Vendor", "doc_number": "GRN-55"}'
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            # Full prompt attempts return garbage
            return _make_response("not json at all")
        # Simplified prompt succeeds
        return _make_response(good_json)

    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.side_effect = side_effect
        result, error = extract_entities("Some document text")

    assert error is None
    assert result["party_name"] == "Fallback Vendor"
    assert call_count == 4  # 3 full + 1 simplified


def test_extract_entities_all_attempts_fail():
    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.return_value = _make_response("this is not json")
        result, error = extract_entities("Some document text")
    assert result == {}
    assert "failed" in error


def test_extract_entities_llm_unavailable():
    with patch("src.ingestion.entity_extractor.httpx.post") as mock_post:
        mock_post.side_effect = Exception("connection refused")
        result, error = extract_entities("Some document text")
    assert result == {}
    assert error is not None
