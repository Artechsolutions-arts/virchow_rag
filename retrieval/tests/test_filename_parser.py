"""
Tests for src/ingestion/filename_parser.py

Zero-tolerance financial system — filename-derived fields are ground truth.
If parse_filename_metadata() returns wrong values, SQL aggregations break.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.filename_parser import (
    parse_filename_metadata,
    MONTH_MAP,
    DOC_TYPE_MAP,
    MONTH_NAME_MAP,
)


# ── Standard format tests ─────────────────────────────────────────────────────

def test_standard_purchase_order():
    result = parse_filename_metadata("DEC-U2-PUR-24-25-40.pdf")
    assert result["doc_month"] == "December"
    assert result["doc_unit"] == "U2"
    assert result["doc_type"] == "Purchase Order"
    assert result["fiscal_year"] == "FY 2024-2025"
    assert result["serial_no"] == 40


def test_standard_grn():
    result = parse_filename_metadata("MAY-U3-RM-24-25-31.pdf")
    assert result["doc_month"] == "May"
    assert result["doc_unit"] == "U3"
    assert result["doc_type"] == "Goods Receipt Note"
    assert result["fiscal_year"] == "FY 2024-2025"
    assert result["serial_no"] == 31


def test_standard_delivery_note():
    result = parse_filename_metadata("FEB-U2-DN-24-25-12.pdf")
    assert result["doc_month"] == "February"
    assert result["doc_type"] == "Delivery Note"
    assert result["fiscal_year"] == "FY 2024-2025"
    assert result["serial_no"] == 12


def test_standard_sales_invoice():
    result = parse_filename_metadata("JUL-U1-INV-23-24-7.pdf")
    assert result["doc_month"] == "July"
    assert result["doc_unit"] == "U1"
    assert result["doc_type"] == "Sales Invoice"
    assert result["fiscal_year"] == "FY 2023-2024"
    assert result["serial_no"] == 7


def test_standard_credit_note():
    result = parse_filename_metadata("APR-U2-CR-24-25-3.pdf")
    assert result["doc_type"] == "Credit Note"
    assert result["doc_month"] == "April"


def test_standard_unit_number_preserved_uppercase():
    result = parse_filename_metadata("jan-u5-pur-24-25-1.pdf")
    assert result["doc_unit"] == "U5"
    assert result["doc_month"] == "January"
    assert result["doc_type"] == "Purchase Order"


# ── JV / short format tests ───────────────────────────────────────────────────

def test_jv_short_format():
    result = parse_filename_metadata("JV-24-25-5.pdf")
    assert result["doc_type"] == "Journal Voucher"
    assert result["fiscal_year"] == "FY 2024-2025"
    assert result["serial_no"] == 5
    assert "doc_unit" not in result
    assert "doc_month" not in result


def test_jv_no_serial():
    result = parse_filename_metadata("JV-24-25.pdf")
    assert result["doc_type"] == "Journal Voucher"
    assert result["fiscal_year"] == "FY 2024-2025"
    assert "serial_no" not in result


# ── Unknown / error cases ─────────────────────────────────────────────────────

def test_unknown_format_returns_empty():
    result = parse_filename_metadata("unknown-format.pdf")
    assert result == {}


def test_empty_string_returns_empty():
    result = parse_filename_metadata("")
    assert result == {}


def test_none_like_non_string_returns_empty():
    # Should not raise even if called with unexpected type
    # (guards against caller mistakes)
    try:
        result = parse_filename_metadata(None)  # type: ignore
        assert result == {}
    except TypeError:
        pass  # acceptable — None is not a valid filename


def test_no_extension_still_parses():
    result = parse_filename_metadata("DEC-U2-PUR-24-25-40")
    assert result["doc_month"] == "December"
    assert result["doc_type"] == "Purchase Order"


def test_unrecognised_doc_type_returns_partial():
    # Month is valid, doc type code is not in DOC_TYPE_MAP → partial result
    result = parse_filename_metadata("DEC-U2-XYZ-24-25-40.pdf")
    assert result["doc_month"] == "December"
    assert result["doc_unit"] == "U2"
    assert "doc_type" not in result


# ── Filename overrides LLM extraction ────────────────────────────────────────

def test_filename_fields_are_ground_truth():
    """
    If the LLM says doc_type='Purchase Invoice' but the filename says 'PUR'
    (Purchase Order), the filename wins. This test verifies parse_filename_metadata()
    returns the authoritative value — the caller in ingestion_pipeline.py must
    apply the override.
    """
    llm_extracted = {"doc_type": "Purchase Invoice", "party_name": "ABC Ltd"}
    filename_meta = parse_filename_metadata("DEC-U2-PUR-24-25-40.pdf")
    # Simulate the override: filename fields win
    merged = {**llm_extracted, **filename_meta}
    assert merged["doc_type"] == "Purchase Order"
    assert merged["party_name"] == "ABC Ltd"  # non-filename fields preserved


# ── Map consistency ───────────────────────────────────────────────────────────

def test_month_map_has_all_12_months():
    assert len(MONTH_MAP) == 12


def test_doc_type_map_has_all_9_types():
    assert len(DOC_TYPE_MAP) == 9


def test_month_name_map_includes_abbreviations():
    assert MONTH_NAME_MAP["jan"] == "January"
    assert MONTH_NAME_MAP["sep"] == "September"
    assert MONTH_NAME_MAP["sept"] == "September"
    assert MONTH_NAME_MAP["dec"] == "December"


def test_month_map_and_month_name_map_consistent():
    """All MONTH_MAP full names must appear as values in MONTH_NAME_MAP."""
    month_name_values = set(MONTH_NAME_MAP.values())
    for abbrev, full_name in MONTH_MAP.items():
        assert full_name in month_name_values, (
            f"{full_name} (from MONTH_MAP[{abbrev}]) not found in MONTH_NAME_MAP values"
        )
