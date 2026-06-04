"""
Schema migrations for the virchow_rag RAG pipeline.

Each migration is wrapped in a transaction. If it crashes mid-run,
postgres_db.schema_migrations records nothing and the next deploy retries cleanly.

Run order: run_all_migrations(conn) — call once at startup after create_schema().
"""

import logging
from src.config import EMBEDDING_DIM

logger = logging.getLogger(__name__)

_MIGRATIONS: list = []


def _migration(version: int):
    def decorator(fn):
        _MIGRATIONS.append((version, fn))
        return fn
    return decorator


def run_all_migrations(conn) -> None:
    """Run every pending migration in version order. Safe to call on every startup."""
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    for version, fn in sorted(_MIGRATIONS, key=lambda x: x[0]):
        if version not in applied:
            logger.info("[Migration] Applying migration %03d …", version)
            fn(conn)
            logger.info("[Migration] Migration %03d applied.", version)
        else:
            logger.debug("[Migration] Migration %03d already applied, skipping.", version)


def _ensure_migrations_table(conn) -> None:
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.close()
    conn.autocommit = False


def _applied_versions(conn) -> set:
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations")
    versions = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.autocommit = False
    return versions


# ── Migration 001: Add structured metadata columns to documents + quality_score to chunks ──

@_migration(1)
def migration_001(conn) -> None:
    """
    Add all structured extraction columns missing from the original create_schema().

    The retrieval layer (analytical_query, vector_search, etc.) already queries
    these columns — without them the live DB relied on manual ALTER TABLE statements
    that weren't tracked in code. This migration makes the schema authoritative.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        # Documents — structured metadata from filename parsing and LLM extraction
        alterations = [
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_path       TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_month       TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_unit        TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type        TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS fiscal_year     TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS serial_no       INTEGER",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS party_name      TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS party_name_canonical TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS party_gstin     TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_date        DATE",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_number      TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS total_amount    NUMERIC(15,2)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS tax_amount      NUMERIC(15,2)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS net_amount      NUMERIC(15,2)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS payment_terms   TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ref_doc_number  TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_quality_score NUMERIC(4,3)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_status      TEXT NOT NULL DEFAULT 'pending'",
            # Chunks — OCR quality per chunk (used by _quality_filter)
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS quality_score NUMERIC(4,3)",
            # Indexes for the new columns
            "CREATE INDEX IF NOT EXISTS idx_doc_month      ON documents(doc_month)",
            "CREATE INDEX IF NOT EXISTS idx_doc_type       ON documents(doc_type)",
            "CREATE INDEX IF NOT EXISTS idx_doc_fiscal     ON documents(fiscal_year)",
            "CREATE INDEX IF NOT EXISTS idx_doc_party      ON documents(party_name)",
            "CREATE INDEX IF NOT EXISTS idx_doc_unit       ON documents(doc_unit)",
        ]
        for sql in alterations:
            cur.execute(sql)

        cur.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 002: document_line_items table ──────────────────────────────────────────────

@_migration(2)
def migration_002(conn) -> None:
    """
    Create document_line_items for table row extraction (chemical traceability,
    per-item amounts). One row per line item extracted from DotsOCR HTML tables.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS document_line_items (
                id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                line_number     INTEGER NOT NULL,
                description     TEXT,
                hsn_code        TEXT,
                quantity        NUMERIC(15,4),
                unit_of_measure TEXT,
                unit_price      NUMERIC(15,2),
                amount          NUMERIC(15,2),
                tax_rate        NUMERIC(6,3),
                created_at      TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_li_document ON document_line_items(document_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_li_description ON document_line_items USING gin(to_tsvector('english', COALESCE(description,'')))"
        )

        cur.execute("INSERT INTO schema_migrations (version) VALUES (2)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 003: document_references table ─────────────────────────────────────────────

@_migration(3)
def migration_003(conn) -> None:
    """
    Cross-document references (e.g. a GRN referencing a PO by doc_number).
    ref_doc_id is populated in Phase 5 (resolution pass) once all docs are ingested.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS document_references (
                id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                source_doc_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ref_doc_number  TEXT NOT NULL,
                ref_doc_id      UUID REFERENCES documents(id) ON DELETE SET NULL,
                created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (source_doc_id, ref_doc_number)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_source  ON document_references(source_doc_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_target  ON document_references(ref_doc_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_number  ON document_references(ref_doc_number)"
        )

        cur.execute("INSERT INTO schema_migrations (version) VALUES (3)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 004: failed_extractions table ───────────────────────────────────────────────

@_migration(4)
def migration_004(conn) -> None:
    """
    Track documents where LLM entity extraction failed after retries.
    Exposed via /admin/failed-extractions so operators can queue manual review.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS failed_extractions (
                id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                step         TEXT NOT NULL,
                error        TEXT,
                attempted_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fe_doc ON failed_extractions(document_id)"
        )

        cur.execute("INSERT INTO schema_migrations (version) VALUES (4)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 005: page_embeddings table (ColPali) ───────────────────────────────────────

@_migration(5)
def migration_005(conn) -> None:
    """
    Per-page visual embeddings generated by ColPali (vidore/colpali-v1.2).
    Covers handwritten text, stamps, and signatures that DotsOCR may miss.
    128-dimensional vectors stored alongside the text embeddings.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS page_embeddings (
                id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_num    INTEGER NOT NULL,
                embedding   vector(128),
                created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (document_id, page_num)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_page_emb_doc ON page_embeddings(document_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_page_emb_vector
            ON page_embeddings USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)

        cur.execute("INSERT INTO schema_migrations (version) VALUES (5)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 007: colpali_page_embeddings table ─────────────────────────────

@_migration(7)
def migration_007(conn) -> None:
    """
    Create colpali_page_embeddings for per-page visual search.
    128-dim vectors from vidore/colpali-v1.2, RBAC via department_id.
    HNSW index for fast approximate nearest-neighbour search.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS colpali_page_embeddings (
                id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                page_num      INT  NOT NULL,
                embedding     vector(128),
                created_at    TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_colpali_doc_page
            ON colpali_page_embeddings(document_id, page_num)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_colpali_dept
            ON colpali_page_embeddings(department_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_colpali_vector
            ON colpali_page_embeddings USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)

        cur.execute("INSERT INTO schema_migrations (version) VALUES (7)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise


# ── Migration 006: UNIQUE constraint on documents(file_name, department_id) ──

@_migration(6)
def migration_006(conn) -> None:
    """
    Add UNIQUE(file_name, department_id) to documents.
    Required for the ON CONFLICT upsert in upsert_document() to be atomic.
    Deduplicates existing rows first, keeping the most recently updated record.
    """
    try:
        conn.autocommit = False
        cur = conn.cursor()
        # Remove duplicates: for each (file_name, department_id) group keep the
        # row with the highest id (most recently inserted), delete the rest.
        cur.execute("""
            DELETE FROM documents
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY file_name, department_id
                               ORDER BY id DESC
                           ) AS rn
                    FROM documents
                ) ranked
                WHERE rn > 1
            )
        """)
        deleted = cur.rowcount
        if deleted:
            logger.info("[Migration 006] Removed %d duplicate document rows before adding UNIQUE constraint", deleted)
        cur.execute(
            "ALTER TABLE documents ADD CONSTRAINT uq_doc_file_dept "
            "UNIQUE (file_name, department_id)"
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (6)")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
