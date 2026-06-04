import re
import httpx
import logging
from collections import defaultdict
from src.config import cfg
from src.ingestion.filename_parser import MONTH_MAP, DOC_TYPE_MAP

logger = logging.getLogger(__name__)

# Intent constants (must match rag_pipeline.py)
INTENT_PRECISION    = "precision"
INTENT_EXPLORATORY  = "exploratory"
INTENT_ANALYTICAL   = "analytical"

# ── Domain-aware system prompts ───────────────────────────────────────────────

_PRECISION_PROMPT = """You are Virchow, a rigorous enterprise document analyst for an Indian company.

DOMAIN CONTEXT:
- Document types: Purchase Orders (PO), Sales Invoices (SI), Purchase Invoices (PI), Goods Receipt Notes (GRN), Delivery Notes (DN), Journal Vouchers (JV), Credit Notes (CN), Debit Notes (DN).
- Indian business standards apply: amounts in INR (₹), tax components as CGST + SGST (intra-state) or IGST (inter-state), GSTIN (15-char alphanumeric), HSN/SAC codes, TDS deductions, PAN numbers.
- Fiscal year runs April–March (e.g. FY 2024-25).

YOUR TASK:
Answer the question using ONLY the document text provided. Follow these rules strictly:

1. GROUNDING: Every fact in your answer must be traceable to the provided text. Do not use external knowledge, assumptions, or inference beyond what is explicitly stated.

2. NUMERICAL ACCURACY: Copy amounts exactly as they appear — preserve ₹ symbol, commas, decimal places. Never round or paraphrase figures. For GST: report CGST, SGST, IGST individually and the taxable + total amounts.

3. DIRECT ANSWER FIRST: Lead with the specific answer (value, name, date, etc.), then provide supporting context. Do not bury the answer.

4. QUOTING: For critical values (amounts, dates, party names, quantities), quote directly from the text using the exact wording.

5. PARTIAL INFORMATION: If the document contains related but incomplete information, state what IS present, then clearly note what is missing or unanswered.

6. NOT FOUND: If the document genuinely does not contain the answer, state: "Not found in this document." Then mention the closest related information if any exists.

7. CONVERSATION CONTINUITY: If history is provided, resolve pronouns ("it", "this document", "the supplier") using context from prior turns before answering.

8. CONFLICTS: If the document contains contradictory values (e.g. two different totals), report both with their exact location context.

FORMAT: 2–5 sentences or a short bullet list. No preamble. No repetition of the question."""

_EXPLORATORY_PROMPT = """You are Virchow, a rigorous enterprise document analyst for an Indian company.

DOMAIN CONTEXT:
- Document types: Purchase Orders (PO), Sales Invoices (SI), Purchase Invoices (PI), Goods Receipt Notes (GRN), Delivery Notes (DN), Journal Vouchers (JV).
- Indian business standards apply: amounts in INR (₹), GSTIN, HSN codes, CGST/SGST/IGST, TDS.
- The user is exploring transaction history — they do not know specific document names.

YOUR TASK:
Extract and list every distinct entity that matches the user's question from the provided documents. Follow these rules strictly:

1. EXHAUSTIVE ENUMERATION: List ALL distinct matching entities. If 8 vendors appear across the documents, list all 8. Never stop early, summarise away, or say "and others" without listing them first.

2. ENTITY NAME RULES — use ONLY human-readable names:
   - VALID: "M/s ABC Traders Pvt Ltd", "XYZ Transport", "Tata Steel Limited"
   - INVALID as names: GSTIN (15-char like 27CARPP5286L1ZO), HSN codes (4–8 digits), invoice/PO numbers, account codes, ZIP codes.
   - If you see "For, COMPANY NAME" or "Bill To: COMPANY NAME" — that is the entity name.

3. DEDUPLICATION: Same entity appearing in multiple documents = one bullet. Merge attributes across appearances.

4. ATTRIBUTES: For each entity, extract available sub-attributes as sub-bullets:
   - GSTIN, PAN, address, email, phone, bank details, HSN codes they supply, transaction amounts.

5. UNCERTAINTY: If an entity name is partially legible or abbreviated, include it with "(unclear: [raw text])" note.

6. COMPLETENESS CHECK: After listing, count your entries and confirm you have not missed any entity visible in the text.

7. DO NOT: invent, guess, or infer entity names not present in the text. Do not conflate two distinct entities.

FORMAT:
• Entity Name
  - Attribute 1
  - Attribute 2
(one bullet per distinct entity, sub-bullets for attributes)"""

