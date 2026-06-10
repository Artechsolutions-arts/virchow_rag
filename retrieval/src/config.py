import os
import logging

logger = logging.getLogger(__name__)

PG_HOST     = os.getenv("PG_HOST",     "postgres")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "virchow_dev")
PG_USER     = os.getenv("PG_USER",     "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")

EMBEDDING_MODEL    = "qwen3-embedding:8b"
EMBEDDING_DIM      = 4096
OLLAMA_BASE_URL    = "http://localhost:11434"
OLLAMA_EMBED_MODEL = "qwen3-embedding:8b"

_INSECURE_JWT_DEFAULT = "change-this-secret-in-production"

# Max allowed question length (chars) — prevents DoS via oversized inputs
MAX_QUESTION_LENGTH = 2000


class RAGConfig:
    def __init__(self):
        self.embedding_model      = os.getenv("EMBEDDING_MODEL",    EMBEDDING_MODEL)
        self.embedding_dim        = int(os.getenv("EMBEDDING_DIM", str(EMBEDDING_DIM)))
        self.embedding_device     = os.getenv("EMBEDDING_DEVICE",  "cpu")
        self.ollama_base_url      = os.getenv("OLLAMA_BASE_URL",   OLLAMA_BASE_URL)
        self.ollama_embed_model   = os.getenv("OLLAMA_EMBED_MODEL", OLLAMA_EMBED_MODEL)
        self.top_k_retrieval      = int(os.getenv("TOP_K", "20"))
        self.similarity_threshold = float(os.getenv("SIM_THRESHOLD", "0.45"))
        # LLM (Ollama-compatible endpoint)
        self.llm_url              = os.getenv("LLM_URL", "http://ollama:11434")
        self.llm_model            = os.getenv("LLM_MODEL", "qwen2.5:latest")
        self.max_tokens           = int(os.getenv("MAX_TOKENS", "2048"))
        self.temperature          = float(os.getenv("LLM_TEMPERATURE", "0.0"))
        # JWT
        self.jwt_secret           = os.getenv("JWT_SECRET", _INSECURE_JWT_DEFAULT)
        self.jwt_algorithm        = "HS256"
        self.jwt_expire_hours     = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
        # ColPali visual search — disable when not needed to avoid 3B model CPU load blocking queries
        self.enable_colpali       = os.getenv("ENABLE_COLPALI", "false").lower() == "true"
        # When the text-RAG path returns "no relevant info" or empty results,
        # fall back to ColPali → render top pages → vision-language LLM
        # (e.g. qwen3-vl:8b). Useful when OCR mis-handled pages that ColPali
        # still captured as images.
        #
        # ON by default. The encoder load (3B-param PaliGemma + LoRA on CPU,
        # several minutes the first time) runs in a daemon thread at
        # service startup (see RetrievalService._start_colpali_warmup), so
        # the worker stays responsive throughout. Until the warmup finishes,
        # the fallback method silently no-ops and queries fall through to
        # the standard "no relevant info" message. Set this to false if
        # you want to skip the warmup entirely (e.g. CI smoke tests).
        self.enable_colpali_fallback = os.getenv("ENABLE_COLPALI_FALLBACK", "true").lower() == "true"
        self.colpali_fallback_top_k  = int(os.getenv("COLPALI_FALLBACK_TOP_K", "4"))
        self.colpali_fallback_vl_model = os.getenv("COLPALI_FALLBACK_VL_MODEL", "qwen3-vl:8b")
        self.colpali_fallback_page_dpi = int(os.getenv("COLPALI_FALLBACK_PAGE_DPI", "144"))
        # SeaweedFS
        self.seaweedfs_filer_url  = os.getenv("SEAWEEDFS_FILER_URL", "http://192.168.10.10:889")
        self.seaweedfs_bucket     = os.getenv("SEAWEEDFS_BUCKET", "rag-docs")
        # CORS — comma-separated list of allowed origins
        raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
        self.cors_origins         = [o.strip() for o in raw_origins.split(",") if o.strip()]
        # OCR quality thresholds (tune after Phase 0 validation)
        self.ocr_quality_min      = float(os.getenv("OCR_QUALITY_MIN", "0.3"))
        self.ocr_quality_penalty_max = float(os.getenv("OCR_QUALITY_PENALTY_MAX", "0.6"))
        # DotsOCR local model weights (HF mode — no vLLM server needed)
        self.dotsocr_weights_path = os.getenv("DOTSOCR_WEIGHTS_PATH", "/app/weights/DotsOCR")
        # Complete backend ingestion service (handles OCR + chunking + embedding via MPS)
        # Empty string means disabled — rag_pipeline falls back to its own BackgroundTasks ingestion.
        self.ingest_url = os.getenv("INGEST_URL", "").rstrip("/")

        if self.jwt_secret == _INSECURE_JWT_DEFAULT:
            logger.warning(
                "JWT_SECRET is using the insecure default value. "
                "Set the JWT_SECRET environment variable before going to production."
            )

        # ── Runtime model selection ─────────────────────────────────────────────
        # Admins can override `llm_model` via the /admin/configuration/llm page,
        # which writes to the app_settings table. We read it through a tiny
        # 15-second cache so per-request DB lookups stay cheap.
        self._llm_model_cache = (0.0, None)

    def effective_llm_model(self) -> str:
        """LLM model to use right now: DB override (if set) else env-configured."""
        import time
        ts, cached = self._llm_model_cache
        if cached and (time.time() - ts) < 15.0:
            return cached
        override = None
        try:
            from src.database.postgres_db import get_pg_pool, RBACManager
            pool = get_pg_pool(minconn=1, maxconn=2)
            rbac = RBACManager(pool)
            override = rbac.get_setting("llm_model")
        except Exception:
            override = None
        chosen = (override or self.llm_model).strip() or self.llm_model
        self._llm_model_cache = (time.time(), chosen)
        return chosen


cfg = RAGConfig()
