"""
OCR quality scoring for text chunks.

Score range: 0.0 (garbage/empty) to 1.0 (clean readable text).
The threshold is controlled by OCR_QUALITY_MIN env var (default 0.3).
Chunks below threshold are excluded from retrieval but not deleted — they
are flagged so operators can identify documents that need re-OCR.

Calibration note (from Phase 0 guidance):
  Run on 20-30 sample docs, plot the distribution, and set OCR_QUALITY_MIN
  just below the cluster of legitimate low-quality docs (not blank/corrupt ones).
  Assign a team member to own this number before backfill runs.
"""

import re
import unicodedata

_GARBAGE_RE = re.compile(r'[^\x20-\x7E\u00A0-\u024F\u0900-\u097F\n\r\t]')
_WORD_RE = re.compile(r'\b[a-zA-Z]{2,}\b')


def score_text(text: str) -> float:
    """
    Return a quality score in [0.0, 1.0] for a chunk of OCR output.

    Factors:
    - Empty / too short → 0.0
    - Ratio of printable characters vs total
    - Presence of real words (letter runs ≥ 2 chars)
    - Penalty for high garbage-character density
    """
    if not text or len(text.strip()) < 10:
        return 0.0

    text = text.strip()
    total_chars = len(text)

    # Count garbage characters (outside printable ASCII + Latin extended + Devanagari)
    garbage_count = len(_GARBAGE_RE.findall(text))
    garbage_ratio = garbage_count / total_chars

    # Count printable non-whitespace characters
    printable = sum(1 for c in text if not c.isspace() and unicodedata.category(c)[0] != 'C')
    printable_ratio = printable / total_chars if total_chars > 0 else 0.0

    # Word presence: real alphabetic words ≥ 2 chars
    words = _WORD_RE.findall(text)
    word_density = min(len(words) / max(total_chars / 10, 1), 1.0)

    # Penalty for extremely high symbol density
    symbol_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
    symbol_ratio = symbol_count / total_chars

    score = (
        0.4 * printable_ratio
        + 0.4 * word_density
        - 0.3 * garbage_ratio
        - 0.1 * max(0.0, symbol_ratio - 0.4)  # allow up to 40% symbols (numbers, punctuation)
    )

    return max(0.0, min(1.0, round(score, 3)))


def score_page_html(html: str) -> float:
    """
    Score an entire page's HTML output from DotsOCR.
    Strips tags first, then scores the visible text.
    Returns 0.0 for empty/blank pages.
    """
    if not html:
        return 0.0
    # Strip HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return score_text(text)
