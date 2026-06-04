"""
Filename metadata parser for structured pharma document filenames.

Standard format:  {MONTH}-{UNIT}-{DOC_TYPE}-{FY_START}-{FY_END}-{SERIAL}.pdf
                  e.g.  DEC-U2-PUR-24-25-40.pdf

JV/short format:  {DOC_TYPE}-{FY_START}-{FY_END}-{SERIAL}.pdf
                  e.g.  JV-24-25-5.pdf

This module is the single source of truth for MONTH_MAP and DOC_TYPE_MAP.
llm_client.py and rag_pipeline.py import from here — do not redefine these maps.
"""

import re
from typing import Dict, Any

# Abbreviation → full month name (stored in documents.doc_month)
MONTH_MAP: Dict[str, str] = {
    "JAN": "January", "FEB": "February", "MAR": "March", "APR": "April",
    "MAY": "May",     "JUN": "June",     "JUL": "July",  "AUG": "August",
    "SEP": "September", "OCT": "October", "NOV": "November", "DEC": "December",
}

# Document type code → human-readable label (stored in documents.doc_type)
DOC_TYPE_MAP: Dict[str, str] = {
    "PUR": "Purchase Order",
    "RM":  "Goods Receipt Note",
    "DN":  "Delivery Note",
    "JV":  "Journal Voucher",
    "INV": "Sales Invoice",
    "SO":  "Sales Order",
    "PI":  "Purchase Invoice",
    "CR":  "Credit Note",
    "DR":  "Debit Note",
}

# All forms a month name may appear as in a user query → canonical full name.
# Used by rag_pipeline.py for natural-language month extraction from queries.
MONTH_NAME_MAP: Dict[str, str] = {
    "january": "January",   "jan": "January",
    "february": "February", "feb": "February",
    "march": "March",       "mar": "March",
    "april": "April",       "apr": "April",
    "may": "May",
    "june": "June",         "jun": "June",
    "july": "July",         "jul": "July",
    "august": "August",     "aug": "August",
    "september": "September", "sep": "September", "sept": "September",
    "october": "October",   "oct": "October",
    "november": "November", "nov": "November",
    "december": "December", "dec": "December",
}

_UNIT_RE = re.compile(r'^U\d+$', re.IGNORECASE)
_TWO_DIGIT_RE = re.compile(r'^\d{2}$')


def parse_filename_metadata(filename: str) -> Dict[str, Any]:
    """
    Parse a structured pharma document filename into metadata fields.

    Returns a dict with any subset of:
        doc_month, doc_unit, doc_type, fiscal_year, serial_no

    Returns an empty dict for unrecognised formats. Never raises.
    """
    if not filename:
        return {}

    try:
        name = re.sub(r'\.(pdf|xlsx?|docx?|csv|txt)$', '', filename, flags=re.IGNORECASE)
        parts = name.split('-')
        if not parts:
            return {}

        first = parts[0].upper()

        if first in MONTH_MAP:
            return _parse_standard_format(parts)

        if first in DOC_TYPE_MAP:
            return _parse_short_format(parts)

        return {}

    except Exception:
        return {}


def _parse_standard_format(parts: list) -> Dict[str, Any]:
    """Parse {MONTH}-{UNIT}-{DOC_TYPE}-{FY_START}-{FY_END}-{SERIAL}."""
    result: Dict[str, Any] = {}

    result["doc_month"] = MONTH_MAP[parts[0].upper()]

    if len(parts) >= 2 and _UNIT_RE.match(parts[1]):
        result["doc_unit"] = parts[1].upper()

    if len(parts) >= 3 and parts[2].upper() in DOC_TYPE_MAP:
        result["doc_type"] = DOC_TYPE_MAP[parts[2].upper()]

    if len(parts) >= 5 and _TWO_DIGIT_RE.match(parts[3]) and _TWO_DIGIT_RE.match(parts[4]):
        result["fiscal_year"] = f"FY 20{parts[3]}-20{parts[4]}"

    if len(parts) >= 6 and parts[5].isdigit():
        result["serial_no"] = int(parts[5])

    return result


def _parse_short_format(parts: list) -> Dict[str, Any]:
    """Parse {DOC_TYPE}-{FY_START}-{FY_END}-{SERIAL} (JV and similar)."""
    result: Dict[str, Any] = {}

    result["doc_type"] = DOC_TYPE_MAP[parts[0].upper()]

    if len(parts) >= 3 and _TWO_DIGIT_RE.match(parts[1]) and _TWO_DIGIT_RE.match(parts[2]):
        result["fiscal_year"] = f"FY 20{parts[1]}-20{parts[2]}"

    if len(parts) >= 4 and parts[3].isdigit():
        result["serial_no"] = int(parts[3])

    return result
