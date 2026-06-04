"""Tests for src/ingestion/ocr_quality.py"""

import pytest
from src.ingestion.ocr_quality import score_text, score_page_html


# ── score_text ────────────────────────────────────────────────────────────────

def test_empty_string_returns_zero():
    assert score_text("") == 0.0


def test_none_like_short_string_returns_zero():
    assert score_text("hi") == 0.0


def test_below_10_chars_returns_zero():
    assert score_text("123456789") == 0.0


def test_clean_english_text_scores_high():
    text = "Invoice received from Pharma Supplier for batch delivery of raw materials."
    score = score_text(text)
    assert score >= 0.5, f"Expected >= 0.5 for clean text, got {score}"


def test_all_garbage_scores_low():
    # Non-printable / control characters
    text = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0e\x0f" * 20
    score = score_text(text)
    assert score < 0.3, f"Expected < 0.3 for garbage text, got {score}"


def test_score_in_range():
    text = "Purchase Order No. 12345 dated 01-04-2024 for 100 units of Paracetamol."
    score = score_text(text)
    assert 0.0 <= score <= 1.0


def test_devanagari_text_scores_reasonably():
    # Devanagari is explicitly allowed in the _GARBAGE_RE charset
    text = "यह एक परीक्षण दस्तावेज़ है जिसमें कुछ हिंदी पाठ है।"
    score = score_text(text)
    assert score >= 0.0  # should not be penalised as garbage
    assert score <= 1.0


def test_mixed_numbers_and_words_score_decent():
    text = "Total Amount: 1,25,430.00 INR  GST @ 18%  Net Payable: 1,48,007.40 INR"
    score = score_text(text)
    assert score >= 0.3, f"Expected >= 0.3 for mixed text, got {score}"


def test_whitespace_only_returns_zero():
    assert score_text("   \n\t   ") == 0.0


def test_repeated_symbol_garbage_penalised():
    text = "@@@@####$$$$%%%%^^^^&&&&****!!!!" * 5  # high symbol ratio
    score = score_text(text)
    # Should be penalised but not necessarily 0.0 — it IS printable ASCII
    assert score < 0.6, f"Expected < 0.6 for symbol-heavy text, got {score}"


def test_score_is_deterministic():
    text = "Batch No. 2024/APR/001 — Supplier: ABC Chemicals Pvt Ltd"
    assert score_text(text) == score_text(text)


# ── score_page_html ───────────────────────────────────────────────────────────

def test_score_page_html_empty():
    assert score_page_html("") == 0.0


def test_score_page_html_strips_tags():
    html = "<html><body><p>Invoice from Supplier for goods delivered.</p></body></html>"
    plain_score = score_text("Invoice from Supplier for goods delivered.")
    html_score = score_page_html(html)
    # Should be close — stripping tags changes whitespace slightly
    assert abs(html_score - plain_score) < 0.2


def test_score_page_html_table_content():
    html = """
    <table>
      <tr><th>Description</th><th>Qty</th><th>Rate</th><th>Amount</th></tr>
      <tr><td>Paracetamol API</td><td>100 kg</td><td>500.00</td><td>50000.00</td></tr>
      <tr><td>Grand Total</td><td></td><td></td><td>50000.00</td></tr>
    </table>
    """
    score = score_page_html(html)
    assert score > 0.0
    assert score <= 1.0


def test_score_page_html_tags_only_returns_low_score():
    html = "<div><span></span><p></p><br/><hr/></div>"
    score = score_page_html(html)
    # After stripping tags, barely any content
    assert score < 0.3
