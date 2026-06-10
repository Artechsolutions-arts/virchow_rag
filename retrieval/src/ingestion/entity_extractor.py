"""
LLM entity extraction for pharma document header fields.

Extracts: party_name, party_gstin, doc_date, doc_number,
          payment_terms, ref_doc_number.

Financial amounts (total_amount, tax_amount, net_amount) come from
html_table_parser.py — NOT from this module. The LLM is unreliable for numbers.

Retry strategy (TODO-1):
  1. Full prompt, up to 3 attempts
  2. On 3rd failure, simplified prompt for just party_name + doc_number
  3. If simplified also fails → return {} and log to failed_extractions table

The caller (ingestion_pipeline.py) is responsible for logging failures.

Party name normalization (TODO-5):
  normalize_party_name(name) → canonical form for SQL aggregation.
  Raw value is preserved in party_name; canonical goes to party_name_canonical.
"""

import html as _html
import json
import logging
import re
from typing import Any, Optional

import httpx

from src.config import cfg

logger = logging.getLogger(__name__)

# GSTIN: 2-digit state code + 10-char PAN + 1-digit entity + Z + 1 alphanumeric
_GSTIN_RE = re.compile(r'\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b')
_DATE_RE  = re.compile(
    r'\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}|\d{4}[-/\.]\d{2}[-/\.]\d{2})\b'
)

_FULL_PROMPT = """\
You are extracting structured metadata from a pharma company document.
The document text is below. Extract ONLY these fields as a JSON object.
Do not compute or summarize — extract exact values that appear in the document.
If a field is not present, use null.

Fields to extract:
- party_name: the other party's company name (vendor or customer)
- party_gstin: their GST Identification Number (format: 2 digits + 5 letters + 4 digits + 1 letter + Z + 1 alphanumeric)
- doc_date: document date in YYYY-MM-DD format if possible, else as written
- doc_number: the document's own reference number (invoice no., PO no., GRN no., etc.)
- payment_terms: payment terms if stated (e.g. "Net 30", "Immediate")
- ref_doc_number: any referenced document number (e.g. PO number mentioned in an invoice)

Respond with ONLY a JSON object. No explanation. No markdown. No extra keys.

Document text:
{text}"""

_SIMPLE_PROMPT = """\
Extract from this document:
- party_name: the vendor or customer company name
- doc_number: the document's reference number (invoice/PO/GRN number)

Respond with ONLY a JSON object. No explanation.

Document text:
{text}"""


# Legal suffixes stripped from the END of the name only (order: longest first)
_LEGAL_SUFFIXES = re.compile(
    r'\s+(PVT\.?\s*LTD\.?|PRIVATE\s+LIMITED|LIMITED|LTD\.?|LLP|INC\.?|'
    r'CORPORATION|CORP\.?|CO\.?\s*LTD\.?)\s*$',
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r'[^\w\s]')
_SPACE_RE = re.compile(r'\s+')


def normalize_party_name(name: str | None) -> str | None:
    """
    Return a canonical form of a party/vendor name for SQL aggregation.

    Steps:
      1. Upper-case
      2. Strip "M/S" prefix common in Indian vendor names
      3. Strip punctuation (keep word chars and spaces)
      4. Strip legal suffixes from the end (PVT LTD, LIMITED, LLP, etc.)
      5. Collapse whitespace and strip

    'M/s ABC Traders Pvt. Ltd.' → 'ABC TRADERS'
    'ABC Traders Private Limited' → 'ABC TRADERS'
    'EMNAR PHARMA PRIVATE LIMITED' → 'EMNAR PHARMA'
    Returns None if input is None or blank after normalization.
    """
    if not name or not name.strip():
        return None
    canonical = name.upper()
    canonical = re.sub(r'^M/S\.?\s*', '', canonical)
    canonical = _PUNCT_RE.sub(' ', canonical)
    # Strip trailing legal suffixes repeatedly (e.g. "Pvt. Ltd." needs two passes)
    prev = None
    while prev != canonical:
        prev = canonical
        canonical = _LEGAL_SUFFIXES.sub('', canonical)
    canonical = _SPACE_RE.sub(' ', canonical).strip()
    return canonical or None