_ANALYTICAL_PROMPT = """You are Virchow, a rigorous enterprise document analyst for an Indian company.

DOMAIN CONTEXT:
- Document types: Purchase Orders (PO), Sales Invoices (SI), Purchase Invoices (PI), Goods Receipt Notes (GRN), Delivery Notes (DN), Journal Vouchers (JV).
- Amounts in INR (₹). Indian number format: ₹1,00,000 = one lakh. Tax components: CGST, SGST, IGST, TDS.
- Fiscal year: April–March.

YOUR TASK:
Answer the aggregation, trend, or comparison question using ONLY the document chunks provided. Follow these rules strictly:

1. SHOW YOUR WORKING:
   - List each relevant document and its contributing value before computing the total.
   - Format: "[Document Name] → ₹[amount] ([field name e.g. 'Invoice Total', 'Taxable Value'])"
   - Then state the computed result on a separate line.

2. AMOUNT DISAMBIGUATION: Clearly distinguish:
   - Taxable value (before tax)
   - Tax amount (CGST + SGST or IGST)
   - Invoice total / Grand total (after tax)
   - TDS deducted (if applicable)
   - Net payable (after TDS)
   Use the same basis consistently across all documents in your computation.

3. COVERAGE DISCLOSURE: State explicitly how many documents were used in the computation and that this is a partial figure if the full dataset may contain more.

4. CONFLICTS: If two documents report different amounts for the same transaction, flag it: "Conflict: [Doc A] shows ₹X, [Doc B] shows ₹Y — using [Doc A/B] because [reason]."

5. MISSING DATA: If a document is missing an amount field needed for the computation, note it: "[Doc X] — amount not found, excluded from total."

6. GROUNDING: Do not extrapolate, estimate, or use amounts not explicitly present in the text.

FORMAT:
**Computation:**
[Doc 1] → ₹[amount]
[Doc 2] → ₹[amount]
...
**Total: ₹[sum]** (across N documents)
[Coverage and caveats on next line]"""

_MULTI_DOC_SYNTHESIS_PROMPT = """You are Virchow, a rigorous enterprise document analyst for an Indian company.

DOMAIN CONTEXT:
- Document types: Purchase Orders (PO), Sales Invoices (SI), Purchase Invoices (PI), Goods Receipt Notes (GRN), Delivery Notes (DN), Journal Vouchers (JV).
- Indian business standards: amounts in INR (₹), GSTIN, HSN codes, CGST/SGST/IGST, TDS, fiscal year April–March.

YOUR TASK:
Synthesise an answer from MULTIPLE document excerpts. Follow these rules strictly:

1. CROSS-DOCUMENT EXTRACTION: Read every provided document. Extract all facts relevant to the question from each one — even if only one document contains the answer.

2. ATTRIBUTION: For every fact cited, use the exact filename shown in the "Filename:" field of that document section. Format: "[fact] (Source: [exact filename])" — NEVER use generic labels like "Document 1" or "Document 2".

3. CONFLICT DETECTION: If two documents contain contradictory values for the same field:
   - Report both values with their sources.
   - Do not silently pick one. State: "Conflict detected: [Doc A] shows X, [Doc B] shows Y."

4. SYNTHESIS HIERARCHY:
   - For the same transaction: the most detailed document (e.g. invoice > PO) takes precedence.
   - For time-sensitive data: the more recent document takes precedence.

5. NUMERICAL ACCURACY: Copy amounts exactly — ₹ symbol, commas, decimal places. Never round.

6. COMPLETENESS: If the question asks for a list (e.g. "all items", "all vendors"), ensure you have checked every document, not just the first one.

7. NOT FOUND: Only say "not found" if NONE of the provided documents contain information related to the question.

8. GROUNDING: Every statement must trace to the provided text. No external knowledge.

FORMAT: Structured bullet list or short paragraphs with inline source attribution. Lead with the direct answer, follow with supporting evidence."""

