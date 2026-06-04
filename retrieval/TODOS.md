# TODOS

Deferred work captured during plan-eng-review on 2026-04-21.

---

## TODO-1: LLM entity extraction — JSON parse failure handling

**What:** Retry logic + fallback + failure logging for entity extraction in `src/ingestion/ingestion_pipeline.py`.

**Why:** qwen2.5:14b returns partial/non-JSON ~5-10% of the time. Unhandled, the document gets ingested with null `party_name`, `total_amount`, etc. Those nulls silently drop the document from SQL aggregations. Zero-tolerance financial system — a missing doc is a wrong total.

**How to apply:**
1. Try extraction up to 3 times with the full prompt
2. On 3rd failure, try a simplified single-field prompt (just `party_name` and `total_amount`)
3. If still failing, store nulls and log to a `failed_extractions` table: `(document_id, step, error, attempted_at)`
4. Expose a `/admin/failed-extractions` endpoint so operators can see what needs manual review

**Depends on:** Phase 2 ingestion pipeline implementation

---

## TODO-2: Upload route — `/upload` endpoint in routes.py

**What:** A POST `/documents/upload` endpoint that accepts a PDF, stores it in SeaweedFS, and triggers the ingestion pipeline.

**Why:** Currently there's no upload route. The ingestion pipeline (Phase 2) can only be called from the CLI backfill script. New documents added after Phase 3 backfill have no automated ingestion path. The system is write-only via manual DB inserts.

**How to apply:**
1. `POST /documents/upload` accepts multipart file + department_id
2. Store in SeaweedFS, record `file_path` in documents table
3. Run ingestion pipeline (async background task or sync with timeout)
4. Return `{document_id, status, estimated_time}` immediately
5. Poll `/documents/{id}/status` for completion

**Depends on:** Phase 2 ingestion pipeline implementation

---

## TODO-3: DotsOCR vLLM server health check before ingestion/backfill

**What:** Health check at startup of any ingestion job — fail fast if vLLM server is not running.

**Why:** `wait_for_vllm()` already exists in `handler.py` but is commented out. If vLLM isn't running, every document fails OCR silently — 335 documents in backfill get ingested with null text. The operator doesn't know until they notice all `quality_score` values are 0.

**How to apply:**
- Backfill script entry point: call `wait_for_vllm(timeout=60)` — fail with clear message if timeout
- Upload route: health check on ingestion worker startup
- Add `DOTSOCR_VLLM_URL` env var (default `http://127.0.0.1:8000`) to docker-compose

**Depends on:** Phase 2 implementation, docker-compose service definition for vLLM

---

## TODO-4: Schema migrations — transaction wrapping

**What:** Each migration function wrapped in a database transaction (BEGIN/COMMIT/ROLLBACK).

**Why:** If migration crashes mid-run (disk full, DB connection dropped), the schema lands in a partially-applied state. `schema_migrations` records nothing, so next deploy re-runs the migration and hits errors on already-added columns. Manual recovery on a live pharma system is painful and risky.

**How to apply:**
```python
def run_migration_001(conn):
    try:
        conn.autocommit = False
        cur = conn.cursor()
        # ... all ALTER TABLE statements ...
        cur.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True
```

**Depends on:** Phase 1 schema migration implementation

---

## TODO-5: Party name normalization

**What:** Normalize vendor/party names to a canonical form for reliable SQL aggregation.

**Why:** After Phase 3 backfill, `analytical_query()` will return fragmented results for the same vendor: 'ABC Traders', 'ABC Traders Pvt Ltd', 'M/s ABC Traders', 'ABC TRADERS PVT. LTD.' are stored as 4 different party_names. Revenue queries by vendor name will miss records.

**How to apply:**
1. After Phase 3 backfill, run `SELECT DISTINCT party_name FROM documents ORDER BY party_name` — see the actual fragmentation
2. Simple normalization: UPPER() + strip punctuation + strip "PVT LTD / PVT. LTD." suffixes
3. If 50+ distinct forms: use RapidFuzz fuzzy deduplication at ingestion time (threshold ~85%)
4. Add `party_name_canonical TEXT` column alongside `party_name` (raw value preserved)

**Depends on:** Phase 3 backfill completion (need real data to assess extent)