def _call_llm(prompt: str) -> Optional[str]:
    """Call the local Ollama LLM. Returns raw text or None on failure."""
    try:
        response = httpx.post(
            f"{cfg.llm_url}/api/generate",
            json={
                "model": cfg.effective_llm_model(),
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 512},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        logger.warning("[EntityExtractor] LLM call failed: %s", e)
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract a JSON object from LLM output. Returns None if unparseable."""
    if not text:
        return None
    # Strip markdown fences if present
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text.strip(), flags=re.MULTILINE)
    # Find the first { ... } block
    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _validate_and_clean(data: dict) -> dict:
    """Validate extracted fields, strip invalid values."""
    result: dict[str, Any] = {}

    party_name = data.get("party_name")
    if party_name and isinstance(party_name, str) and len(party_name.strip()) >= 2:
        result["party_name"] = _html.escape(party_name.strip()[:255])

    gstin = data.get("party_gstin") or ""
    if isinstance(gstin, str):
        m = _GSTIN_RE.search(gstin.upper())
        if m:
            result["party_gstin"] = m.group(0)  # regex-validated, safe

    doc_date = data.get("doc_date")
    if doc_date and isinstance(doc_date, str):
        m = _DATE_RE.search(doc_date)
        if m:
            try:
                from dateutil import parser as _dateutil_parser
                result["doc_date"] = _dateutil_parser.parse(m.group(0), dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                pass  # unparseable date — omit rather than crash the upsert

    doc_number = data.get("doc_number")
    if doc_number and isinstance(doc_number, str) and doc_number.strip():
        result["doc_number"] = _html.escape(doc_number.strip()[:100])

    payment_terms = data.get("payment_terms")
    if payment_terms and isinstance(payment_terms, str) and payment_terms.strip():
        result["payment_terms"] = _html.escape(payment_terms.strip()[:100])

    ref_doc_number = data.get("ref_doc_number")
    if ref_doc_number and isinstance(ref_doc_number, str) and ref_doc_number.strip():
        result["ref_doc_number"] = _html.escape(ref_doc_number.strip()[:100])

    return result


def extract_entities(text: str) -> tuple:
    """
    Extract header entities from document text.

    Returns (fields_dict, error_message).
    - On success: (non-empty dict, None)
    - On partial success (simplified fallback worked): (partial dict, None)
    - On total failure: ({}, error_message_string)

    The caller decides whether to log the failure to failed_extractions.
    """
    if not text or not text.strip():
        return {}, "empty document text"

    # Truncate to avoid LLM context overflow (keep first 3000 chars — the header)
    truncated = text[:3000]

    # ── Attempt 1-3: full prompt ──────────────────────────────────────────────
    for attempt in range(1, 4):
        raw = _call_llm(_FULL_PROMPT.format(text=truncated))
        parsed = _parse_json_response(raw) if raw else None
        if parsed:
            result = _validate_and_clean(parsed)
            if result:
                logger.debug(
                    "[EntityExtractor] Full extraction succeeded on attempt %d", attempt
                )
                return result, None
        logger.warning(
            "[EntityExtractor] Full extraction attempt %d/%d failed (raw=%r)",
            attempt, 3, (raw or "")[:200]
        )

    # ── Attempt 4: simplified prompt (party_name + doc_number only) ──────────
    logger.warning("[EntityExtractor] Falling back to simplified extraction prompt")
    raw = _call_llm(_SIMPLE_PROMPT.format(text=truncated))
    parsed = _parse_json_response(raw) if raw else None
    if parsed:
        result = _validate_and_clean(parsed)
        if result:
            logger.info("[EntityExtractor] Simplified fallback succeeded")
            return result, None

    error = "all 4 extraction attempts failed (3 full + 1 simplified)"
    logger.error("[EntityExtractor] %s", error)
    return {}, error