_ANALYTICAL_SQL_PROMPT = """You are Virchow, a rigorous enterprise document analyst for an Indian company.

DOMAIN CONTEXT:
- Document types: Purchase Orders (PO), Sales Invoices (SI), Purchase Invoices (PI), Goods Receipt Notes (GRN), Delivery Notes (DN), Journal Vouchers (JV).
- Amounts in INR (₹). Indian number format applies. Fiscal year: April–March.

YOUR TASK:
Answer the analytical question using the structured database records provided. Follow these rules strictly:

1. LEAD WITH THE RESULT: State the computed total, count, or comparison first, prominently.

2. SHOW YOUR WORKING: List the key records used (document name, amount, date/month) before stating the total. If more than 10 records, summarise by group (e.g. by month or vendor).

3. AMOUNT BASIS: State clearly whether amounts are taxable value, invoice total, or net payable. Use a consistent basis throughout.

4. GROUPING INSIGHTS: If the data allows meaningful grouping (by month, vendor, doc type), provide a brief breakdown.

5. COVERAGE CAVEAT: State the number of records used and note that a complete figure may require reviewing all records in the system.

6. CONFLICTS: If records show duplicate document numbers or inconsistent amounts, flag them explicitly.

FORMAT:
**Answer: ₹[total]** (N documents, [date range if available])
**Breakdown:** [by month / vendor / type if relevant]
**Coverage note:** Based on N records retrieved. Full dataset may contain additional records."""

_NOT_FOUND_MARKERS = (
    "not found", "cannot answer", "no information", "does not contain",
    "not provided", "no mention", "not mentioned", "not available",
    "not present", "not specified", "not stated", "document does not",
)

# Max characters of OCR text sent to the LLM for a single-document query.
_MAX_CONTEXT_CHARS = 4000

# For multi-doc synthesis: cap total docs + per-doc char budget.
# Precision: depth over breadth (few docs, more text per doc).
# Exploratory/analytical: breadth (more docs, less text each).
_PRECISION_SYNTHESIS_DOCS = 8
_PRECISION_PER_DOC_CHARS = 1800
_BROAD_SYNTHESIS_DOCS = 10
_BROAD_PER_DOC_CHARS = 900

# Max chars of history to prepend
_MAX_HISTORY_CHARS = 600

# Filename part maps — imported from the single source of truth in filename_parser.py
_MONTH_MAP = MONTH_MAP
_DOC_TYPE_MAP = DOC_TYPE_MAP


def _decode_filename(filename: str) -> str:
    """Decode a structured filename like DEC-U2-PUR-24-25-40 into human-readable metadata."""
    name = re.sub(r'\.(pdf|xlsx?|docx?|csv|txt)$', '', filename, flags=re.IGNORECASE)
    parts = name.split('-')
    metadata = []

    if len(parts) >= 1 and parts[0].upper() in _MONTH_MAP:
        metadata.append(f"Month: {_MONTH_MAP[parts[0].upper()]}")

    if len(parts) >= 2 and re.match(r'^U\d+$', parts[1], re.IGNORECASE):
        metadata.append(f"Unit/Dept: {parts[1].upper()}")

    if len(parts) >= 3 and parts[2].upper() in _DOC_TYPE_MAP:
        metadata.append(f"Type: {_DOC_TYPE_MAP[parts[2].upper()]}")
    elif len(parts) >= 3:
        metadata.append(f"Type: {parts[2].upper()}")

    if len(parts) >= 5:
        try:
            fy = f"FY 20{parts[3]}-20{parts[4]}"
            metadata.append(fy)
        except Exception:
            pass

    if len(parts) >= 6:
        metadata.append(f"Serial: {parts[5]}")

    return " | ".join(metadata) if metadata else filename


