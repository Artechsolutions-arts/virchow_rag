"""OCR text metadata extractor.

Parses the markdown produced by DotsOCR and extracts structured fields:
  party_name, party_gstin, doc_date, doc_month, doc_number, doc_type,
  doc_unit, total_amount, tax_amount, net_amount, payment_terms,
  ref_doc_number, extracted_text.

All fields are optional — any that cannot be confidently extracted are
omitted from the result dict.  The extractor never raises.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional

# ── Compiled patterns ─────────────────────────────────────────────────────────

# GSTIN: ideally 15 chars, but OCR frequently drops/swaps one character.
# Accept 14-15 uppercase-alphanumeric chars that start with two digits.
# The separator after "GSTIN" is optional — some OCR outputs omit the colon.
_GSTIN_RE = re.compile(
    r"GSTIN\s*[:/]?\s*([0-9]{2}[A-Z0-9]{12,13})\b",
    re.IGNORECASE,
)

# Dates: DD/MM/YYYY or DD-MM-YYYY
_DATE_RE = re.compile(
    r"(?:Invoice\s+Date|Date|Dt\.?|Dated)\s*[:/]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
    re.IGNORECASE,
)
_DATE_BARE_RE = re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b")

# Invoice / challan / PO number — look for labelled fields first
_DOC_NO_RE = re.compile(
    r"(?:Invoice\s+No\.?|Challan\s+No\.?|Bill\s+No\.?|Order\s+No\.?|"
    r"Voucher\s+No\.?|Doc\s+No\.?|Document\s+No\.?|Receipt\s+No\.?)\s*[:/]\s*([^\n,|<]{1,40})",
    re.IGNORECASE,
)

# Grand Total (prefer over sub-totals)
# Matches both plain text ("Grand Total : 1234.00") and HTML table variants
# where the value lands in the next cell ("Grand Total :</td><td>1234.00").
_GRAND_TOTAL_RE = re.compile(
    r"Grand\s+Total\s*[:/]?\s*(?:<[^>]*>\s*)*(?:INR|USD|Rs\.?)?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# Require explicit `:` or `=` separator to avoid matching "TOTAL USD <row-index>".
_TOTAL_RE = re.compile(
    r"(?:^|\s)Total\s*[:/=]\s*(?:<[^>]*>\s*)*(?:INR|USD|Rs\.?)?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE | re.MULTILINE,
)
# Export invoice line-item total: "Rate USD <rate> <total>"  e.g. "41.50  124500.00"
_RATE_USD_TOTAL_RE = re.compile(
    r"\bRate\s+USD\b\s+([\d.]+)\s+([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# Last-resort: the final numeric <td> in the last product table row.
# Works for export invoices where "TOTAL USD" is the last column and there's
# no separate Grand Total summary cell.
_HTML_LAST_ROW_AMOUNT_RE = re.compile(
    r"<td[^>]*>\s*([\d,]+\.\d{2})\s*</td>\s*</tr>",
    re.IGNORECASE,
)

# Tax amounts: IGST / CGST / SGST.
# Require explicit `:` separator to prevent matching GSTINs ("GST : 24AAFC...")
# and percentage rates ("IGST 18.00%"). Negative lookahead ensures the captured
# value is not immediately followed by `%` or an alpha char (GSTIN continuation).
_TAX_RE = re.compile(
    r"(?:IGST|CGST|SGST)\s*(?:Amount\s*)?[:/]\s*([\d,]+(?:\.\d{1,2})?)(?![\s]*[%A-Za-z])",
    re.IGNORECASE,
)

# Net / taxable amount
_NET_RE = re.compile(
    r"(?:Net\s+Amount|Taxable\s+Amount|Sub[\s-]?Total)\s*[:/]?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Payment terms
_PAYMENT_RE = re.compile(
    r"Payment\s+Terms?\s*[:/]\s*(.+?)(?:\n|<|$)",
    re.IGNORECASE,
)

# Reference document number (PO, LR, etc.)
_REF_RE = re.compile(
    r"(?:PO\s+No\.?|L\.?R\.?\s*No\.?|Ref\.?\s+No\.?|Reference\s+No\.?)\s*[:/]\s*([^\n,|<]{1,40})",
    re.IGNORECASE,
)

# Company name heuristic: ALL-CAPS line containing LIMITED / PVT / LTD / CO.
_COMPANY_RE = re.compile(
    r"^([A-Z][A-Z0-9 &.,\-()]{4,80}"
    r"(?:LIMITED|PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|LTD\.?|CO\.?|CORPORATION|INDUSTRIES|PHARMA|LABS?|"
    r"CHEMICALS?|ENTERPRISE|EXPORTS?|IMPORTS?|TRADING|TRADERS?)[^\n]{0,40})$",
    re.MULTILINE,
)

# Unit designator: standalone "UNIT-2", "UNIT 2", or inside a party name.
_UNIT_RE = re.compile(r"\bUNIT[-\s]?(\d+)\b", re.IGNORECASE)

# Document type keywords — ordered most-specific first.
_DOC_TYPE_PATTERNS = [
    (re.compile(r"\bEXPORT\s+INVOICE\b", re.IGNORECASE),         "Export Invoice"),
    (re.compile(r"\bINVOICE\s+CUM\s+CHALLAN\b", re.IGNORECASE),  "Invoice Cum Challan"),
    (re.compile(r"\bSALES\s+DEBIT\s+NOTE\b", re.IGNORECASE),     "Sales Debit Note"),
    (re.compile(r"\bSALES\s+CREDIT\s+NOTE\b", re.IGNORECASE),    "Sales Credit Note"),
    (re.compile(r"\bDEBIT\s+NOTE\b", re.IGNORECASE),             "Debit Note"),
    (re.compile(r"\bCREDIT\s+NOTE\b", re.IGNORECASE),            "Credit Note"),
    (re.compile(r"\bDELIVERY\s+CHALLAN\b", re.IGNORECASE),       "Delivery Challan"),
    (re.compile(r"\bPURCHASE\s+ORDER\b", re.IGNORECASE),         "Purchase Order"),
    (re.compile(r"\bSALES\s+ORDER\b", re.IGNORECASE),            "Sales Order"),
    (re.compile(r"\bPROFORMA\s+INVOICE\b", re.IGNORECASE),       "Proforma Invoice"),
    (re.compile(r"\bTAX\s+INVOICE\b", re.IGNORECASE),            "Tax Invoice"),
    (re.compile(r"\bINVOICE\b", re.IGNORECASE),                  "Invoice"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_amount(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(raw: str) -> Optional[str]:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags and HTML comments; collapse runs of whitespace."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


# ── Public API ────────────────────────────────────────────────────────────────

def extract_from_ocr(ocr_text: str) -> Dict:
    """Extract structured metadata from DotsOCR markdown output.

    Returns a dict with any subset of:
      party_name, party_gstin, doc_date, doc_month, doc_number, doc_type,
      doc_unit, total_amount, tax_amount, net_amount, payment_terms,
      ref_doc_number, extracted_text.
    """
    if not ocr_text:
        return {}

    result: Dict = {}
    # raw text preserved for extracted_text; tag-stripped version used for regexes
    clean = re.sub(r"<!--.*?-->", "", ocr_text, flags=re.DOTALL)
    flat = _strip_html(clean)

    # extracted_text: strip page separators and whitespace-collapse (keep HTML for display)
    result["extracted_text"] = re.sub(r"\n{3,}", "\n\n", clean).strip()

    # GSTIN (issuer's — first match is typically the issuer's own block)
    m = _GSTIN_RE.search(flat)
    if m:
        result["party_gstin"] = m.group(1).upper().strip()

    # Company / party name
    companies = _COMPANY_RE.findall(flat)
    if companies:
        result["party_name"] = companies[0].strip()

    # Document date
    m = _DATE_RE.search(flat)
    if m:
        parsed = _parse_date(m.group(1))
        if parsed:
            result["doc_date"] = parsed
    if "doc_date" not in result:
        m = _DATE_BARE_RE.search(flat)
        if m:
            parsed = _parse_date(m.group(1))
            if parsed:
                result["doc_date"] = parsed

    # doc_month derived from doc_date
    if "doc_date" in result:
        try:
            dt = datetime.strptime(result["doc_date"], "%Y-%m-%d")
            result["doc_month"] = dt.strftime("%B")  # e.g. "April"
        except ValueError:
            pass

    # Document number
    m = _DOC_NO_RE.search(flat)
    if m:
        result["doc_number"] = m.group(1).strip().rstrip(".,;")

    # Document type — first keyword match wins
    for pattern, label in _DOC_TYPE_PATTERNS:
        if pattern.search(flat):
            result["doc_type"] = label
            break

    # Unit designator (UNIT-1, UNIT-2, …)
    m = _UNIT_RE.search(flat)
    if m:
        result["doc_unit"] = f"U{m.group(1)}"

    # Grand total (preferred) → plain Total: → export-invoice Rate USD row
    m = _GRAND_TOTAL_RE.search(flat)
    if m:
        amt = _parse_amount(m.group(1))
        if amt is not None:
            result["total_amount"] = amt
    if "total_amount" not in result:
        m = _TOTAL_RE.search(flat)
        if m:
            amt = _parse_amount(m.group(1))
            if amt is not None:
                result["total_amount"] = amt
    if "total_amount" not in result:
        # Export invoices with "Rate USD ... <total>" table rows
        m = _RATE_USD_TOTAL_RE.search(flat)
        if m:
            amt = _parse_amount(m.group(2))  # group(1)=rate, group(2)=total
            if amt is not None:
                result["total_amount"] = amt
    if "total_amount" not in result:
        # Last resort: final numeric <td> in the last product table row
        matches = _HTML_LAST_ROW_AMOUNT_RE.findall(clean)
        if matches:
            amt = _parse_amount(matches[-1])
            if amt is not None:
                result["total_amount"] = amt

    # Tax amount (sum IGST + CGST + SGST if multiple)
    tax_matches = _TAX_RE.findall(flat)
    if tax_matches:
        amounts = [_parse_amount(v) for v in tax_matches if _parse_amount(v) is not None]
        if amounts:
            result["tax_amount"] = sum(amounts)

    # Net / taxable amount
    m = _NET_RE.search(flat)
    if m:
        amt = _parse_amount(m.group(1))
        if amt is not None:
            result["net_amount"] = amt

    # Payment terms
    m = _PAYMENT_RE.search(flat)
    if m:
        result["payment_terms"] = m.group(1).strip().rstrip(".,;")

    # Reference document number
    m = _REF_RE.search(flat)
    if m:
        result["ref_doc_number"] = m.group(1).strip().rstrip(".,;")

    return result
