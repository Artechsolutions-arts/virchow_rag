"""Tests for src/ingestion/html_table_parser.py"""

import pytest
from src.ingestion.html_table_parser import parse_tables, TableParseResult, _parse_amount


# ── _parse_amount helper ──────────────────────────────────────────────────────

def test_parse_amount_plain_number():
    assert _parse_amount("1000.00") == 1000.0


def test_parse_amount_with_rupee_symbol():
    assert _parse_amount("₹1,25,430.00") == pytest.approx(125430.0)


def test_parse_amount_with_commas():
    assert _parse_amount("1,00,000") == pytest.approx(100000.0)


def test_parse_amount_empty():
    assert _parse_amount("") is None


def test_parse_amount_none():
    assert _parse_amount(None) is None


def test_parse_amount_non_numeric():
    assert _parse_amount("N/A") is None


def test_parse_amount_trailing_dot():
    assert _parse_amount("5000.") == pytest.approx(5000.0)


# ── parse_tables — empty / malformed inputs ───────────────────────────────────

def test_empty_html_returns_empty_result():
    result = parse_tables("")
    assert result.line_items == []
    assert result.total_amount is None


def test_none_html_returns_empty_result():
    result = parse_tables(None)
    assert result.line_items == []
    assert result.total_amount is None


def test_html_without_tables_returns_empty():
    result = parse_tables("<html><body><p>No tables here.</p></body></html>")
    assert result.line_items == []
    assert result.total_amount is None


def test_malformed_html_does_not_raise():
    result = parse_tables("<table><tr><td>Unclosed")
    assert isinstance(result, TableParseResult)


# ── parse_tables — well-formed invoice table ─────────────────────────────────

_INVOICE_HTML = """
<table>
  <tr>
    <th>Description</th>
    <th>HSN Code</th>
    <th>Qty</th>
    <th>UOM</th>
    <th>Rate</th>
    <th>Amount</th>
    <th>GST %</th>
  </tr>
  <tr>
    <td>Paracetamol API</td>
    <td>29242990</td>
    <td>100</td>
    <td>KG</td>
    <td>500.00</td>
    <td>50000.00</td>
    <td>18</td>
  </tr>
  <tr>
    <td>Aspirin</td>
    <td>29262000</td>
    <td>50</td>
    <td>KG</td>
    <td>800.00</td>
    <td>40000.00</td>
    <td>12</td>
  </tr>
  <tr>
    <td>Sub Total</td>
    <td></td><td></td><td></td><td></td>
    <td>90000.00</td>
    <td></td>
  </tr>
  <tr>
    <td>Total GST</td>
    <td></td><td></td><td></td><td></td>
    <td>13800.00</td>
    <td></td>
  </tr>
  <tr>
    <td>Grand Total</td>
    <td></td><td></td><td></td><td></td>
    <td>103800.00</td>
    <td></td>
  </tr>
</table>
"""


def test_invoice_line_item_count():
    result = parse_tables(_INVOICE_HTML)
    assert len(result.line_items) == 2


def test_invoice_first_line_item_description():
    result = parse_tables(_INVOICE_HTML)
    assert result.line_items[0]["description"] == "Paracetamol API"


def test_invoice_line_item_amounts():
    result = parse_tables(_INVOICE_HTML)
    assert result.line_items[0]["amount"] == pytest.approx(50000.0)
    assert result.line_items[1]["amount"] == pytest.approx(40000.0)


def test_invoice_total_amount():
    result = parse_tables(_INVOICE_HTML)
    assert result.total_amount == pytest.approx(103800.0)


def test_invoice_net_amount():
    result = parse_tables(_INVOICE_HTML)
    assert result.net_amount == pytest.approx(90000.0)


def test_invoice_tax_amount():
    result = parse_tables(_INVOICE_HTML)
    assert result.tax_amount == pytest.approx(13800.0)


def test_invoice_line_numbers_sequential():
    result = parse_tables(_INVOICE_HTML)
    for i, item in enumerate(result.line_items, start=1):
        assert item["line_number"] == i


def test_invoice_hsn_code_extracted():
    result = parse_tables(_INVOICE_HTML)
    assert result.line_items[0]["hsn_code"] == "29242990"


def test_invoice_quantity_extracted():
    result = parse_tables(_INVOICE_HTML)
    assert result.line_items[0]["quantity"] == pytest.approx(100.0)


# ── parse_tables — no labeled total row, fallback to last row ─────────────────

_NO_LABEL_HTML = """
<table>
  <tr><th>Item</th><th>Amount</th></tr>
  <tr><td>Product A</td><td>5000.00</td></tr>
  <tr><td>Product B</td><td>3000.00</td></tr>
  <tr><td></td><td>8000.00</td></tr>
</table>
"""


def test_fallback_total_from_last_row():
    result = parse_tables(_NO_LABEL_HTML)
    # The last row has 8000.00 — should be picked up as total via fallback
    assert result.total_amount == pytest.approx(8000.0)


# ── parse_tables — td-based header detection ─────────────────────────────────

_TD_HEADER_HTML = """
<table>
  <tr>
    <td>Particulars</td>
    <td>Quantity</td>
    <td>Rate</td>
    <td>Amount</td>
  </tr>
  <tr>
    <td>Chemical X</td>
    <td>200</td>
    <td>150.00</td>
    <td>30000.00</td>
  </tr>
  <tr>
    <td>Grand Total</td>
    <td></td>
    <td></td>
    <td>30000.00</td>
  </tr>
</table>
"""


def test_td_header_line_item_extracted():
    result = parse_tables(_TD_HEADER_HTML)
    assert len(result.line_items) == 1
    assert result.line_items[0]["description"] == "Chemical X"


def test_td_header_total_detected():
    result = parse_tables(_TD_HEADER_HTML)
    assert result.total_amount == pytest.approx(30000.0)


# ── parse_tables — multiple tables ───────────────────────────────────────────

_MULTI_TABLE_HTML = """
<table>
  <tr><th>Description</th><th>Amount</th></tr>
  <tr><td>Item 1</td><td>1000.00</td></tr>
  <tr><td>Grand Total</td><td>1000.00</td></tr>
</table>
<table>
  <tr><th>Description</th><th>Amount</th></tr>
  <tr><td>Item 2</td><td>2000.00</td></tr>
</table>
"""


def test_multi_table_line_items_combined():
    result = parse_tables(_MULTI_TABLE_HTML)
    assert len(result.line_items) == 2


def test_multi_table_first_grand_total_wins():
    # total_amount is set on first detection and not overwritten
    result = parse_tables(_MULTI_TABLE_HTML)
    assert result.total_amount == pytest.approx(1000.0)