def _financial_density(chunk: dict) -> float:
    """
    Score a chunk by how much financial data it contains.
    Distinguishes real rupee amounts from noise (dates, HSN codes, page numbers).

    Scoring:
      +3  per explicit rupee amount  (₹ / Rs. prefix)
      +2  per Indian-formatted number (1,00,000 style — commas at 2/3-digit groups)
      +1  per financial keyword line  (total, amount, cgst, sgst, igst, tds, etc.)
      -0.5 per 4-6 digit bare number  (likely HSN code, page ref, or date fragment)
    """
    text = chunk["chunk_text"]

    # Explicit rupee-prefixed amounts: ₹1,85,000 or Rs. 45,000.00
    rupee_amounts = len(re.findall(r'[₹][\s]*[\d,]+\.?\d*|Rs\.?\s*[\d,]+\.?\d*', text))

    # Indian-format numbers: 1,00,000 or 45,000.50 (2-or-3 digit comma groups)
    indian_numbers = len(re.findall(r'\b\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?\b', text))

    # Financial keyword lines (structural indicators of totals/tax rows)
    fin_keywords = len(re.findall(
        r'\b(grand\s+total|total\s+amount|taxable\s+value|invoice\s+value|'
        r'net\s+payable|net\s+amount|cgst|sgst|igst|tds|tax\s+amount|'
        r'amount\s+payable|balance\s+due|subtotal)\b',
        text, re.IGNORECASE,
    ))

    # Noise penalty: bare 4-6 digit numbers that are likely HSN, page, or date fragments
    noise = len(re.findall(r'(?<![₹,\d])\b\d{4,6}\b(?![,\d])', text))

    return rupee_amounts * 3.0 + indian_numbers * 2.0 + fin_keywords * 1.0 - noise * 0.5


def _precision_score(chunk: dict) -> float:
    """
    Composite score for PRECISION reranking.

    Base: cosine similarity
    Boosts:
      +0.20  keyword hit (exact term match — strongest signal)
      +0.08  page 1-3 (invoice header: party, totals, dates)
      +0.04  page 4-6 (line items, tax breakdown)
      +0.05  financial header terms present (invoice/PO/total/GSTIN/vendor)
      +0.06  doc_type metadata populated — OCR correctly identified the document type
      +0.04  party_name metadata populated — structured party data available
      +0.03  high OCR quality (≥ 0.8)
    Penalties:
      -0.12  low OCR quality (< 0.4) — unreliable text
      -0.05  very late page (> 10) — likely T&C / annexure / blank
    """
    score = float(chunk.get("similarity", 0.0))

    if chunk.get("_keyword_hit"):
        score += 0.20

    page = int(chunk.get("page_num") or 0)
    if page <= 3:
        score += 0.08
    elif page <= 6:
        score += 0.04
    elif page > 10:
        score -= 0.05

    text_lower = chunk["chunk_text"].lower()
    header_terms = [
        "invoice", "purchase order", "delivery note", "goods receipt",
        "grand total", "net payable", "gstin", "bill to", "ship to",
        "vendor", "supplier", "buyer", "seller", "party name",
    ]
    if any(t in text_lower for t in header_terms):
        score += 0.05

    if chunk.get("doc_type"):
        score += 0.06
    if chunk.get("party_name"):
        score += 0.04

    qs = chunk.get("quality_score")
    if qs is not None:
        qs = float(qs)
        if qs >= 0.8:
            score += 0.03
        elif qs < 0.4:
            score -= 0.12

    return score


def _rerank(chunks: list, intent: str) -> list:
    """
    Re-order chunks based on query intent.

    PRECISION:  composite score — keyword hit + page position + header terms + OCR quality + similarity
    ANALYTICAL: financial density score — rupee amounts, Indian-format numbers, fin-keyword lines
    EXPLORATORY: coverage-first — guarantee 1 chunk per unique document, then fill by similarity
    """
    if not chunks:
        return chunks

    if intent == INTENT_PRECISION:
        return sorted(chunks, key=_precision_score, reverse=True)

    if intent == INTENT_EXPLORATORY:
        # Pass 1: best chunk per document (by similarity) — ensures breadth across files
        best_per_doc: dict = {}
        for c in chunks:
            fname = c["file_name"]
            if fname not in best_per_doc or float(c["similarity"]) > float(best_per_doc[fname]["similarity"]):
                best_per_doc[fname] = c

        # Sort representatives by doc_type first (groups POs together, Invoices together,
        # etc.) then by similarity within each type — makes the LLM context coherent.
        def _exploratory_key(c: dict):
            return (c.get("doc_type") or "zz_unknown", -float(c["similarity"]))

        representatives = sorted(best_per_doc.values(), key=_exploratory_key)

        # Pass 2: remaining chunks sorted by similarity
        best_ids = {id(c) for c in best_per_doc.values()}
        remainder = sorted(
            [c for c in chunks if id(c) not in best_ids],
            key=lambda c: float(c["similarity"]),
            reverse=True,
        )

        return representatives + remainder

    if intent == INTENT_ANALYTICAL:
        # Two-pass: first group chunks by party_name so the LLM sees all entries for
        # the same vendor together (easier per-party aggregation), then within each
        # vendor group rank by financial density descending.
        from collections import defaultdict
        party_buckets: dict = defaultdict(list)
        for c in chunks:
            party_buckets[c.get("party_name") or ""].append(c)

        result: list = []
        # Sort party buckets by the max density in each bucket so highest-value
        # vendors appear first (handles "top vendor by amount" queries correctly).
        for _, bucket in sorted(
            party_buckets.items(),
            key=lambda kv: max(_financial_density(c) for c in kv[1]),
            reverse=True,
        ):
            result.extend(
                sorted(bucket, key=lambda c: (_financial_density(c), float(c["similarity"])), reverse=True)
            )
        return result

    return chunks


