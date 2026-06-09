import os
import re
import logging
from src.config import cfg
from src.database.postgres_db import RBACManager
from src.ingestion.embedding.embedder import MxbaiEmbedder
from src.retrieval.llm_client import call_llm, call_llm_sql, INTENT_PRECISION, INTENT_EXPLORATORY, INTENT_ANALYTICAL, _rerank
from src.ingestion.filename_parser import MONTH_NAME_MAP as _MONTH_NAME_MAP_IMPORTED

try:
    from src.ingestion.embedding.colpali_embedder import ColPaliEmbedder as _ColPaliEmbedder
    _COLPALI_AVAILABLE = True
except Exception:
    _COLPALI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Matches document IDs like DEC-U2-PUR-24-25-40, INV-2024-001, PO-23-456, etc.
_DOC_ID_RE = re.compile(r'\b([A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+){2,})\b')

# Matches file extensions to strip before doc-ID matching
_FILE_EXT_RE = re.compile(r'\.(pdf|xlsx?|docx?|csv|txt)$', re.IGNORECASE)

# Matches bare filenames with extensions (e.g. "report.pdf", "FEB-U2-DN.pdf")
_FILENAME_RE = re.compile(r'\S+\.(?:pdf|xlsx?|docx?|csv|txt)', re.IGNORECASE)


def _compose_answer_with_sources(answer: str, citations: list) -> str:
    """Append a markdown 'Sources:' block with proxy URLs to the answer so it
    survives DB persistence. The frontend renderer detects /api/chat/file/
    links and opens the in-app PDF preview modal instead of navigating away.
    Skips if the answer already contains a 'Sources:' block (idempotent) or
    if there are no citations."""
    from urllib.parse import quote
    if not citations:
        return answer
    if re.search(r'(?im)^\s*Sources:\s*$', answer):
        return answer
    seen = set()
    lines = []
    for c in citations:
        name = (c or {}).get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(f"- [{name}](/api/chat/file/{quote(name)})")
    if not lines:
        return answer
    return f"{answer.rstrip()}\n\nSources:\n" + "\n".join(lines)

# Short conversational inputs that don't need RAG
_CONVERSATIONAL_RE = re.compile(
    r'^\s*(hello|hi+|hey|thanks|thank\s+you|ok(ay)?|sure|bye|goodbye|'
    r'good\s+(morning|afternoon|evening|day)|how\s+are\s+you|'
    r'what\s+can\s+you\s+do|help\s+me|who\s+are\s+you)\s*[.!?]?\s*$',
    re.IGNORECASE,
)

# Extracts filenames cited in assistant messages like **FEB-U2-DN.pdf**
_CITED_FILE_RE = re.compile(
    r'\*\*([^*]+\.(?:pdf|xlsx?|docx?|csv|txt))\*\*',
    re.IGNORECASE,
)

# Month name → canonical DB value — imported from filename_parser (single source of truth)
_MONTH_NAME_MAP = _MONTH_NAME_MAP_IMPORTED

# Quarter → months (calendar year)
_QUARTER_MONTHS = {
    "q1": ["January", "February", "March"],
    "q2": ["April", "May", "June"],
    "q3": ["July", "August", "September"],
    "q4": ["October", "November", "December"],
    "first quarter": ["January", "February", "March"],
    "second quarter": ["April", "May", "June"],
    "third quarter": ["July", "August", "September"],
    "fourth quarter": ["October", "November", "December"],
}

_MONTH_RE = re.compile(
    r'\b(january|february|march|april|may|june|july|august|'
    r'september|october|november|december|'
    r'jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b',
    re.IGNORECASE,
)

_QUARTER_RE = re.compile(
    r'\b(q[1-4]|first\s+quarter|second\s+quarter|third\s+quarter|fourth\s+quarter)\b',
    re.IGNORECASE,
)

# Fiscal year patterns: "FY 2024-25", "2024-25", "FY24-25", "financial year 2024-25"
_FISCAL_YEAR_RE = re.compile(
    r'\b(?:fy\s*)?20(\d{2})[/-](?:20)?(\d{2})\b|\b(?:financial\s+year\s+)20(\d{2})[/-](?:20)?(\d{2})\b',
    re.IGNORECASE,
)

