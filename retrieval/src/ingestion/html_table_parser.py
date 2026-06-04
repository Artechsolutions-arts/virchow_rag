"""
HTML table parser for DotsOCR output.

DotsOCR in prompt_layout_all_en mode returns HTML with <table> elements.
This module extracts:
  - Line items (one row per product/service)
  - Financial totals (total_amount, tax_amount, net_amount)

Design constraint: amounts come from the HTML table, NOT from LLM extraction.
The table parser is authoritative for all financial figures (zero-tolerance system).

Returns None for amounts if no grand total row is found — callers must log this
as an incomplete extraction, not silently zero-fill.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# Column header patterns → canonical field name
_HEADER_PATTERNS = {
    "description":     re.compile(r'descri|item|particular|product|material|chemical', re.I),
    "hsn_code":        re.compile(r'hsn|sac|code', re.I),
    "quantity":        re.compile(r'qty|quantity|units?', re.I),
    "unit_of_measure": re.compile(r'\buom\b|unit\s*of\s*meas', re.I),
    "unit_price":      re.compile(r'rate|unit\s*price|price\s*per', re.I),
    "amount":          re.compile(r'^amount$|taxable\s*value|gross\s*amount', re.I),
    "tax_rate":        re.compile(r'gst\s*%|tax\s*rate|cgst\s*\+\s*sgst', re.I),
}

# Grand total row detection
_TOTAL_ROW_RE = re.compile(r'grand\s*total|total\s*amount|net\s*payable|invoice\s*total', re.I)
_SUB_TOTAL_RE = re.compile(r'sub\s*total|subtotal|taxable\s*total', re.I)
_TAX_TOTAL_RE = re.compile(r'total\s*gst|total\s*tax|tax\s*amount', re.I)


def _parse_amount(text: str) -> Optional[float]:
    """Extract a numeric amount from a cell string. Returns None if unparseable."""
    if not text:
        return None
    # Remove currency symbols, commas, spaces
    cleaned = re.sub(r'[₹$,\s]', '', text.strip())
    # Remove trailing dots
    cleaned = cleaned.rstrip('.')
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _map_headers(header_cells: list) -> dict:
    """Map column index → field name based on header text patterns."""
    mapping = {}
    for idx, cell_text in enumerate(header_cells):
        cell_text = cell_text.strip()
        for field, pattern in _HEADER_PATTERNS.items():
            if pattern.search(cell_text):
                if field not in mapping.values():  # first match wins per field
                    mapping[idx] = field
                break
    return mapping


def _find_amount_in_row(cell_texts: list, col_map: dict, field: str) -> Optional[float]:
    """
    Extract a numeric amount from a total/subtotal row.

    Strategy:
      1. Use the col_map column for `field` if present.
      2. Fall back to the last non-empty cell that parses as a number.
    """
    # Try mapped column first
    for col_idx, col_field in col_map.items():
        if col_field == field and col_idx < len(cell_texts):
            amount = _parse_amount(cell_texts[col_idx])
            if amount is not None:
                return amount

    # Fall back to last parseable numeric value in the row
    for text in reversed(cell_texts):
        amount = _parse_amount(text)
        if amount is not None and amount > 0:
            return amount

    return None


class TableParseResult:
    __slots__ = ("line_items", "total_amount", "tax_amount", "net_amount")

    def __init__(self):
        self.line_items: list = []
        self.total_amount: Optional[float] = None
        self.tax_amount: Optional[float] = None
        self.net_amount: Optional[float] = None


def parse_tables(html: str) -> TableParseResult:
    """
    Parse all tables in a DotsOCR HTML page.
    Returns a TableParseResult with line_items and financial totals.

    On malformed HTML or no tables found: returns an empty result (no exception).
    """
    result = TableParseResult()

    if not html or not html.strip():
        return result

    if not _BS4_AVAILABLE:
        logger.error(
            "[HTMLParser] beautifulsoup4 is not installed. "
            "Install it: pip install beautifulsoup4"
        )
        return result

    try:
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")

        for table in tables:
            _extract_from_table(table, result)

    except Exception as e:
        logger.warning("[HTMLParser] Table parse failed: %s", e)

    return result


def _extract_from_table(table, result: TableParseResult) -> None:
    """Extract line items and totals from a single <table> element."""
    rows = table.find_all("tr")
    if not rows:
        return

    # Find header row (first <tr> with <th> cells, or first row)
    header_idx = 0
    col_map = {}
    for i, row in enumerate(rows):
        headers = row.find_all("th")
        if headers:
            header_idx = i
            col_map = _map_headers([h.get_text() for h in headers])
            break
        elif i == 0:
            # No <th> — treat first row as header if it looks like one
            cells = row.find_all("td")
            texts = [c.get_text() for c in cells]
            potential_map = _map_headers(texts)
            if len(potential_map) >= 2:
                header_idx = 0
                col_map = potential_map
                break

    line_number = 0
    for i, row in enumerate(rows):
        if i <= header_idx:
            continue

        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        cell_texts = [c.get_text(strip=True) for c in cells]
        first_cell = cell_texts[0] if cell_texts else ""

        # Detect grand total / sub-total rows
        if _TOTAL_ROW_RE.search(first_cell) or (
            len(cell_texts) > 1 and _TOTAL_ROW_RE.search(cell_texts[1])
        ):
            # Extract total_amount from "amount" column or last numeric column
            amount = _find_amount_in_row(cell_texts, col_map, "amount")
            if amount is not None and result.total_amount is None:
                result.total_amount = amount
            continue

        if _SUB_TOTAL_RE.search(first_cell):
            amount = _find_amount_in_row(cell_texts, col_map, "amount")
            if amount is not None and result.net_amount is None:
                result.net_amount = amount
            continue

        if _TAX_TOTAL_RE.search(first_cell):
            amount = _find_amount_in_row(cell_texts, col_map, "amount")
            if amount is not None and result.tax_amount is None:
                result.tax_amount = amount
            continue

        # Skip empty rows
        if not any(t for t in cell_texts):
            continue

        # Skip rows that are just serial numbers with no other data
        if len([t for t in cell_texts if t]) <= 1:
            continue

        # Build a line item
        line_number += 1
        item = {"line_number": line_number}

        for col_idx, field in col_map.items():
            if col_idx < len(cell_texts):
                raw = cell_texts[col_idx].strip()
                if field in ("quantity", "unit_price", "amount", "tax_rate"):
                    item[field] = _parse_amount(raw)
                else:
                    item[field] = raw or None

        # Must have at least a description or amount to be a real line item
        if item.get("description") or item.get("amount"):
            result.line_items.append(item)

    # If grand total not found as a labeled row, try the last row with a large amount
    if result.total_amount is None and result.line_items:
        last_row = rows[-1] if rows else None
        if last_row:
            last_cells = [c.get_text(strip=True) for c in last_row.find_all(["td", "th"])]
            # Try each cell for a parseable amount — take the largest
            amounts = [_parse_amount(t) for t in last_cells]
            amounts = [a for a in amounts if a is not None and a > 0]
            if amounts:
                result.total_amount = max(amounts)