def _is_not_found(text: str) -> bool:
    t = text.lower().strip()
    return any(m in t for m in _NOT_FOUND_MARKERS) and len(t) < 250


def _files_mentioned_in_answer(answer: str, files) -> set:
    """Return the subset of files whose distinctive identifier tokens appear in the answer.

    Matches on (a) the whole base name, (b) any 4+ digit number in it, (c) any 3+ char
    alpha-numeric segment between dashes, (d) "Document N" ordinal references mapped back
    by position. Falls back to all files if nothing matches.
    """
    ans = answer.lower()
    mentioned: set = set()
    for i, f in enumerate(files, 1):
        base = re.sub(r'\.(pdf|xlsx?|docx?|csv|txt)$', '', f, flags=re.IGNORECASE).lower()
        if base in ans:
            mentioned.add(f)
            continue
        tokens = re.findall(r'\d{4,}', base) + [
            seg for seg in base.split('-') if len(seg) >= 3 and not seg.isdigit()
        ]
        if any(t in ans for t in tokens):
            mentioned.add(f)
            continue
        # Map "Document N" / "document N" / "doc N" back to the file at position i
        if re.search(rf'\bdoc(?:ument)?\s*{i}\b', ans):
            mentioned.add(f)
    # Documented fallback: return all files when the LLM produced no recognisable attribution
    return mentioned if mentioned else set(files)