# Document type keywords → DB value map
_DOC_TYPE_KEYWORDS: dict = {
    "purchase order": "Purchase Order", "po": "Purchase Order",
    "purchase invoice": "Purchase Invoice", "pi": "Purchase Invoice",
    "sales invoice": "Sales Invoice", "invoice": "Sales Invoice",
    "grn": "Goods Receipt Note", "goods receipt": "Goods Receipt Note",
    "delivery note": "Delivery Note", "dn": "Delivery Note",
    "journal voucher": "Journal Voucher", "jv": "Journal Voucher",
    "credit note": "Credit Note", "debit note": "Debit Note",
    "sales order": "Sales Order", "so": "Sales Order",
}

# Analytical queries: aggregation, totals, trends, time ranges
_ANALYTICAL_RE = re.compile(
    r'\b(total|sum|how\s+much|how\s+many|revenue|turnover|count|aggregate|'
    r'all\s+(vendors?|suppliers?|invoices?|orders?|customers?|purchases?)|'
    r'list\s+(all|every|of)|across\s+all|overall|combined|'
    r'year(ly)?|quarter(ly)?|month(ly)?|since\s+when|when\s+did\s+we\s+start|'
    r'history|trend|compare|comparison|highest|lowest|maximum|minimum|'
    r'top\s+\d+|rank)\b',
    re.IGNORECASE,
)

# Exploratory queries: discovery, understanding, overview
_EXPLORATORY_RE = re.compile(
    r'\b(who\s+(are|is)\s+(our|the)|what\s+(vendors?|suppliers?|customers?|products?|'
    r'items?|materials?|do\s+we|kind|type|does\s+this|all)|'
    r'tell\s+me\s+about|overview|summary|summarize|explain|describe|'
    r'what\s+all|understand|onboard|new\s+(here|joinee|employee)|'
    r'introduce|background|context|previous\s+(transactions?|history|records?))\b',
    re.IGNORECASE,
)

_STOP_WORDS = {
    # Common English function words
    "a", "an", "the", "in", "on", "at", "of", "for", "to", "and", "or", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "but", "not", "with", "this", "that", "from", "by", "what",
    "who", "which", "how", "when", "where", "me", "my", "we", "our", "its",
    "these", "those", "they", "them", "their", "there", "here", "will",
    "would", "could", "should", "shall", "may", "might", "can", "just", "also",
    "some", "any", "all", "each", "every", "both", "more", "most", "other",
    "than", "then", "now", "only", "over", "such", "very", "show", "give",
    "tell", "find", "list", "need", "want", "make", "like", "know", "about",
    "please", "full", "complete",
    # Common document words that appear in nearly every chunk (low discrimination)
    "price", "amount", "invoice", "number", "details", "detail",
    "company", "purchase", "order", "quantity", "unit", "rate", "value",
    "payment", "information", "document", "product", "goods", "account",
    "india", "limited", "private", "pvt", "ltd",
    # NOTE: "supplier", "buyer", "seller", "total", "service" intentionally kept —
    # they are meaningful discriminators for invoice/PO queries.
}

# Max chunks sent to LLM (precision), more for broad queries
_MAX_LLM_CHUNKS = 10
_MAX_LLM_CHUNKS_BROAD = 20

# How many recent messages to consider for conversation context
_HISTORY_WINDOW = 6


def _rrf_merge(text_results: list, visual_results: list,
               k: int = 60, top_k: int = 20) -> list:
    """
    Reciprocal Rank Fusion of text-pipeline chunks and ColPali-sourced chunks.
    score = sum(1 / (k + rank)) across both ranked lists.
    Deduplicates by chunk_id, keeping the entry with the higher final score.
    """
    scores: dict = {}
    for rank, r in enumerate(text_results):
        key = str(r.get("chunk_id", ""))
        if not key:
            continue
        if key not in scores:
            scores[key] = {"score": 0.0, "data": r}
        scores[key]["score"] += 1.0 / (k + rank + 1)

    for rank, r in enumerate(visual_results):
        key = str(r.get("chunk_id", ""))
        if not key:
            continue
        if key not in scores:
            scores[key] = {"score": 0.0, "data": r}
        scores[key]["score"] += 1.0 / (k + rank + 1)

    merged = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return [m["data"] for m in merged[:top_k]]


