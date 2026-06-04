# Virchow RAG — System Architecture

> Complete audit for ingestion and retrieval pipelines.
> Last updated: 2026-04-24

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Infrastructure Topology](#2-infrastructure-topology)
3. [Ingestion Pipeline — Detailed Architecture](#3-ingestion-pipeline)
4. [Retrieval Pipeline — Detailed Architecture](#4-retrieval-pipeline)
5. [Data Model](#5-data-model)
6. [Security Model](#6-security-model)
7. [Capacity Assessment for 20,000 Documents](#7-capacity-assessment)
8. [Known Issues and Bottlenecks](#8-known-issues-and-bottlenecks)
9. [Recommendations Before Bulk Upload](#9-recommendations-before-bulk-upload)

---

## 1. System Overview

Virchow RAG is a self-hosted document intelligence system that ingests PDFs (invoices, purchase orders, delivery challans, etc.), OCRs them with a vision-language model, stores vector embeddings in PostgreSQL+pgvector, and answers natural-language queries via a multi-modal retrieval pipeline backed by Ollama.

```
User / Admin Browser
        |
        v
   Next.js (port 3000)
        |  HTTP proxy
        v
  Retrieval API (port 8080)   <---- Ollama LLM (qwen3-vl:8b) on host
        |
        +-- pgvector  (similarity + keyword search)
        +-- ColPali   (visual page search)

   Admin Browser / API
        |
        v
  Ingest API (port 8000)
        |
        +-- Redis         (progress state, SSE pub/sub, dedup)
        +-- RabbitMQ      (job queue: priority / normal / large)
                |
                v
  Ingest Worker (port -)
        |
        +-- DotsOCR       (PDF page -> markdown, VLM on MPS/CPU)
        +-- ColPali       (page -> 128-dim visual embedding)
        +-- Ollama embed  (qwen3-embedding:8b -> 4096-dim vectors)
        +-- PostgreSQL    (documents + chunks + embeddings)
        +-- SeaweedFS     (raw PDF object storage)
```

---

## 2. Infrastructure Topology

### 2.1 Containerised Services (docker-compose.yml)

| Service | Container | Port(s) | Image |
|---|---|---|---|
| Redis | virchow_redis | 6379 | redis:7-alpine |
| RabbitMQ | virchow_rabbitmq | 5672, 15672 | rabbitmq:3.13-management-alpine |
| Ingest API | virchow_ingest_api | 8000 | virchow_ingest:latest |
| Ingest Worker | virchow_ingest_worker | - | virchow_ingest:latest |
| Retrieval | virchow_retrieval | 8080 | (built from ./retrieval) |
| Web (Next.js) | virchow_web | 3000 | (built from ./web) |

Both ingest containers share the same image (`virchow_ingest:latest`) and are differentiated by `RUN_TYPE=api` vs `RUN_TYPE=worker`. The `./ingest/uploads` directory is bind-mounted into both so the API writes files there and the worker reads them.

### 2.2 External Services (not containerised)

| Service | Address | Role |
|---|---|---|
| PostgreSQL | 192.168.10.10:5433 | virchow_dev database, pgvector extension |
| SeaweedFS Master | 192.168.10.10:9333 | Object storage master |
| SeaweedFS Filer | 192.168.10.10:8889 | HTTP filer / citation URLs |
| SeaweedFS S3 | 192.168.10.10:8333 | S3-compatible endpoint (aioboto3) |
| Ollama | host.docker.internal:11434 | LLM + embedding model host (MPS on Apple Silicon) |

### 2.3 Native Worker (recommended for production)

The `ingest-worker` Docker container runs OCR and ColPali on CPU only (no MPS passthrough), resulting in very slow throughput and OOM risk. The recommended alternative for Apple Silicon hosts is the native runner:

```bash
docker compose stop ingest-worker
cd ingest && bash run_native.sh
```

The native worker uses MPS (Metal Performance Shaders) giving ~5-10x faster OCR and ColPali throughput. It connects to the same RabbitMQ and PostgreSQL as the Docker stack.

### 2.4 Service Startup Order

```
redis (healthy)
    |
rabbitmq (healthy)
    |
ingest-api (healthy after 90s start_period)
    |
retrieval (healthy after 360s start_period -- ColPali cold start)
    |
web
```

---

## 3. Ingestion Pipeline

### 3.1 High-Level Flow

```
HTTP POST /upload
    |
    v
[Ingest API] FastAPI
    +-- Validate file (size <= 200 MB, type=PDF)
    +-- Save to ./uploads/{uuid}_{filename}
    +-- Create documents row (status=pending) in PostgreSQL
    +-- Publish job message to RabbitMQ (priority/normal/large queue)
    +-- Return { file_id, session_id } to client

[RabbitMQ Queue]
    |
    v
[PDFWorker thread] (pool of 3 feeder threads)
    +-- Consume message from queue
    +-- Decode DocJob JSON payload
    +-- Call RAGPipeline.submit(DocJob)

[Sequential Pipeline] (N_SEQ_WORKERS=2 worker threads)
    |
    Step 1: Read bytes from disk
    Step 2: Upload raw PDF to SeaweedFS (async via new event loop, non-fatal)
    Step 3: SHA-256 hash + Redis dedup check (TTL=1 year)
    Step 4: Resolve db_doc_id from upload_id (PostgreSQL lookup)
    Step 5: fitz -> PIL images (one per page, 300 DPI)
    Step 6: DotsOCR each page (serialised with _ocr_lock)
            +-- fetch_image -> _inference_with_hf -> post_process_output -> layoutjson2md
            +-- Strip base64 images from markdown
    Step 7: Assemble full markdown (page separators: "<!-- page N -->")
    Step 8: Parse filename metadata (party, month, doc_type from filename)
    Step 9: Extract OCR metadata (regex extractor: party_name, party_gstin,
            doc_date, doc_month, doc_number, doc_type, doc_unit,
            total_amount, tax_amount, net_amount, payment_terms, ref_doc_number)
    Step 10: ColPali visual embeddings (batches of 4 pages, serialised with
             _colpali_embed_lock; skipped if not yet loaded)
    Step 11: DocumentChunker -> chunk list (chunk_size=600, overlap=100)
    Step 12: Ollama embed (qwen3-embedding:8b, 4096-dim, serialised with
             _embed_lock, batches of 128 chunks)
    Step 13: Store chunks + embeddings to PostgreSQL
    Step 14: Update status = completed; write dedup key to Redis
    Step 15: Delete temp file from ./uploads
```

### 3.2 API Layer (ingest-api, RUN_TYPE=api)

**Entry Points:**

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check (Docker healthcheck + retrieval depends_on) |
| `/upload` | POST | Upload one or more PDFs; returns file_id + session_id per file |
| `/status/{file_id}` | GET | SSE stream of pipeline progress (stage + percent) |
| `/documents` | GET | List documents for a dept with full metadata |
| `/documents/{id}` | DELETE | Remove document + chunks + embeddings |
| `/admin/upload` | POST | Admin bulk upload (links to source_admin_upload_id) |
| `/ingest` | POST | Direct ingest from an existing file path or SeaweedFS key |

**Upload routing logic:**

Files are routed to one of three RabbitMQ queues based on size:
- `rag.q.priority` -- files < 1 MB (small invoices, 1-2 pages)
- `rag.q.normal` -- files 1 MB to 10 MB (standard PDFs)
- `rag.q.large` -- files > 10 MB (high-res scans, catalogues)

All queues have: TTL=3,600,000 ms (1 hour), DLX=rag.dlx -> rag.q.dead. The priority queue supports `x-max-priority=10`.

**Rate limiting:** 200 requests/hour per IP.

**Deduplication:** SHA-256 hash checked in Redis (1-year TTL). Files with identical content are fast-skipped without running OCR or embedding.

### 3.3 Worker Layer (ingest-worker, RUN_TYPE=worker)

**PDFWorker pool (worker/pool.py):**
- 3 PDFWorker threads, each with its own pika AMQP connection
- `prefetch_count=1` -- no worker takes a second job until the first is ACKed
- Non-blocking consumer: `_on_message` drops jobs to an in-process Queue; `_submit_thread` does file I/O and calls `RAGPipeline.submit()`
- MAX_RETRIES=3 before NACK (message routed to DLX)

**SequentialPipeline (N_SEQ_WORKERS=2):**
- Each worker thread processes one document fully before picking up the next
- In-memory doc queue: `Queue(maxsize=64)` -- backpressure prevents unbounded RAM use
- DotsOCR loaded once per process and shared across all workers via `_ocr_lock`
- Ollama embedding serialised across all workers via `_embed_lock`
- ColPali loaded in a background thread; docs processed before it's ready skip visual embeddings (no blocking)

### 3.4 OCR Stage — DotsOCR

- Model: `rednote-hilab/dots.ocr` (HuggingFace VLM)
- Weights: `/app/weights/DotsOCR` (8 GB, bind-mounted read-only)
- Prompt mode: `prompt_layout_all_en` (structured layout JSON -> markdown)
- Per-page flow: `fetch_image` (smart-resize to 11.28M px budget) -> `_inference_with_hf` (672x672 inference, bbox remapping) -> `post_process_output` -> `layoutjson2md`
- Base64 image blobs stripped before storage (reduces markdown size 10-100x)
- Thread safety: serialised with `_ocr_lock` -- only one forward pass at a time on MPS
- OCR DPI: 300 (fitz rasterisation)

### 3.5 Visual Embedding Stage — ColPali

- Model: `vidore/colpali-v1.2`
- Embedding dim: 128
- Scope: ALL pages after OCR assembly
- Batch size: 4 pages per forward pass
- Thread safety: serialised with `_colpali_embed_lock`
- Storage: `colpali_embeddings` table in PostgreSQL (doc_id, page_num, embedding)
- Loading: background thread at worker startup -- pipeline continues without it if not yet ready

### 3.6 Chunking Stage

- Chunker: `DocumentChunker` (markdown-aware sliding window)
- chunk_size: 600 tokens, chunk_overlap: 100 tokens
- Each chunk stores: text, token_count, page_num, quality_score (alpha ratio of text)
- Quality score: `alpha_chars / total_chars` -- used in retrieval to penalise low-quality OCR chunks

### 3.7 Embedding Stage — Ollama

- Model: `qwen3-embedding:8b`
- Embedding dim: 4096
- API: `POST http://host.docker.internal:11434/api/embed`
- Batch size: 128 chunks per call
- Thread safety: serialised with `_embed_lock`
- Timeout: 600s per batch call

### 3.8 OCR Metadata Extraction

Runs after full document OCR, before chunking. Regex-based extractor (`ocr_extractor.py`) extracts:

| Field | Pattern | Notes |
|---|---|---|
| `party_name` | ALL-CAPS line with LIMITED/PVT/LTD | First match |
| `party_gstin` | `GSTIN[:/]?[0-9]{2}[A-Z0-9]{12,13}` | 14-15 chars |
| `doc_date` | `Invoice Date / Date / Dt.` + DD/MM/YYYY | ISO format stored |
| `doc_month` | Derived from doc_date | e.g. "April" |
| `doc_number` | Invoice No. / Challan No. / Bill No. | First match |
| `doc_type` | Keyword list ordered by specificity | Export Invoice checked first |
| `doc_unit` | `UNIT[-\s]?(\d+)` | Stored as "U1", "U2" |
| `total_amount` | Grand Total -> Total: -> Rate USD row | Decimal |
| `tax_amount` | IGST/CGST/SGST amounts summed | Decimal |
| `net_amount` | Net Amount / Taxable Amount / Sub-Total | Decimal |
| `payment_terms` | Payment Terms[:/] | Free text |
| `ref_doc_number` | PO No. / L.R. No. / Ref. No. | First match |
| `extracted_text` | Full OCR markdown (HTML-cleaned) | Stored in DB |

Filename metadata is also extracted (`parse_filename_metadata`) from structured filenames like `FEB-U2-DN-24-25-001.pdf`.

### 3.9 SeaweedFS Storage

- Async client: `aioboto3` (S3-compatible)
- Key format: `raw/{session_id}/{filename}` in bucket `rag-docs`
- Called from sync worker thread via `asyncio.new_event_loop()` wrapper
- Non-fatal: storage failures are logged as WARNING -- ingestion continues regardless
- Citation URLs: `{filer_url}/buckets/{bucket}/raw/{uuid}/{filename}`

### 3.10 Status Tracking

Real-time progress tracked in Redis per `(file_id, session_id)` and streamed via SSE:

| Stage | Percent range |
|---|---|
| preprocessing | 5-10% |
| ocr | 20-70% (increments per page) |
| chunking | 72-75% |
| embedding | 80-84% |
| storing | 85-99% |
| done | 100% |
| error | 0% |

---

## 4. Retrieval Pipeline

### 4.1 High-Level Flow

```
POST /query  {question, user_id, dept_id, chat_id?}
    |
    v
RetrievalService.query()
    |
    Step 1:  Conversational shortcut check (regex)
             -> If hello/hi/thanks -> canned response, skip RAG
    |
    Step 2:  Load conversation history from PostgreSQL (last 6 messages)
             Extract active files (last assistant message with citations)
    |
    Step 3:  Detect document reference (filename or doc-ID-like token)
             Extract temporal filters (months, quarters, fiscal year)
             Detect doc_type from keywords
             Classify intent: precision | exploratory | analytical
    |
    Step 4a: ANALYTICAL path (if intent=analytical, no doc_name)
             -> SQL aggregation query (sum/count/group by month/type)
             -> If rows found: call_llm_sql -> return (skip vector search)
             -> If no rows: fall through to vector search
    |
    Step 4b: Embed question -> 4096-dim vector (Ollama qwen3-embedding:8b)
    |
    Step 5:  Vector search (pgvector cosine similarity)
             - precision: top_k=8
             - exploratory/analytical: top_k=16
             - doc_name known: scoped to that filename
             - active_files known: scoped to those filenames (max 2)
    |
    Step 6:  Keyword search (PostgreSQL ILIKE)
             - Words >= 5 chars, not in stop-word list
             - Scoped to doc_name / active_files / global
    |
    Step 6b: ColPali visual search
             - Embed question -> 128-dim ColPali vector
             - cosine search on colpali_embeddings table
             - Map page hits -> text chunks
    |
    Step 7:  Merge: deduplicate keyword + vector results
             RRF (Reciprocal Rank Fusion) with ColPali chunks
             score = sum(1 / (60 + rank)) across ranked lists
    |
    Step 8a: Quality penalty: multiply similarity x0.85 for 0.3 <= quality_score < 0.6
    Step 8b: Threshold filter: similarity >= 0.50 (SIM_THRESHOLD)
             (keyword hits bypass threshold)
    |
    Step 9:  Cap results
             - precision: max 10 chunks
             - exploratory/analytical: max 20 chunks
    |
    Step 10: Call LLM (qwen3-vl:8b, max_tokens=300, temperature=0.0)
             With conversation history (last 6 messages)
    |
    Step 11: Build citations (doc_id, filename, SeaweedFS URL)
             Only files the LLM marked as relevant
    |
    Step 12: Persist chat + messages + retrieval log to PostgreSQL
    |
    v
Return { answer, citations, chat_id }
```

### 4.2 Intent Classification

| Intent | Trigger | Search Strategy |
|---|---|---|
| `precision` | doc_name or active_files in conversation | Scoped vector + keyword search |
| `analytical` | Aggregation words (total, sum, list all, yearly, count) | SQL first, vector fallback |
| `exploratory` | Discovery words (tell me about, overview, who are our vendors) | Global search, top_k x2 |

### 4.3 Retrieval Filters

Temporal and type filters narrow vector and keyword search:
- **Month filter**: "February", "Q3", "third quarter" -> `months_filter` list
- **Fiscal year**: "FY 2024-25", "2024-25" -> `fiscal_year` string
- **Doc type**: "purchase order", "po", "invoice" -> `detected_doc_type`

If a specific `doc_name` is identified, all filters are cleared (document-level precision takes priority).

### 4.4 RRF Merge Formula

```
score(chunk) = sum(  1 / (60 + rank_in_list)  )
               for each list (text_merged, colpali_chunks)
               where rank is 0-indexed position

Deduplication by chunk_id -- highest score kept on collision.
```

### 4.5 LLM Integration

- Model: `qwen3-vl:8b` (Ollama, host.docker.internal:11434)
- Context: retrieved chunks + conversation history (last 6 messages)
- Temperature: 0.0
- max_tokens: 300 (docker-compose override)
- Two call modes:
  - `call_llm(question, chunks, history, intent)` -- RAG answer generation
  - `call_llm_sql(question, sql_rows, history)` -- analytical answer from structured data

### 4.6 Conversation Continuity

- Last 6 messages loaded on each query
- `_extract_active_files`: scans the last assistant message for bold-formatted citations (`**filename.pdf**`)
- Active files scope all subsequent searches to those documents until the conversation moves on

### 4.7 Retrieval API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/query` | POST | Main RAG query (JWT required) |
| `/chat/{id}/messages` | GET | Fetch conversation messages |
| `/chats` | GET | List user's conversations |
| `/documents` | GET | Proxy to ingest-api /documents |
| `/auth/login` | POST | Issue JWT (HS256, 24hr expiry) |

---

## 5. Data Model

### 5.1 Key Tables

```
documents
  id UUID PK
  file_name TEXT
  file_path TEXT          -- local path (./uploads/...) or SeaweedFS key
  dept_id UUID
  uploaded_by UUID
  content_hash TEXT       -- SHA-256
  page_count INT
  embed_status TEXT       -- pending | processing | completed | failed
  ocr_used BOOL
  party_name TEXT
  party_gstin TEXT
  doc_date DATE
  doc_month TEXT          -- "January" ... "December"
  doc_number TEXT
  doc_type TEXT           -- "Invoice", "Purchase Order", "Tax Invoice", etc.
  doc_unit TEXT           -- "U1", "U2", etc.
  total_amount DECIMAL
  tax_amount DECIMAL
  net_amount DECIMAL
  payment_terms TEXT
  ref_doc_number TEXT
  extracted_text TEXT     -- full OCR markdown
  created_at TIMESTAMP
  last_embedded_at TIMESTAMP

chunks
  id UUID PK
  doc_id UUID FK -> documents
  chunk_index INT
  chunk_text TEXT
  chunk_token_count INT
  page_num INT
  quality_score FLOAT     -- 0.0-1.0, alpha char ratio
  source_user_upload_id UUID
  source_admin_upload_id UUID
  created_at TIMESTAMP

embeddings
  id UUID PK
  chunk_id UUID FK -> chunks
  dept_id UUID
  embedding vector(4096)  -- pgvector, cosine similarity
  source_user_upload_id UUID
  source_admin_upload_id UUID
  created_at TIMESTAMP

colpali_embeddings
  id UUID PK
  doc_id UUID FK -> documents
  dept_id UUID
  page_num INT
  embedding vector(128)   -- pgvector, cosine similarity
  created_at TIMESTAMP

user_uploads
  id UUID PK
  file_id TEXT            -- matches Redis key
  session_id TEXT
  file_name TEXT
  file_size BIGINT
  status TEXT             -- pending | processing | completed | failed
  dept_id UUID
  user_id UUID
  created_at TIMESTAMP

chats
  id UUID PK
  user_id UUID
  dept_id UUID
  title TEXT
  created_at TIMESTAMP

messages
  id UUID PK
  chat_id UUID FK -> chats
  role TEXT               -- user | assistant
  content TEXT
  created_at TIMESTAMP

retrieval_log
  id UUID PK
  chat_id UUID
  user_id UUID
  dept_id UUID
  question TEXT
  chunk_ids TEXT[]
  similarities FLOAT[]
  created_at TIMESTAMP
```

### 5.2 Redis Key Space

| Key pattern | TTL | Purpose |
|---|---|---|
| `stage:{file_id}:{session_id}` | 24h | Pipeline progress JSON |
| `dedup:{sha256}` | 1 year | Deduplication: hash -> doc_id |
| `session:{session_id}` | 24h | Session metadata |
| `worker:hb:{worker_id}` | 10s | Worker heartbeat |

---

## 6. Security Model

| Concern | Implementation |
|---|---|
| Authentication | JWT HS256, 24hr expiry, issued by retrieval /auth/login |
| RBAC | dept_id scoping on all DB queries -- users only see their department's documents |
| Rate limiting | 200 req/hr per IP on ingest API |
| Input validation | Max PDF size 200 MB, max pages 2000, max batch 100 files |
| Secrets | PG_PASSWORD required at startup (hard fail if missing); JWT_SECRET warns if default |
| CORS | Configurable via CORS_ORIGINS env (default: localhost:3000) |

**Important:** `JWT_SECRET=change-this-in-production` is the current docker-compose value. Change this before exposing retrieval to any external network.

---

## 7. Capacity Assessment

### 7.1 Assumptions for 20,000 Document Upload

Based on the existing 5-document test corpus (typical invoice PDFs, 1-4 pages each):

| Parameter | Estimate |
|---|---|
| Average pages per document | 2 pages |
| Total pages | ~40,000 |
| Average chunks per page | ~5 |
| Total chunks | ~200,000 |
| Average embedding size | 4096 dim x 4 bytes = 16 KB |
| Total embedding storage | ~3.2 GB |
| Average OCR output per page | ~2 KB |
| Total extracted_text storage | ~80 MB |

### 7.2 Throughput Estimates

| Runner | OCR time/page | Embed time/chunk | Docs/hour |
|---|---|---|---|
| Docker (CPU) | 30-120s | 0.5-2s | 10-30 |
| Native (MPS, Apple M3) | 3-15s | 0.2-0.5s | 100-400 |

**At 20,000 documents (40,000 pages):**
- Docker worker: 40,000 pages x 60s avg = ~667 hours. Not viable for bulk upload.
- Native worker (N_SEQ_WORKERS=2): ~55-110 hours.
- Native worker (N_SEQ_WORKERS=4): ~28-55 hours.

### 7.3 Bottleneck Analysis

**Bottleneck 1: DotsOCR (dominant)**
- Single VLM forward pass per page, serialised by `_ocr_lock`
- MPS throughput ~6-10 pages/min on Apple M3
- Cannot be parallelised on a single MPS device without batching (current architecture runs one page at a time)

**Bottleneck 2: ColPali**
- 4 pages/batch, serialised by `_colpali_embed_lock`
- At MPS: ~2s per batch -> 40,000 pages / 4 x 2s = ~5.5 hours total
- Runs after OCR for each document, so overlaps with the OCR of the next doc

**Bottleneck 3: Ollama Embedding**
- Serialised across all workers via `_embed_lock`
- At ~200ms per 128-chunk batch: 200,000 chunks / 128 = ~1,562 batches x 0.2s = ~5 minutes total (not a bottleneck)

**Bottleneck 4: PostgreSQL**
- 200,000 chunk inserts + 200,000 embedding inserts
- Connection pool: max(20, upload_workers+15) = 20 connections
- At ~1ms per insert: ~400s = ~7 minutes total (not a bottleneck)

**Bottleneck 5: RabbitMQ TTL**
- Current queue TTL = 1 hour
- If the worker falls more than 1 hour behind, messages expire to the dead letter queue
- With 20,000 docs queued at once, the queue will drain over many hours
- **Action required: increase TTL before bulk upload**

**Bottleneck 6: In-memory queue overflow**
- `SEQ_QUEUE_SIZE=64` -- if all 64 slots are full, new submissions are dropped (logged as error)
- The 3 PDFWorker feeder threads will back off, leaving messages in RabbitMQ (safe)
- No data loss -- just back-pressure

### 7.4 Storage Requirements

| Store | Current (5 docs) | After 20K docs |
|---|---|---|
| PostgreSQL (embeddings) | ~50 MB | ~3.5 GB |
| PostgreSQL (chunks + docs) | ~5 MB | ~500 MB |
| PostgreSQL (colpali) | ~5 MB | ~400 MB |
| SeaweedFS (raw PDFs) | ~25 MB | ~10-50 GB |
| Redis (dedup + state) | <1 MB | ~20 MB |

Ensure PostgreSQL has at least **5 GB free disk** before starting. A pgvector HNSW index on embeddings requires ~1.5x the raw data size (~5 GB additional for the index structure).

---

## 8. Known Issues and Bottlenecks

### 8.1 CRITICAL: Ingest Worker OOM (Exit 137)

The Docker ingest-worker is OOM-killed when processing large PDFs. DotsOCR (~4 GB) + ColPali (~2 GB) are both resident in memory simultaneously.

**Impact:** Worker restarts. Job is NACK'd and retried up to 3 times. After 3 failures, job goes to `rag.q.dead`.

**Fix:** Use native runner (MPS uses Metal shared memory, lower peak RAM than CPU tensors). Increase Docker memory limit for ingest-worker as a fallback.

### 8.2 CRITICAL: RabbitMQ TTL vs. Long Processing Time

The 1-hour TTL means a job expires if the worker hasn't ACKed it within 1 hour. At Docker CPU throughput (10-30 docs/hr), 20,000 documents will cause most queue entries to expire before processing.

**Fix before bulk upload:** Change `x-message-ttl` to 86,400,000 (24 hours) in `rabbitmq_broker.py`:

```python
# ingest/src/database/rabbitmq_broker.py
# Change in all three queue declarations:
"x-message-ttl": 86_400_000,   # was 3_600_000
```

Then restart RabbitMQ: `docker compose restart rabbitmq`.

### 8.3 IMPORTANT: Sequential Processing Rate

With `N_SEQ_WORKERS=2` and OCR as the dominant step, only 2 pages are being OCR'd simultaneously. For 20,000 documents this is the primary time constraint.

**Fix:** Set `N_SEQ_WORKERS=4` in the native runner env (safe on MPS since all 4 workers share a single serialised OCR lock; the gain comes from overlapping ColPali, chunking, and embedding stages).

### 8.4 ColPali CPU Block in Retrieval

The retrieval service lazy-loads ColPali on the first visual search query. On CPU in Docker, this takes 3-10 minutes and blocks all HTTP requests during that time.

**Fix:** Pre-warm the retrieval service with a dummy query after startup before exposing to users.

### 8.5 SeaweedFS Citation URLs

The retrieval service constructs citation URLs using the `file_path` column, which stores the local temp path (`./uploads/{uuid}_{filename}`). The UUID parsing (first 36 chars of basename) may not match the `session_id` used as the SeaweedFS directory key.

**Action:** After bulk upload, test that citation URLs from retrieved chunks correctly resolve to files in SeaweedFS.

### 8.6 ColPali Pages Skipped on Cold Start

Documents processed before ColPali finishes its background load will have no visual embeddings. These documents will still be fully retrievable via text search but will miss visual search coverage.

**Fix:** After bulk upload, a backfill script can regenerate ColPali embeddings for any document where `colpali_embeddings` rows are missing.

### 8.7 max_tokens=300 May Truncate Answers

The docker-compose sets `MAX_TOKENS=300` for the retrieval LLM. For complex analytical queries covering many documents, answers will be cut off.

**Fix:** Increase to 600-1000 in docker-compose.yml `retrieval` service environment.

---

## 9. Recommendations Before Bulk Upload

### 9.1 Must Do (blockers)

**1. Switch to native worker**
```bash
docker compose stop ingest-worker
cd ingest && bash run_native.sh
```
Without MPS, 20,000 documents will take 667+ hours and repeatedly OOM-kill the container.

**2. Increase RabbitMQ TTL to 24 hours**

Edit `ingest/src/database/rabbitmq_broker.py` -- change `x-message-ttl` from 3,600,000 to 86,400,000 in all three queue declarations, then rebuild and restart:
```bash
docker compose build ingest-api ingest-worker
docker compose up -d ingest-api ingest-worker
docker compose restart rabbitmq
```

**3. Verify PostgreSQL disk space (need 5 GB+ free)**
```sql
SELECT pg_size_pretty(pg_database_size('virchow_dev'));
SELECT pg_size_pretty(pg_tablespace_disk_usage);
```

**4. Change JWT_SECRET**

In docker-compose.yml:
```yaml
retrieval:
  environment:
    JWT_SECRET: <your-strong-secret-here>
```

### 9.2 Strongly Recommended

**5. Increase N_SEQ_WORKERS to 4**

In the native runner env or docker-compose.yml ingest-worker:
```bash
N_SEQ_WORKERS=4 bash run_native.sh
```

**6. Increase MAX_TOKENS to 600**

In docker-compose.yml retrieval environment:
```yaml
MAX_TOKENS: "600"
```

**7. Pre-warm ColPali in retrieval**
```bash
curl -X POST http://localhost:8080/query \
  -H "Authorization: Bearer <token>" \
  -d '{"question":"warmup","user_id":"...","dept_id":"..."}'
```

**8. Upload in batches of 500-1,000**

Avoid flooding RabbitMQ with all 20,000 at once. Upload in chunks, monitor the dead letter queue at http://localhost:15672, and verify each batch before proceeding.

**9. Build pgvector HNSW indexes after bulk upload**

Run these after all documents are processed to maximise query speed:
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS embeddings_vec_idx
  ON embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY IF NOT EXISTS colpali_vec_idx
  ON colpali_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

### 9.3 Monitoring During Bulk Upload

| Signal | How to check |
|---|---|
| Queue depths | http://localhost:15672 (guest/guest) |
| Dead letter queue | RabbitMQ management UI, queue: rag.q.dead |
| Doc status counts | `SELECT embed_status, count(*) FROM documents GROUP BY embed_status;` |
| Worker throughput | `docker compose logs -f ingest-worker` (or native runner terminal) |
| Redis progress | `redis-cli hgetall "stage:{file_id}:{session_id}"` |
| SeaweedFS storage | http://192.168.10.10:9333 (master dashboard) |

---

## Appendix: Configuration Reference

### Ingest (ingest/src/config.py + docker-compose.yml)

| Parameter | Current Value | Notes |
|---|---|---|
| chunk_size | 600 | Tokens per chunk |
| chunk_overlap | 100 | Overlap between chunks |
| max_pdf_size_mb | 200 | Max upload size |
| max_pdf_pages | 2000 | Max pages per PDF |
| max_batch_files | 100 | Max files per batch upload |
| N_SEQ_WORKERS | 2 (Docker) | Sequential pipeline workers |
| SEQ_QUEUE_SIZE | 64 | In-memory doc queue size |
| DEDUP_TTL | 31,536,000s (1 year) | Redis dedup TTL |
| SESSION_TTL | 86,400s (24hr) | Session TTL |
| PRIORITY_MAX_KB | 1,024 KB | Threshold for priority queue |
| LARGE_MIN_KB | 10,240 KB | Threshold for large queue |
| x-message-ttl | 3,600,000ms (1hr) | **Increase to 86,400,000 before bulk upload** |
| MAX_RETRIES | 3 | AMQP retries before DLX |
| OCR_DPI | 300 | fitz rasterisation DPI |
| EMBEDDING_DIM | 4096 | qwen3-embedding:8b output dim |

### Retrieval (retrieval/src/config.py + docker-compose.yml)

| Parameter | Current Value | Notes |
|---|---|---|
| TOP_K | 8 | Vector search top-k |
| SIM_THRESHOLD | 0.50 | Minimum similarity to include |
| MAX_TOKENS | 300 | **Consider increasing to 600** |
| LLM_MODEL | qwen3-vl:8b | Ollama LLM model |
| EMBEDDING_DIM | 4096 | Query embedding dim |
| jwt_expire_hours | 24 | JWT token lifetime |
| _MAX_LLM_CHUNKS | 10 | Max chunks for precision queries |
| _MAX_LLM_CHUNKS_BROAD | 20 | Max chunks for exploratory/analytical |
| _HISTORY_WINDOW | 6 | Conversation messages to include |