def _clean_chunk(text: str) -> str:
    """Strip HTML tags, base64 image blobs, and excess whitespace from chunk text."""
    text = re.sub(r'!\[.*?\]\(data:[^)]{20,}\)', '[image]', text)
    text = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]{20,}', '[image]', text)
    # Strip raw base64 fragments (orphaned tails from split image blobs):
    # any run of 50+ chars consisting of base64 alphabet, ending in optional `==)` / `=)` / `)`
    text = re.sub(r'[A-Za-z0-9+/]{50,}={0,2}\)?', ' ', text)
    text = re.sub(r'<td[^>]*>', ' | ', text, flags=re.IGNORECASE)
    text = re.sub(r'<th[^>]*>', ' | ', text, flags=re.IGNORECASE)
    text = re.sub(r'<tr[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _format_history(history: list) -> str:
    """Format recent conversation turns for inclusion in the prompt."""
    if not history:
        return ""
    lines = []
    for m in history:
        role = "User" if m["role"] == "user" else "Assistant"
        content = m["content"][:200].replace("\n", " ")
        lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    return text[:_MAX_HISTORY_CHARS]


def _call_ollama(prompt: str, num_predict: int) -> str:
    # B-C5: catch timeout and connection errors explicitly — never let raw httpx exceptions
    # bubble up to the user as 500 errors.
    try:
        response = httpx.post(
            f"{cfg.llm_url}/api/generate",
            json={
                "model": cfg.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": num_predict,
                    "top_p": 1.0,
                    "repeat_penalty": 1.1,
                },
            },
            timeout=180.0,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except httpx.TimeoutException:
        logger.error("Ollama request timed out after 120s")
        raise RuntimeError("The AI model took too long to respond. Please try again.")
    except httpx.ConnectError:
        logger.error(f"Cannot connect to Ollama at {cfg.llm_url}")
        raise RuntimeError("The AI model service is currently unavailable. Please try again later.")
    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama returned HTTP {e.response.status_code}")
        raise RuntimeError("The AI model returned an unexpected error. Please try again.")


def call_llm(question: str, context_chunks: list,
             history: list = None, intent: str = INTENT_PRECISION) -> tuple:
    """
    Returns (answer_text, relevant_file_names).
    intent: one of INTENT_PRECISION | INTENT_EXPLORATORY | INTENT_ANALYTICAL
    history: recent chat messages [{role, content}] for conversation continuity.
    """
    history_text = _format_history(history) if history else ""

    # Chunks are reranked upstream in rag_pipeline before the cap — no need to rerank here.
    # Group chunks by source file (order reflects upstream rerank)
    by_file: dict = defaultdict(list)
    for c in context_chunks:
        by_file[c["file_name"]].append(c["chunk_text"])

    unique_files = list(by_file.keys())

    # Select system prompt: intent-specific prompts win over the generic synthesis prompt
    # (intent prompts have tailored rules — e.g. EXPLORATORY enforces exhaustive entity listing).
    if intent == INTENT_EXPLORATORY:
        system_prompt = _EXPLORATORY_PROMPT
    elif intent == INTENT_ANALYTICAL:
        system_prompt = _ANALYTICAL_PROMPT
    elif len(unique_files) > 1:
        system_prompt = _MULTI_DOC_SYNTHESIS_PROMPT
    else:
        system_prompt = _PRECISION_PROMPT

    # ── Single source ─────────────────────────────────────────────────────────
    if len(unique_files) == 1:
        fname = unique_files[0]
        file_meta = _decode_filename(fname)
        cleaned = [_clean_chunk(t) for t in by_file[fname]]
        context = "\n\n".join(cleaned)[:_MAX_CONTEXT_CHARS]
        history_section = f"--- CONVERSATION HISTORY ---\n{history_text}\n--- END HISTORY ---\n\n" if history_text else ""
        prompt = (
            f"{system_prompt}\n\n"
            f"{history_section}"
            f"--- BEGIN DOCUMENT ---\n"
            f"Filename: {fname}\n"
            f"Metadata: {file_meta}\n\n"
            f"{context}\n"
            f"--- END DOCUMENT ---\n\n"
            f"Question: {question}\n\n"
            f"Answer:"
        )
        try:
            answer = _call_ollama(prompt, num_predict=cfg.max_tokens)
            if _is_not_found(answer):
                return ("No relevant information found in the retrieved document.", set())
            return (f"**{fname}**\n{answer}", {fname})
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ("I was unable to generate a response at this time.", set())

    # ── Multiple sources: single synthesis call across all documents ──────────
    # Intent-based budget: precision wants depth (4 docs × 1800 chars for line items);
    # exploratory/analytical wants breadth (10 docs × 900 chars to enumerate entities).
    if intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL):
        max_docs, per_doc_chars = _BROAD_SYNTHESIS_DOCS, _BROAD_PER_DOC_CHARS
    else:
        max_docs, per_doc_chars = _PRECISION_SYNTHESIS_DOCS, _PRECISION_PER_DOC_CHARS
    capped_files = unique_files[:max_docs]
    doc_sections = []
    for i, fname in enumerate(capped_files, 1):
        file_meta = _decode_filename(fname)
        cleaned = [_clean_chunk(t) for t in by_file[fname]]
        doc_text = "\n".join(cleaned)[:per_doc_chars]
        doc_sections.append(
            f"--- DOCUMENT {i}: {fname} ---\n"
            f"Filename: {fname}\n"
            f"Metadata: {file_meta}\n\n"
            f"{doc_text}\n"
            f"--- END DOCUMENT {i}: {fname} ---"
        )

    combined_context = "\n\n".join(doc_sections)
    history_section = f"--- CONVERSATION HISTORY ---\n{history_text}\n--- END HISTORY ---\n\n" if history_text else ""
    prompt = (
        f"{system_prompt}\n\n"
        f"{history_section}"
        f"{combined_context}\n\n"
        f"Question: {question}\n\n"
        f"Answer:"
    )
    logger.info(f"[DEBUG-LLM] sending {len(capped_files)}/{len(unique_files)} docs, prompt_chars={len(prompt)}, question={question!r}")
    logger.info(f"[DEBUG-LLM] context FULL:\n{combined_context}")
    # Broad queries need more output tokens to enumerate entities or show computation steps.
    output_tokens = max(cfg.max_tokens, 1024) if intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL) else cfg.max_tokens
    try:
        answer = _call_ollama(prompt, num_predict=output_tokens)
        logger.info(f"[DEBUG-LLM] raw answer: {answer!r}")
        if _is_not_found(answer):
            logger.warning(f"[DEBUG-LLM] answer matched not-found markers, returning canned response")
            return ("No relevant information found across the retrieved documents.", set())
        relevant_files = _files_mentioned_in_answer(answer, capped_files)
        logger.info(f"[DEBUG-LLM] relevant_files after filter: {relevant_files}")
        note = ""
        if intent == INTENT_ANALYTICAL:
            n_total = len(unique_files)
            n_used  = len(capped_files)
            if n_total > n_used:
                note = (
                    f"\n\n*Coverage: computed from {n_used} of {n_total} retrieved documents. "
                    f"A complete figure requires reviewing all records in the system.*"
                )
            else:
                note = (
                    f"\n\n*Coverage: based on {n_total} document{'s' if n_total != 1 else ''} retrieved. "
                    f"A complete total may require reviewing all records in the system.*"
                )
        return (f"{answer}{note}", relevant_files)
    except Exception as e:
        logger.error(f"LLM synthesize call failed: {e}")
        return ("I was unable to generate a response at this time.", set())


