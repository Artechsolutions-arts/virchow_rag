# Virchow RAG — Pipeline Configuration Reference

**Generated:** 2026-05-30 | **Pipeline status:** Complete (14,263 / 14,263 docs)

---

## Chunking

| Parameter | Value | Config key |
|-----------|-------|------------|
| Chunk size | **600 tokens** | `CHUNK_SIZE` |
| Chunk overlap | **100 tokens** | `CHUNK_OVERLAP` |
| Tokenization | Whitespace-split word count (approximate) | — |

Chunks are produced by `DocumentChunker` in `ingest/src/ingestion/chunking/chunker.py`. The 600-token window was chosen to fit comfortably within the `qwen3-embedding:8b` 32k-token context limit while keeping retrieval precision high. Overlap of 100 tokens prevents context loss at chunk boundaries.

---

## Embedding

| Parameter | Value | Config key |
|-----------|-------|------------|
| Model | **qwen3-embedding:8b** | `EMBEDDING_MODEL` / `OLLAMA_EMBED_MODEL` |
| Embedding dimension (dp) | **4096** | `EMBEDDING_DIM` |
| Backend | Ollama | `OLLAMA_BASE_URL` |
| Ollama base URL | `http://localhost:11434` | `OLLAMA_BASE_URL` |
| Batch size | **64 chunks** | `EMBEDDING_BATCH_SIZE` |
| Device | CPU (configurable) | `EMBEDDING_DEVICE` |

The embedding dimension of **4096** is the native output size of `qwen3-embedding:8b`. This value must match the pgvector column definition (`vector(4096)`) — changing it requires a full re-index.

---

## Indexing (pgvector)

| Parameter | Value |
|-----------|-------|
| Database | PostgreSQL (`virchow_dev`) |
| Host | `192.168.10.10:5433` |
| Vector extension | pgvector |
| Vector column type | `vector(4096)` |
| Distance metric | Cosine similarity |
| Deduplication | SHA-256 content hash, TTL **1 year** (31,536,000 s) |
| Unique constraint | `uq_doc_file_dept` on `(file_name, department_id)` |

---

## OCR

| Parameter | Value | Config key |
|-----------|-------|------------|
| Engine | **DotsOCR** (VLM layout parser) | — |
| Model | `rednote-hilab/dots.ocr` (HuggingFace) | `DOTS_OCR_MODEL` |
| Backend | HuggingFace (not Ollama) | `DOTS_OCR_USE_HF=true` |
| Prompt mode | `prompt_layout_all_en` | `DOTS_OCR_PROMPT_MODE` |
| fitz preprocessing | Enabled | `DOTS_OCR_FITZ_PREPROCESS=true` |
| Resolution (DPI) | **200** | `ocr_dpi` |
| Weights path | `./weights/DotsOCR` | `DOTS_OCR_WEIGHTS` |
| Fallback on failure | Enabled | `ocr_fallback=True` |
| Quality threshold (retrieval) | **0.3** min | `OCR_QUALITY_MIN` |
| Quality penalty cap (retrieval) | **0.6** max | `OCR_QUALITY_PENALTY_MAX` |

OCR runs per-page via `HybridOCR` in `ingest/src/ingestion/ocr/ocr_engine.py`, wrapped with 2-retry backoff in the orchestrator.

---

## Retrieval

| Parameter | Value | Config key |
|-----------|-------|------------|
| Top-K (retrieval stage) | **50** candidates | `top_k_retrieval` |
| Top-K (rerank stage) | **5** results returned | `top_k_rerank` |
| Similarity threshold | **0.45** | `SIM_THRESHOLD` |
| Vector search weight (alpha) | **0.6** | `alpha` |
| Keyword search weight (beta) | **0.4** | `beta` |
| Search mode | Hybrid (vector + keyword) | — |

Retrieval-side `TOP_K` env default is 20 (retrieval service); ingest-side default is 50. The ingest-side value governs pipeline indexing coverage.

---

## LLM (Answer Generation)

| Parameter | Value | Config key |
|-----------|-------|------------|
| Model | **qwen2.5:latest** | `LLM_MODEL` |
| Backend | Ollama | `LLM_URL` |
| Ollama URL | `http://ollama:11434` (container) | `LLM_URL` |
| Max output tokens | **2048** | `MAX_TOKENS` |
| Temperature | **0.0** (deterministic) | `LLM_TEMPERATURE` |