class RetrievalService:
    def __init__(self, pool):
        self.rbac = RBACManager(pool)
        self.embedder = MxbaiEmbedder()
        self._colpali: "_ColPaliEmbedder | None" = None
        logger.info("RetrievalService ready.")

    def _get_colpali(self) -> "_ColPaliEmbedder | None":
        """Lazy-load the ColPali query encoder on first visual search."""
        if not cfg.enable_colpali:
            return None
        if self._colpali is not None:
            return self._colpali
        if not _COLPALI_AVAILABLE:
            return None
        try:
            self._colpali = _ColPaliEmbedder.get_instance()
        except Exception as e:
            logger.warning("ColPali encoder unavailable: %s", e)
        return self._colpali

    def _get_seaweedfs_url(self, file_name: str) -> str:
        # Files ingested via the ingest service are uploaded through the S3 API
        # (port 8333). SeaweedFS exposes S3 bucket objects via the Filer at
        # /buckets/{bucket}/{key}, NOT /{bucket}/{key}.
        if not file_name:
            return ""
        from urllib.parse import quote
        filename = os.path.basename(file_name)
        filer_url = cfg.seaweedfs_filer_url.rstrip("/")
        return f"{filer_url}/buckets/{cfg.seaweedfs_bucket}/raw/{quote(filename)}"

    def store_in_seaweedfs(self, pdf_bytes: bytes, file_name: str, dept_id: str) -> str:
        """
        Upload PDF bytes to SeaweedFS. Returns the file_path (just filename) stored in documents.file_path.
        """
        import requests as _requests

        url = f"{cfg.seaweedfs_filer_url.rstrip('/')}/{cfg.seaweedfs_bucket}/raw/{file_name}"
        resp = _requests.put(url, data=pdf_bytes, headers={"Content-Type": "application/pdf"}, timeout=60)
        resp.raise_for_status()
        return file_name

    def _extract_doc_name(self, question: str) -> str | None:
        """Return the first document-ID-like token or filename found in the question, or None."""
        filename_match = _FILENAME_RE.search(question)
        if filename_match:
            return filename_match.group(0).strip('.,;:?!()[]"\'')

        for token in question.split():
            clean = token.strip('.,;:?!()[]"\'')
            name_only = _FILE_EXT_RE.sub('', clean)
            if _DOC_ID_RE.fullmatch(name_only):
                return clean
            if _DOC_ID_RE.fullmatch(clean):
                return clean
        return None

    def _extract_keywords(self, question: str) -> list:
        """Return content words (>=5 chars, not stop words) for keyword search."""
        words = re.sub(r'[^\w\s]', ' ', question.lower()).split()
        return [w for w in words if len(w) >= 5 and w not in _STOP_WORDS]

    def _extract_active_files(self, history: list) -> list:
        """Return filenames cited in the most recent assistant message that had citations."""
        for msg in reversed(history[-_HISTORY_WINDOW:]):
            if msg["role"] == "assistant":
                found = _CITED_FILE_RE.findall(msg["content"])
                if found:
                    seen: dict = {}
                    for f in found:
                        seen[f] = True
                    logger.info(f"Active documents from history: {list(seen.keys())}")
                    return list(seen.keys())
        return []

    def _extract_fiscal_year(self, question: str) -> str | None:
        """Return fiscal year string like 'FY 2024-25' if detected, else None."""
        m = _FISCAL_YEAR_RE.search(question)
        if not m:
            return None
        # groups: (yy1, yy2) from "2024-25" or (None, None, yy1, yy2) from "financial year"
        g = m.groups()
        yy1 = g[0] or g[2]
        yy2 = g[1] or g[3]
        if yy1 and yy2:
            return f"FY 20{yy1}-20{yy2}"
        return None

    def _extract_doc_type(self, question: str) -> str | None:
        """Return canonical doc_type value if a document type keyword is found."""
        q_lower = question.lower()
        # Check multi-word keys first (longest match)
        for key in sorted(_DOC_TYPE_KEYWORDS, key=len, reverse=True):
            if re.search(r'\b' + re.escape(key) + r'\b', q_lower):
                return _DOC_TYPE_KEYWORDS[key]
        return None

    def _apply_quality_penalty(self, results: list) -> list:
        """Downweight chunks with medium-quality OCR (0.3 ≤ score < 0.6)."""
        penalized = []
        for r in results:
            qs = r.get("quality_score")
            if qs is not None and 0.3 <= float(qs) < 0.6:
                r = dict(r)
                r["similarity"] = float(r["similarity"]) * 0.85
            penalized.append(r)
        return penalized

    def _extract_months(self, question: str) -> list:
        """Return canonical month names (matching doc_month column) found in the question."""
        months: set = set()
        for m in _QUARTER_RE.finditer(question):
            key = re.sub(r'\s+', ' ', m.group(0).lower().strip())
            if key in _QUARTER_MONTHS:
                months.update(_QUARTER_MONTHS[key])
        for m in _MONTH_RE.finditer(question):
            key = m.group(0).lower()
            if key in _MONTH_NAME_MAP:
                months.add(_MONTH_NAME_MAP[key])
        return sorted(months)

    def _classify_intent(self, question: str, doc_name: str | None, active_files: list) -> str:
        """Classify query intent: precision | exploratory | analytical."""
        # If we have a specific document context, always treat as precision
        if doc_name or active_files:
            return INTENT_PRECISION

        if _ANALYTICAL_RE.search(question):
            return INTENT_ANALYTICAL

        if _EXPLORATORY_RE.search(question):
            return INTENT_EXPLORATORY

        return INTENT_PRECISION

    def query(self, question: str, user_id: str, dept_id: str, chat_id: str = None) -> dict:
        # 1. Short-circuit for conversational inputs — no RAG needed
        if _CONVERSATIONAL_RE.match(question):
            logger.info("Conversational query detected — skipping RAG")
            if not chat_id:
                chat_id = self.rbac.create_chat(user_id, dept_id, title=question[:60])
            self.rbac.update_chat_title_if_empty(chat_id, question[:60])
            self.rbac.add_message(chat_id, "user", question)
            answer = (
                "Hello! I'm Virchow, your document knowledge assistant. "
                "Ask me anything about your documents — prices, suppliers, quantities, and more."
            )
            self.rbac.add_message(chat_id, "assistant", answer)
            return {"answer": answer, "citations": [], "chat_id": chat_id}

        # 2. Fetch conversation history (for context continuity)
        history: list = []
        active_files: list = []
        if chat_id:
            history = self.rbac.get_messages_full(chat_id, dept_id)
            active_files = self._extract_active_files(history)

        # 3. Detect document reference, temporal filter, and classify intent
        doc_name = self._extract_doc_name(question)
        # Month/year/type filters only apply to global searches
        months_filter = self._extract_months(question) if not doc_name else []
        fiscal_year = self._extract_fiscal_year(question) if not doc_name else None
        detected_doc_type = self._extract_doc_type(question) if not doc_name else None
        intent = self._classify_intent(question, doc_name, active_files)
        logger.info(
            f"Query intent: {intent} | doc_name: {doc_name!r} | "
            f"active_files: {active_files} | months_filter: {months_filter} | "
            f"fiscal_year: {fiscal_year} | doc_type: {detected_doc_type}"
        )

        # 4a. Analytical SQL handler — try structured aggregation before vector search
        if intent == INTENT_ANALYTICAL and not doc_name and not active_files:
            _kws = self._extract_keywords(question)
            _meta = {"price", "prices", "amount", "total", "document", "documents",
                     "across", "invoice", "order", "purchase", "vendor", "supplier",
                     "spent", "spend", "costs", "costed", "about", "items", "drugs",
                     "medicines", "medications", "medical", "hospital", "product"}
            candidate_keywords = [k for k in _kws if k.lower() not in _meta]

            # Try each candidate keyword — use the first that returns matching rows
            sql_rows: list = []
            product_keyword: str | None = None
            for _ck in (candidate_keywords or [None]):
                sql_rows = self.rbac.analytical_query(
                    dept_id,
                    months=months_filter or None,
                    fiscal_year=fiscal_year,
                    doc_type=detected_doc_type,
                    product_keyword=_ck,
                )
                if sql_rows:
                    product_keyword = _ck
                    break
            if sql_rows:
                logger.info(f"Analytical SQL returned {len(sql_rows)} document rows")
                recent_history = history[-_HISTORY_WINDOW:] if history else []
                answer, relevant_files = call_llm_sql(question, sql_rows, history=recent_history)
                citations = []
                seen: dict = {}
                for r in sql_rows:
                    doc_id = str(r["document_id"])
                    fname = r["file_name"]
                    if doc_id in seen or fname not in relevant_files:
                        continue
                    seen[doc_id] = True
                    seaweed_url = self._get_seaweedfs_url(r.get("file_name", ""))
                    citations.append({"name": fname, "document_id": doc_id, "url": seaweed_url})
                if not chat_id:
                    chat_id = self.rbac.create_chat(user_id, dept_id, title=question[:60])
                self.rbac.update_chat_title_if_empty(chat_id, question[:60])
                persisted_answer = _compose_answer_with_sources(answer, citations)
                self.rbac.add_message(chat_id, "user", question)
                self.rbac.add_message(chat_id, "assistant", persisted_answer)
                return {"answer": persisted_answer, "citations": citations, "chat_id": chat_id}
            logger.info("Analytical SQL returned no rows — falling back to vector search")

        # 4b. Embed the question
        query_vec = self.embedder.embed_text(question)

        # 5. Retrieval — strategy depends on intent
        top_k_broad = cfg.top_k_retrieval * 2  # more candidates for synthesis

        if doc_name:
            # Explicit document targeted — scoped search only
            logger.info(f"Detected document ID {doc_name!r} — filtered vector search")
            vec_results = self.rbac.vector_search_by_filename(
                query_vec, dept_id, doc_name, top_k=cfg.top_k_retrieval
            )
            if not vec_results:
                logger.info(f"No chunks for {doc_name!r}, falling back to global search")
                vec_results = self.rbac.vector_search(query_vec, dept_id, top_k=cfg.top_k_retrieval)
            active_files = []

        elif active_files:
            # Conversation context — scoped to active document(s), no global fallback
            logger.info(f"Continuing conversation on {active_files} — scoped vector search")
            vec_results = []
            seen_ids: set = set()
            for fname in active_files[:2]:
                for r in self.rbac.vector_search_by_filename(
                    query_vec, dept_id, fname, top_k=cfg.top_k_retrieval
                ):
                    if r["chunk_id"] not in seen_ids:
                        vec_results.append(r)
                        seen_ids.add(r["chunk_id"])

        elif intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL):
            # Broad query — global search with higher top_k; apply month filter if detected
            logger.info(
                f"Broad {intent} query — global vector search (top_k={top_k_broad})"
                + (f", months={months_filter}" if months_filter else "")
            )
            vec_results = self.rbac.vector_search(
                query_vec, dept_id, top_k=top_k_broad, months=months_filter or None
            )

        else:
            vec_results = self.rbac.vector_search(
                query_vec, dept_id, top_k=cfg.top_k_retrieval, months=months_filter or None
            )

        # 6. Keyword search — scoped to target document(s) when known
        keywords = self._extract_keywords(question)
        kw_results: list = []
        if len(keywords) >= 1:
            if doc_name:
                kw_results = self.rbac.keyword_search_by_filename_pattern(
                    keywords, dept_id, doc_name, top_k=cfg.top_k_retrieval
                )
            elif active_files:
                kw_results = self.rbac.keyword_search_in_files(
                    keywords, dept_id, active_files, top_k=cfg.top_k_retrieval
                )
            else:
                # Global keyword search — apply month filter if detected
                kw_results = self.rbac.keyword_search(
                    keywords, dept_id,
                    top_k=top_k_broad if intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL) else cfg.top_k_retrieval,
                    months=months_filter or None,
                )
            logger.info(f"Keyword search ({keywords}) → {len(kw_results)} chunks")

        # 6b. ColPali visual search — runs in parallel with keyword search
        colpali_chunks: list = []
        colpali_enc = self._get_colpali()
        if colpali_enc:
            try:
                colpali_vec = colpali_enc.embed_query(question)
                colpali_hits = self.rbac.colpali_search(
                    colpali_vec, dept_id, top_k=cfg.top_k_retrieval
                )
                if colpali_hits:
                    colpali_chunks = self.rbac.get_chunks_for_colpali_pages(
                        colpali_hits, dept_id
                    )
                    logger.info("ColPali search → %d page hits → %d chunks",
                                len(colpali_hits), len(colpali_chunks))
            except Exception as e:
                logger.warning("ColPali search skipped: %s", e)

        # 7. Merge: RRF of (keyword + vector) results with ColPali visual results
        seen_chunks = {r["chunk_id"] for r in kw_results}
        text_merged = list(kw_results)
        for r in vec_results:
            if r["chunk_id"] not in seen_chunks:
                text_merged.append(r)

        if colpali_chunks:
            max_chunks_rrf = (
                _MAX_LLM_CHUNKS_BROAD
                if intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL)
                else _MAX_LLM_CHUNKS
            )
            merged = _rrf_merge(text_merged, colpali_chunks, top_k=max_chunks_rrf * 2)
        else:
            merged = text_merged

        # 8a. Apply quality score penalty for medium-quality OCR chunks
        merged = self._apply_quality_penalty(merged)

        # 8b. Threshold filter: keyword hits bypass threshold; vector-only hits must meet it
        results = [
            r for r in merged
            if r.get("_keyword_hit") or float(r["similarity"]) >= cfg.similarity_threshold
        ]

        # 8c. Rerank before cap — ensures best chunks from the full pool survive the cut.
        # Analytical: globally sorts by numeric density so a data-rich vector chunk
        # ranks above a low-numeric keyword chunk.
        # Exploratory: limits to 2 chunks per doc for source diversity.
        # Precision: no-op (keyword-first order already correct).
        results = _rerank(results, intent)

        # 9. Cap results — broader cap for synthesis queries
        max_chunks = _MAX_LLM_CHUNKS_BROAD if intent in (INTENT_EXPLORATORY, INTENT_ANALYTICAL) else _MAX_LLM_CHUNKS
        results = results[:max_chunks]

        if not results:
            if months_filter:
                available = self.rbac.get_available_months(dept_id)
                if available:
                    no_ans = (
                        f"No documents found for {', '.join(months_filter)}. "
                        f"The knowledge base currently has data for: {', '.join(available)}."
                    )
                else:
                    no_ans = (
                        f"No documents found for {', '.join(months_filter)} in the knowledge base."
                    )
            else:
                no_ans = "I couldn't find relevant information in the knowledge base to answer your question."
            if not chat_id:
                chat_id = self.rbac.create_chat(user_id, dept_id, title=question[:60])
            self.rbac.update_chat_title_if_empty(chat_id, question[:60])
            self.rbac.add_message(chat_id, "user", question)
            self.rbac.add_message(chat_id, "assistant", no_ans)
            return {"answer": no_ans, "citations": [], "chat_id": chat_id}

        # 10. Call LLM — pass intent and conversation history
        recent_history = history[-_HISTORY_WINDOW:] if history else []
        answer, relevant_files = call_llm(question, results, history=recent_history, intent=intent)

        # 11. Build citations — only files the LLM found relevant
        citations = []
        if relevant_files:
            seen: dict = {}
            for r in results:
                doc_id = str(r["document_id"])
                fname = r["file_name"]
                if doc_id in seen or fname not in relevant_files:
                    continue
                seen[doc_id] = True
                seaweed_url = self._get_seaweedfs_url(r["file_name"])
                citations.append({"name": fname, "document_id": doc_id, "url": seaweed_url})

        # 12. Persist chat + messages
        if not chat_id:
            chat_id = self.rbac.create_chat(user_id, dept_id, title=question[:60])
        self.rbac.update_chat_title_if_empty(chat_id, question[:60])
        persisted_answer = _compose_answer_with_sources(answer, citations)
        self.rbac.add_message(chat_id, "user", question)
        self.rbac.add_message(chat_id, "assistant", persisted_answer)
        self.rbac.log_retrieval(
            chat_id, user_id, dept_id, question,
            [str(r["chunk_id"]) for r in results],
            [float(r["similarity"]) for r in results],
        )

        return {"answer": persisted_answer, "citations": citations, "chat_id": chat_id}

    def get_chat_messages(self, chat_id: str, dept_id: str) -> list:
        return self.rbac.get_messages(chat_id, dept_id)

    def get_user_chats(self, user_id: str, dept_id: str) -> list:
        return self.rbac.get_user_chats(user_id, dept_id)