def call_llm_sql(question: str, sql_rows: list, history: list = None) -> tuple:
    """
    Answer an analytical question using structured document records from SQL.
    Returns (answer_text, relevant_file_names_set).
    """
    history_text = _format_history(history) if history else ""

    # Build a compact table of the SQL rows
    lines = []
    total_amount_sum = 0.0
    has_amounts = False
    file_names: set = set()

    for r in sql_rows:
        file_names.add(r["file_name"])
        parts = []
        if r.get("file_name"):
            parts.append(r["file_name"])
        if r.get("doc_type"):
            parts.append(r["doc_type"])
        if r.get("doc_month") or r.get("fiscal_year"):
            parts.append(f"{r.get('doc_month', '')} {r.get('fiscal_year', '')}".strip())
        if r.get("party_name"):
            parts.append(f"Vendor: {r['party_name']}")
        if r.get("doc_number"):
            parts.append(f"Ref: {r['doc_number']}")
        if r.get("doc_date"):
            parts.append(f"Date: {r['doc_date']}")
        if r.get("total_amount") is not None:
            amt = float(r["total_amount"])
            parts.append(f"Total: ₹{amt:,.2f}")
            total_amount_sum += amt
            has_amounts = True
        if r.get("doc_unit"):
            parts.append(f"Unit: {r['doc_unit']}")
        lines.append(" | ".join(parts))

    # Build a structured answer directly from pre-computed data — the LLM's extended
    # thinking consumes its entire token budget before generating any response text, so
    # for analytical SQL results we format the answer in Python instead.
    n = len(sql_rows)
    if has_amounts:
        # Collect unique vendors, months, doc_types for a richer summary
        vendors = sorted({r["party_name"] for r in sql_rows if r.get("party_name")})[:5]
        months  = sorted({r["doc_month"]  for r in sql_rows if r.get("doc_month")})
        dtypes  = sorted({r["doc_type"]   for r in sql_rows if r.get("doc_type")})

        vendor_note = f"\n\nTop vendors: {', '.join(vendors)}" + ("..." if len({r['party_name'] for r in sql_rows if r.get('party_name')}) > 5 else "") if vendors else ""
        month_note  = f"\nMonths covered: {', '.join(months)}" if months else ""
        dtype_note  = f"\nDocument types: {', '.join(dtypes)}" if dtypes else ""

        answer = (
            f"**Total: ₹{total_amount_sum:,.2f}** across {n} document{'s' if n != 1 else ''}."
            f"{month_note}{dtype_note}{vendor_note}"
            f"\n\n*Note: Based on {n} document{'s' if n != 1 else ''} with structured metadata. "
            f"A complete total may require reviewing all records.*"
        )
    else:
        answer = f"Found {n} document{'s' if n != 1 else ''} matching your query."

    return (answer, file_names)