---

## Queue Routing (RabbitMQ)

| Queue | Condition | Routing key |
|-------|-----------|-------------|
| `rag.q.priority` | File size < **1 MB** (1,024 KB) | `job.priority` |
| `rag.q.normal` | 1 MB – 10 MB | `job.normal` |
| `rag.q.large` | File size > **10 MB** (10,240 KB) | `job.large` |
| `rag.q.dead` | Max retries exceeded | DLX |

| Parameter | Value |
|-----------|-------|
| Exchange | `rag.jobs` |
| Dead-letter exchange | `rag.dlx` |
| Max retries per job | **3** |
| Workers | **3** (parallel) |
| Feeder threads per worker | **4** (= 12 total concurrent slots) |

---

## PDF Limits

| Parameter | Value | Config key |
|-----------|-------|------------|
| Max file size | **200 MB** | `max_pdf_size_mb` |
| Max pages | **2,000** | `max_pdf_pages` |
| Max batch upload | **100 files** | `max_batch_files` |
| Upload workers | **1** (sequential) | `UPLOAD_WORKERS` |

---

## Auth (JWT)

| Parameter | Value | Config key |
|-----------|-------|------------|
| Algorithm | **HS256** | hardcoded |
| Token expiry | **24 hours** | `JWT_EXPIRE_HOURS` |
| Secret | from env | `JWT_SECRET` |

---

## Object Storage (SeaweedFS)

| Parameter | Value | Config key |
|-----------|-------|------------|
| Filer URL | `http://192.168.10.10:889` | `SEAWEEDFS_FILER_URL` |
| Master URL | `http://192.168.10.10:933` | `SEAWEEDFS_MASTER_URL` |
| S3 endpoint | `http://192.168.10.10:833` | `SEAWEEDFS_S3_ENDPOINT` |
| Bucket | `rag-docs` | `SEAWEEDFS_BUCKET` |
| Upload enabled | `true` | `SEAWEEDFS_UPLOAD_TO_STORAGE` |

SeaweedFS stores uploaded PDFs and extracted text. Failures are non-fatal (logged as warnings, pipeline continues).

---

## Redis (State & Progress)

| Parameter | Value | Config key |
|-----------|-------|------------|
| Host | `localhost` | `REDIS_HOST` |
| Port | `6379` | `REDIS_PORT` |
| Session TTL | **86,400 s** (24 h) | `SESSION_TTL` |
| File progress TTL | **86,400 s** (24 h) | `FILE_TTL` |
| Worker heartbeat TTL | **10 s** | `WORKER_HB_TTL` |
| Deduplication TTL | **31,536,000 s** (1 year) | `DEDUP_TTL` |

---

## Rate Limiting

| Parameter | Value |
|-----------|-------|
| Max uploads | **200 / hour** |
| Deduplication | Skip re-uploads (SHA-256 hash match) |

---

## Worker Memory Management

| Parameter | Value |
|-----------|-------|
| RSS kill threshold | **62 GB** per worker |
| Kill/restart overhead | ~10–15 min downtime per cycle |
| DotsOCR model load time | ~5–10 min |
| RSS growth rate | ~0.3–0.5 GB per 5 min |
| Total kill cycles (full pipeline) | **21** |

Workers are SIGKILL'd by the supervisor when RSS hits 62 GB. Orphaned `processing` documents are reset to `pending` and re-queued before the replacement worker starts.

---

## ColPali (Visual Search)

| Parameter | Value | Config key |
|-----------|-------|------------|
| Enabled | **false** (disabled) | `ENABLE_COLPALI=false` |

ColPali (3B model visual search) is available but disabled by default to avoid CPU load blocking queries.

---

## Summary: Key Numbers at a Glance

| What | Value |
|------|-------|
| Embedding dimension | **4096** |
| Chunk size | **600 tokens** |
| Chunk overlap | **100 tokens** |
| Similarity threshold | **0.45** |
| Vector / keyword weight | **0.6 / 0.4** |
| Top-K retrieval | **50** candidates → **5** returned |
| LLM temperature | **0.0** |
| Total corpus (May 2026) | **14,263 documents** |
| Total chunks indexed | ~**78,000+** (est. ~5.5 pages × ~10 chunks/page) |
