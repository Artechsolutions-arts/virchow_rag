import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, Json
from src.config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD, EMBEDDING_DIM, EMBEDDING_MODEL, cfg
import logging

logger = logging.getLogger(__name__)

def get_pg_connection():
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DATABASE, user=PG_USER, password=PG_PASSWORD,
    )
    conn.autocommit = True
    return conn

def get_pg_pool(minconn=1, maxconn=10):
    return pool.ThreadedConnectionPool(
        minconn, maxconn,
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DATABASE, user=PG_USER, password=PG_PASSWORD,
    )

def create_schema(conn):
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # T1 departments
    cur.execute("""CREATE TABLE IF NOT EXISTS departments (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name TEXT NOT NULL UNIQUE, description TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(), created_by UUID);""")
    # T1 dept_access_grants
    cur.execute("""CREATE TABLE IF NOT EXISTS dept_access_grants (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        granting_dept_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        receiving_dept_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        granted_by UUID,
        access_type TEXT NOT NULL DEFAULT 'read' CHECK (access_type IN ('read','full')),
        expires_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (granting_dept_id, receiving_dept_id));""")
    # T2 users — username is the unique identity (case-insensitive). Email is
    # NOT unique: multiple users may share an email but each must have a
    # distinct username.
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        email TEXT NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(), last_login TIMESTAMP);""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS users_name_lower_key
                   ON users (lower(name));""")
    # deferred FKs
    for cname, table, col, ref in [
        ("fk_dept_created_by",  "departments",       "created_by", "users(id) ON DELETE SET NULL"),
        ("fk_grant_granted_by", "dept_access_grants","granted_by", "users(id) ON DELETE SET NULL"),
    ]:
        cur.execute(f"""DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                         WHERE constraint_name='{cname}') THEN
            ALTER TABLE {table} ADD CONSTRAINT {cname}
              FOREIGN KEY ({col}) REFERENCES {ref};
          END IF; END $$;""")
    # T3 chat
    cur.execute("""CREATE TABLE IF NOT EXISTS chat (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title TEXT, model_name TEXT NOT NULL DEFAULT 'qwen2.5:latest',
        temperature NUMERIC(3,2) NOT NULL DEFAULT 0.0,
        rag_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T3 messages
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chat_id UUID NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant')),
        content TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T3 user_uploads
    cur.execute("""CREATE TABLE IF NOT EXISTS user_uploads (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        chat_id UUID REFERENCES chat(id) ON DELETE SET NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        file_name TEXT NOT NULL, file_path TEXT NOT NULL, file_size_bytes BIGINT,
        mime_type TEXT NOT NULL DEFAULT 'application/pdf',
        upload_scope TEXT NOT NULL DEFAULT 'dept' CHECK (upload_scope IN ('chat','dept')),
        embed_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        processing_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (processing_status IN ('pending','processing','completed','failed')),
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T3 admin_uploads
    cur.execute("""CREATE TABLE IF NOT EXISTS admin_uploads (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        admin_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        file_name TEXT NOT NULL, file_path TEXT NOT NULL, file_size_bytes BIGINT,
        mime_type TEXT NOT NULL DEFAULT 'application/pdf',
        approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
        upload_status TEXT NOT NULL DEFAULT 'approved'
            CHECK (upload_status IN ('pending','approved','rejected')),
        processing_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (processing_status IN ('pending','processing','completed','failed')),
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T4 documents
    cur.execute("""CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        title TEXT, file_name TEXT NOT NULL, file_path TEXT NOT NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        uploaded_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        source_user_upload_id UUID REFERENCES user_uploads(id) ON DELETE SET NULL,
        source_admin_upload_id UUID REFERENCES admin_uploads(id) ON DELETE SET NULL,
        embed_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (embed_status IN ('pending','processing','completed','failed')),
        content_hash TEXT, page_count INTEGER NOT NULL DEFAULT 0,
        ocr_used BOOLEAN NOT NULL DEFAULT FALSE,
        version INTEGER NOT NULL DEFAULT 1,
        last_embedded_at TIMESTAMP);""")
    # T4 chunks
    cur.execute("""CREATE TABLE IF NOT EXISTS chunks (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        source_user_upload_id UUID REFERENCES user_uploads(id) ON DELETE SET NULL,
        source_admin_upload_id UUID REFERENCES admin_uploads(id) ON DELETE SET NULL,
        chunk_index INTEGER NOT NULL, chunk_text TEXT NOT NULL,
        chunk_token_count INTEGER, page_num INTEGER NOT NULL DEFAULT 0,
        doc_version INTEGER NOT NULL DEFAULT 1);""")
    # Migration
    for _sql in [
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_num INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_token_count INTEGER;",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS doc_version INTEGER NOT NULL DEFAULT 1;",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_user_upload_id UUID;",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_admin_upload_id UUID;",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS quality_score FLOAT DEFAULT 1.0;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_used BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;",
        "ALTER TABLE documents DROP COLUMN IF EXISTS is_shared_globally;",
        "ALTER TABLE admin_uploads DROP COLUMN IF EXISTS is_shared_globally;",
        # Ingestion spec — structured metadata columns on documents
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_month      TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_unit       TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type       TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS fiscal_year    TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS serial_no      TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS party_name     TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS party_gstin    TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_date       DATE;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_number     TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS total_amount   NUMERIC(15,2);",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS tax_amount     NUMERIC(15,2);",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS net_amount     NUMERIC(15,2);",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS payment_terms  TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ref_doc_number TEXT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_quality    FLOAT;",
        # File size + created_at — needed for the document list UI
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_size     BIGINT NOT NULL DEFAULT 0;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at    TIMESTAMP NOT NULL DEFAULT NOW();",
        # Granular pipeline stage tracking — feeds the upload UI progress display
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS current_stage        VARCHAR(20);",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_current_page     INT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_total_pages      INT;",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ;",
        # Sync ocr_status so COALESCE(ocr_status, embed_status) always reflects real state
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_status TEXT NOT NULL DEFAULT 'pending';",
    ]:
        try:
            cur.execute(_sql)
        except Exception as e:
            # Schema drift must not be silent. Many of these statements use
            # ``IF NOT EXISTS`` so they *should* succeed; surface any that
            # don't so a broken deploy doesn't look healthy.
            logger.warning("[Schema] migration failed: %s  (sql=%s)",
                           e, _sql.split("\n", 1)[0])

    # Dedup guard — prevents a race where two concurrent workers ingest the
    # same file before either of them has a chance to set the Redis dedup
    # key. Safe to add now: existing rows may legitimately duplicate from
    # early runs, so we create the index only if no dupes exist.
    try:
        cur.execute("""
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname='uq_doc_hash_dept'
              ) AND NOT EXISTS (
                SELECT 1 FROM documents
                WHERE content_hash IS NOT NULL
                GROUP BY content_hash, department_id
                HAVING COUNT(*) > 1
                LIMIT 1
              ) THEN
                CREATE UNIQUE INDEX uq_doc_hash_dept
                  ON documents(content_hash, department_id)
                  WHERE content_hash IS NOT NULL;
              END IF;
            END $$;
        """)
    except Exception as e:
        logger.warning("[Schema] uq_doc_hash_dept guard skipped: %s", e)

    # T4 document_line_items  (AI-2)
    cur.execute("""CREATE TABLE IF NOT EXISTS document_line_items (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        department_id   UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        line_no         INTEGER,
        description     TEXT,
        hsn_sac         TEXT,
        quantity        NUMERIC(15,3),
        unit_of_measure TEXT,
        unit_rate       NUMERIC(15,2),
        amount          NUMERIC(15,2),
        tax_rate        NUMERIC(5,2),
        tax_amount      NUMERIC(15,2),
        created_at      TIMESTAMP NOT NULL DEFAULT NOW());""")

    # T4 document_references  (AI-3)
    cur.execute("""CREATE TABLE IF NOT EXISTS document_references (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        source_doc_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ref_doc_number  TEXT NOT NULL,
        ref_doc_id      UUID REFERENCES documents(id) ON DELETE SET NULL,
        ref_type        TEXT NOT NULL,
        created_at      TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T5 embeddings
    cur.execute(f"""CREATE TABLE IF NOT EXISTS embeddings (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        source_user_upload_id UUID REFERENCES user_uploads(id) ON DELETE SET NULL,
        source_admin_upload_id UUID REFERENCES admin_uploads(id) ON DELETE SET NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        embedding vector({EMBEDDING_DIM}),
        embedding_model TEXT NOT NULL DEFAULT '{EMBEDDING_MODEL}',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T5 rag_retrieval_log
    cur.execute("""CREATE TABLE IF NOT EXISTS rag_retrieval_log (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chat_id UUID NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        query_text TEXT NOT NULL,
        retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]',
        similarity_scores JSONB NOT NULL DEFAULT '[]',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    # T6 admin_actions
    cur.execute("""CREATE TABLE IF NOT EXISTS admin_actions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        admin_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
        action_type TEXT NOT NULL, target_type TEXT, target_id UUID,
        role_at_action TEXT, ip_address INET, metadata JSONB DEFAULT '{}',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    # Indexes
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_dag_receiving  ON dept_access_grants(receiving_dept_id);",
        "CREATE INDEX IF NOT EXISTS idx_dag_granting   ON dept_access_grants(granting_dept_id);",
        "CREATE INDEX IF NOT EXISTS idx_users_dept     ON users(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_chat_dept      ON chat(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_msg_chat       ON messages(chat_id);",
        "CREATE INDEX IF NOT EXISTS idx_uu_dept        ON user_uploads(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_au_dept        ON admin_uploads(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_doc_dept       ON documents(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_doc_hash       ON documents(content_hash);",
        "CREATE INDEX IF NOT EXISTS idx_chunk_doc      ON chunks(document_id);",
        "CREATE INDEX IF NOT EXISTS idx_chunk_quality  ON chunks(quality_score);",
        "CREATE INDEX IF NOT EXISTS idx_emb_dept       ON embeddings(department_id);",
        # Ingestion spec — structured metadata indexes
        "CREATE INDEX IF NOT EXISTS idx_doc_party_name  ON documents USING gin(to_tsvector('english', coalesce(party_name, '')));",
        "CREATE INDEX IF NOT EXISTS idx_doc_fiscal_year ON documents(fiscal_year);",
        "CREATE INDEX IF NOT EXISTS idx_doc_type        ON documents(doc_type);",
        "CREATE INDEX IF NOT EXISTS idx_doc_date        ON documents(doc_date);",
        "CREATE INDEX IF NOT EXISTS idx_doc_dept_type   ON documents(department_id, doc_type);",
        "CREATE INDEX IF NOT EXISTS idx_line_items_doc    ON document_line_items(document_id);",
        "CREATE INDEX IF NOT EXISTS idx_line_items_dept   ON document_line_items(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_line_items_hsn    ON document_line_items(hsn_sac);",
        "CREATE INDEX IF NOT EXISTS idx_line_items_search ON document_line_items USING gin(to_tsvector('english', coalesce(description, '')));",
        "CREATE INDEX IF NOT EXISTS idx_doc_refs_source ON document_references(source_doc_id);",
        "CREATE INDEX IF NOT EXISTS idx_doc_refs_refnum ON document_references(ref_doc_number);",
        # HNSW index omitted: pgvector HNSW limit is 2000 dims; qwen3-embedding:8b
        # uses 4096 dims. Exact cosine search (<=> operator) is used instead.
        # Add an IVFFlat or halfvec index here once a suitable strategy is chosen.
        "CREATE INDEX IF NOT EXISTS idx_rrl_chat       ON rag_retrieval_log(chat_id);",
        "CREATE INDEX IF NOT EXISTS idx_aa_dept        ON admin_actions(department_id);",
    ]:
        cur.execute(sql)
    # HNSW index skipped: pgvector HNSW limit is 2000 dims.
    # qwen3-embedding:8b outputs 4096 dims — exact cosine search is used instead.
    cur.execute("DROP INDEX IF EXISTS idx_emb_vector;")

    # ColPali page-level visual embeddings (128-dim)
    cur.execute("""CREATE TABLE IF NOT EXISTS colpali_page_embeddings (
        id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        page_num      INT  NOT NULL,
        embedding     vector(128),
        created_at    TIMESTAMP NOT NULL DEFAULT NOW());""")

    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_colpali_doc_page ON colpali_page_embeddings(document_id, page_num);",
        "CREATE INDEX IF NOT EXISTS idx_colpali_dept     ON colpali_page_embeddings(department_id);",
        """CREATE INDEX IF NOT EXISTS idx_colpali_vector
           ON colpali_page_embeddings USING hnsw (embedding vector_cosine_ops)
           WITH (m = 16, ef_construction = 64);""",
    ]:
        cur.execute(sql)

    conn.commit()
    cur.close()
    logger.info("[Schema] Tables + indexes ready ✓")

class RBACManager:
    def __init__(self, conn_or_pool):
        if isinstance(conn_or_pool, pool.AbstractConnectionPool):
            self.pool = conn_or_pool
            self.conn = None
        else:
            self.pool = None
            self.conn = conn_or_pool

    def _get_conn(self):
        if self.pool:
            conn = self.pool.getconn()
            conn.autocommit = True
            return conn
        return self.conn

    def _put_conn(self, conn):
        if self.pool and conn:
            self.pool.putconn(conn)

    def _cur(self, conn=None):
        c = conn or self._get_conn()
        return c.cursor(cursor_factory=RealDictCursor)

    def _audit(self, admin_user_id, department_id, action_type,
               target_type=None, target_id=None, metadata=None, conn=None):
        _conn = conn or self._get_conn()
        try:
            cur = self._cur(_conn)
            cur.execute("""INSERT INTO admin_actions
                           (admin_user_id,department_id,action_type,target_type,target_id,metadata)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (admin_user_id, department_id, action_type,
                         target_type, target_id, Json(metadata or {})))
            cur.close()
        finally:
            if not conn:
                self._put_conn(_conn)

    def create_department(self, name, description=None, created_by=None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("INSERT INTO departments (name,description,created_by) VALUES (%s,%s,%s) RETURNING id",
                        (name, description, created_by))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def grant_dept_access(self, granting_dept_id, receiving_dept_id, granted_by,
                          access_type="read", expires_at=None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO dept_access_grants
                           (granting_dept_id,receiving_dept_id,granted_by,access_type,expires_at)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (granting_dept_id,receiving_dept_id)
                           DO UPDATE SET access_type=EXCLUDED.access_type,granted_by=EXCLUDED.granted_by
                           RETURNING id""",
                        (granting_dept_id, receiving_dept_id, granted_by, access_type, expires_at))
            r = str(cur.fetchone()["id"]); cur.close()
            self._audit(granted_by, granting_dept_id, "dept_access_grant",
                        "department", receiving_dept_id, {"access_type": access_type}, conn=conn)
            return r
        finally:
            self._put_conn(conn)

    def create_user(self, email, name, password_hash, department_id, is_super_admin=False):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO users (email,name,password_hash,department_id,is_super_admin)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        (email, name, password_hash, department_id, is_super_admin))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def create_chat(self, user_id, department_id, title=None, rag_enabled=True):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("INSERT INTO chat (user_id,department_id,title,rag_enabled) VALUES (%s,%s,%s,%s) RETURNING id",
                        (user_id, department_id, title, rag_enabled))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def add_message(self, chat_id, role, content):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("INSERT INTO messages (chat_id,role,content) VALUES (%s,%s,%s) RETURNING id",
                        (chat_id, role, content))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def register_user_upload(self, user_id, dept_id, file_name, file_path,
                             chat_id=None, file_size_bytes=None, upload_scope="dept"):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO user_uploads
                           (user_id,chat_id,department_id,file_name,file_path,
                            file_size_bytes,mime_type,upload_scope)
                           VALUES (%s,%s,%s,%s,%s,%s,'application/pdf',%s) RETURNING id""",
                        (user_id, chat_id, dept_id, file_name, file_path,
                         file_size_bytes, upload_scope))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def get_user_is_admin(self, user_id: str) -> bool:
        """Return True if the user has role='admin' or is_super_admin=True."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT role, is_super_admin FROM users WHERE id=%s::uuid",
                (user_id,)
            )
            row = cur.fetchone()
            cur.close()
            if not row:
                return False
            return row["role"] == "admin" or bool(row["is_super_admin"])
        except Exception:
            return False
        finally:
            self._put_conn(conn)

    def register_admin_upload(self, admin_user_id, dept_id, file_name, file_path,
                              file_size_bytes=None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO admin_uploads
                           (admin_user_id,department_id,file_name,file_path,
                            file_size_bytes,mime_type)
                           VALUES (%s,%s,%s,%s,%s,'application/pdf') RETURNING id""",
                        (admin_user_id, dept_id, file_name, file_path,
                         file_size_bytes))
            r = str(cur.fetchone()["id"]); cur.close()
            self._audit(admin_user_id, dept_id, "admin_upload", "file",
                        metadata={"file_name": file_name}, conn=conn)
            return r
        finally:
            self._put_conn(conn)

    # Whitelist of upload tables — anything outside this set is rejected
    # regardless of how ``upload_type`` got routed in. Prevents SQL
    # injection via a poisoned ``upload_type`` field.
    _UPLOAD_TABLES = {"user": "user_uploads", "admin": "admin_uploads"}

    def update_upload_status(self, upload_id, upload_type, status):
        table = self._UPLOAD_TABLES.get(upload_type)
        if table is None:
            raise ValueError(f"unknown upload_type {upload_type!r}")
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(f"UPDATE {table} SET processing_status=%s WHERE id=%s",
                        (status, upload_id))
            cur.close()
        finally:
            self._put_conn(conn)

    def create_document_pending(self, file_name, file_path, dept_id, uploaded_by,
                               source_user_upload_id=None,
                               source_admin_upload_id=None,
                               file_size: int = 0) -> str:
        """Insert a minimal placeholder row with embed_status='pending' so the
        file appears in the UI immediately after upload."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO documents
                           (title,file_name,file_path,department_id,uploaded_by,
                            source_user_upload_id,source_admin_upload_id,
                            file_size,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING id""",
                        (file_name, file_name, file_path, dept_id, uploaded_by,
                         source_user_upload_id, source_admin_upload_id, file_size))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def find_document_by_upload_id(self, upload_id: str, upload_type: str = "user"):
        """Return the document row created for a given upload, or None.
        Checks source_admin_upload_id for admin uploads, source_user_upload_id otherwise."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            if upload_type == "admin":
                cur.execute(
                    "SELECT id::TEXT FROM documents WHERE source_admin_upload_id = %s::uuid",
                    (upload_id,)
                )
            else:
                cur.execute(
                    "SELECT id::TEXT FROM documents WHERE source_user_upload_id = %s::uuid",
                    (upload_id,)
                )
            row = cur.fetchone(); cur.close()
            return str(row["id"]) if row else None
        finally:
            self._put_conn(conn)

    def update_document_from_pipeline(self, doc_id: str, file_path: str,
                                      content_hash: str, page_count: int,
                                      ocr_used: bool, fname_meta: dict,
                                      file_size: int):
        """Update a pending placeholder document with full pipeline results."""
        fm = fname_meta or {}
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""UPDATE documents
                           SET file_path=%s, content_hash=%s, page_count=%s,
                               ocr_used=%s, embed_status='processing', ocr_status='processing',
                               doc_month=%s, doc_unit=%s, doc_type=%s,
                               fiscal_year=%s, serial_no=%s, file_size=%s
                           WHERE id=%s::uuid""",
                        (file_path, content_hash, page_count, ocr_used,
                         fm.get("doc_month"), fm.get("doc_unit"),
                         fm.get("doc_type"), fm.get("fiscal_year"),
                         fm.get("serial_no"), file_size, doc_id))
            cur.close()
        finally:
            self._put_conn(conn)

    def mark_document_failed(self, doc_id: str):
        """Set embed_status='failed' on a document that could not be processed."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("UPDATE documents SET embed_status='failed', ocr_status='failed' WHERE id=%s::uuid",
                        (doc_id,))
            cur.close()
        finally:
            self._put_conn(conn)

    def create_document(self, file_name, file_path, dept_id, uploaded_by,
                        content_hash=None, page_count=0, ocr_used=False,
                        source_user_upload_id=None, source_admin_upload_id=None,
                        fname_meta=None, file_size: int = 0):
        """Insert a document row, or update the existing row if (file_name, department_id)
        already exists (e.g. a pending placeholder created at upload time).
        ``fname_meta`` is the dict returned by ``parse_filename_metadata``."""
        fm = fname_meta or {}
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO documents
                           (title,file_name,file_path,department_id,uploaded_by,
                            content_hash,page_count,ocr_used,
                            source_user_upload_id,source_admin_upload_id,
                            doc_month,doc_unit,doc_type,fiscal_year,serial_no,
                            file_size,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (file_name, department_id) DO UPDATE
                               SET content_hash  = EXCLUDED.content_hash,
                                   page_count    = EXCLUDED.page_count,
                                   ocr_used      = EXCLUDED.ocr_used,
                                   file_path     = EXCLUDED.file_path,
                                   file_size     = EXCLUDED.file_size,
                                   doc_month     = COALESCE(EXCLUDED.doc_month,     documents.doc_month),
                                   doc_unit      = COALESCE(EXCLUDED.doc_unit,      documents.doc_unit),
                                   doc_type      = COALESCE(EXCLUDED.doc_type,      documents.doc_type),
                                   fiscal_year   = COALESCE(EXCLUDED.fiscal_year,   documents.fiscal_year),
                                   serial_no     = COALESCE(EXCLUDED.serial_no,     documents.serial_no)
                           RETURNING id""",
                        (file_name, file_name, file_path, dept_id, uploaded_by,
                         content_hash, page_count, ocr_used,
                         source_user_upload_id, source_admin_upload_id,
                         fm.get("doc_month"), fm.get("doc_unit"),
                         fm.get("doc_type"), fm.get("fiscal_year"),
                         fm.get("serial_no"), file_size))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def update_document_extraction(self, doc_id, fields: dict):
        """Persist entity-extraction results (AI-5), OCR quality, and extracted text.
        ``fields`` may contain: party_name, party_gstin, doc_date, doc_month,
        doc_number, doc_type, doc_unit, total_amount, tax_amount, net_amount,
        payment_terms, ref_doc_number, ocr_quality, ocr_quality_score,
        extracted_text. Unknown keys are ignored."""
        allowed = {"party_name", "party_gstin", "doc_date", "doc_month",
                   "doc_number", "doc_type", "doc_unit",
                   "total_amount", "tax_amount", "net_amount", "payment_terms",
                   "ref_doc_number", "ocr_quality", "ocr_quality_score", "extracted_text"}
        updates = {k: v for k, v in (fields or {}).items() if k in allowed}
        if not updates:
            return
        cols = ", ".join(f"{k}=%s" for k in updates)
        params = list(updates.values()) + [doc_id]
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(f"UPDATE documents SET {cols} WHERE id=%s", params)
            cur.close()
        finally:
            self._put_conn(conn)

    def add_line_items(self, doc_id, dept_id, rows: list):
        """Bulk insert document_line_items. ``rows`` is a list of dicts with
        keys among: line_no, description, hsn_sac, quantity, unit_of_measure,
        unit_rate, amount, tax_rate, tax_amount. Missing keys default to NULL.

        Uses ``executemany`` — one round-trip regardless of row count."""
        if not rows:
            return
        params = [
            (doc_id, dept_id, r.get("line_no"), r.get("description"),
             r.get("hsn_sac"), r.get("quantity"), r.get("unit_of_measure"),
             r.get("unit_rate"), r.get("amount"), r.get("tax_rate"),
             r.get("tax_amount"))
            for r in rows
        ]
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.executemany(
                """INSERT INTO document_line_items
                   (document_id,department_id,line_no,description,hsn_sac,
                    quantity,unit_of_measure,unit_rate,amount,
                    tax_rate,tax_amount)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                params,
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def add_document_reference(self, source_doc_id, ref_doc_number, ref_type):
        """Record an unresolved cross-document reference. ``ref_doc_id`` is
        populated later by a batch resolution pass (AI-3)."""
        if not ref_doc_number:
            return
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO document_references
                           (source_doc_id,ref_doc_number,ref_type)
                           VALUES (%s,%s,%s)""",
                        (source_doc_id, ref_doc_number, ref_type))
            cur.close()
        finally:
            self._put_conn(conn)

    def get_document_status(self, doc_id: str) -> str:
        """Return embed_status for a document, or None if not found."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT embed_status FROM documents WHERE id=%s::uuid", (doc_id,))
            row = cur.fetchone(); cur.close()
            return row["embed_status"] if row else None
        finally:
            self._put_conn(conn)

    def claim_document_for_processing(self, doc_id: str) -> bool:
        """Atomically transition a document from 'pending' to 'processing'.
        Returns True if this worker successfully claimed it, False if another
        worker already claimed or completed it (duplicate queue message guard)."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """UPDATE documents
                      SET embed_status='processing',
                          processing_started_at=NOW()
                    WHERE id=%s::uuid AND embed_status='pending'
                RETURNING id""",
                (doc_id,)
            )
            claimed = cur.fetchone() is not None
            cur.close()
            return claimed
        finally:
            self._put_conn(conn)

    def update_document_status(self, doc_id, status):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""UPDATE documents SET embed_status=%s, ocr_status=%s, current_stage=%s,
                           last_embedded_at=CASE WHEN %s='completed' THEN NOW()
                           ELSE last_embedded_at END
                           WHERE id=%s AND embed_status != 'completed'""",
                        (status, status, status, status, doc_id))
            cur.close()
        finally:
            self._put_conn(conn)

    # Drug vocabulary for product_names extraction
    _DRUG_RE = None

    @classmethod
    def _get_drug_re(cls):
        import re
        if cls._DRUG_RE is None:
            drugs = [
                "tramadol", "levetiracetam", "levetracetam", "gabapentin",
                "pregabalin", "paracetamol", "acetaminophen", "ibuprofen",
                "diclofenac", "amoxicillin", "amoxycillin", "azithromycin",
                "ciprofloxacin", "metformin", "atorvastatin", "amlodipine",
                "omeprazole", "pantoprazole", "ranitidine", "metronidazole",
                "ceftriaxone", "cefixime", "doxycycline", "clindamycin",
                "iron", "calcium", "zinc", "vitamin", "folic acid",
                "insulin", "dexamethasone", "prednisolone", "hydrocortisone",
                "ondansetron", "domperidone", "metoclopramide",
                "salbutamol", "montelukast", "cetirizine", "loratadine",
                "losartan", "enalapril", "lisinopril", "telmisartan",
                "aspirin", "clopidogrel", "warfarin", "heparin",
                "morphine", "oxycodone", "codeine", "fentanyl",
            ]
            cls._DRUG_RE = re.compile(
                r'\b(' + '|'.join(re.escape(d) for d in drugs) + r')\b',
                re.IGNORECASE,
            )
        return cls._DRUG_RE

    def populate_product_names(self, doc_id: str):
        """Extract drug names from a document's chunks and store in product_names[]."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT chunk_text FROM chunks WHERE document_id=%s::uuid",
                (doc_id,)
            )
            combined = " ".join(r["chunk_text"] for r in cur.fetchall() if r["chunk_text"])
            drug_re = self._get_drug_re()
            found = {m.lower() for m in drug_re.findall(combined)}
            # Normalise levetracetam → levetiracetam so both spellings map to one canonical
            if "levetracetam" in found:
                found.discard("levetracetam")
                found.add("levetiracetam")
            if found:
                cur.execute(
                    "UPDATE documents SET product_names=%s WHERE id=%s::uuid",
                    (list(found), doc_id)
                )
            cur.close()
        except Exception:
            pass  # best-effort — never block completion
        finally:
            self._put_conn(conn)

    def update_document_stage(self, doc_id: str, stage: str,
                              ocr_current_page: int = None,
                              ocr_total_pages: int = None,
                              processing_started_at: float = None):
        """Update pipeline stage tracking columns. Best-effort — never raises."""
        if not doc_id:
            return
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            sets = ["current_stage=%s"]
            params: list = [stage]
            if ocr_current_page is not None:
                sets.append("ocr_current_page=%s")
                params.append(ocr_current_page)
            if ocr_total_pages is not None:
                sets.append("ocr_total_pages=%s")
                params.append(ocr_total_pages)
            if processing_started_at is not None:
                sets.append("processing_started_at=to_timestamp(%s)")
                params.append(float(processing_started_at))
            params.append(doc_id)
            cur.execute(
                f"UPDATE documents SET {', '.join(sets)} WHERE id=%s::uuid",
                params,
            )
            cur.close()
        except Exception:
            pass
        finally:
            self._put_conn(conn)

    def add_chunk(self, doc_id, chunk_index, chunk_text, chunk_token_count,
                  page_num=0, source_user_upload_id=None, source_admin_upload_id=None,
                  quality_score=None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO chunks
                           (document_id,chunk_index,chunk_text,chunk_token_count,page_num,
                            source_user_upload_id,source_admin_upload_id,quality_score)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (doc_id, chunk_index, chunk_text, chunk_token_count, page_num,
                         source_user_upload_id, source_admin_upload_id, quality_score))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    @staticmethod
    def _vec_str(embedding) -> str:
        """Serialize a float list to pgvector literal without scientific notation."""
        return '[' + ','.join(f'{v:.8f}' for v in embedding) + ']'

    def store_embedding(self, chunk_id, dept_id, embedding,
                        source_user_upload_id=None, source_admin_upload_id=None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            vec = self._vec_str(embedding)
            cur.execute("""INSERT INTO embeddings
                           (chunk_id,department_id,embedding,embedding_model,
                            source_user_upload_id,source_admin_upload_id)
                           VALUES (%s,%s,%s::vector,%s,%s,%s) RETURNING id""",
                        (chunk_id, dept_id, vec, cfg.embedding_model,
                         source_user_upload_id, source_admin_upload_id))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def store_colpali_embedding(self, doc_id: str, dept_id: str,
                                page_num: int, embedding: list):
        """Store a 128-dim ColPali page embedding in colpali_page_embeddings."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            vec = self._vec_str(embedding)
            cur.execute("""INSERT INTO colpali_page_embeddings
                           (document_id, department_id, page_num, embedding)
                           VALUES (%s, %s, %s, %s::vector) RETURNING id""",
                        (doc_id, dept_id, page_num, vec))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def find_doc_by_hash(self, content_hash, dept_id):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""SELECT id FROM documents
                           WHERE content_hash=%s AND department_id=%s
                             AND embed_status='completed' LIMIT 1""",
                        (content_hash, dept_id))
            row = cur.fetchone(); cur.close()
            return str(row["id"]) if row else None
        finally:
            self._put_conn(conn)

    def vector_search(self, query_embedding, dept_id, top_k=20,
                      min_quality: float = 0.3):
        """Cosine-similarity search gated by OCR quality.

        ``min_quality`` defaults to 0.3 — the "exclude" threshold from
        INGESTION_PIPELINE_SPEC.md AI-8. Retrieval callers that also
        want to *deprioritize* 0.3–0.6 chunks should apply that rescale
        on their side (e.g. multiply similarity by quality_score)."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            vec = self._vec_str(query_embedding)
            cur.execute("""
                SELECT e.chunk_id, c.chunk_text, c.document_id, c.page_num,
                       e.department_id, d.file_name, c.quality_score,
                       1-(e.embedding <=> %s::vector) AS similarity
                FROM   embeddings e
                JOIN   chunks c ON c.id=e.chunk_id
                JOIN   documents d ON d.id=c.document_id
                WHERE  (e.department_id=%s
                    OR  e.department_id IN (
                          SELECT granting_dept_id FROM dept_access_grants
                          WHERE  receiving_dept_id=%s
                            AND  (expires_at IS NULL OR expires_at>NOW())))
                  AND  COALESCE(c.quality_score, 1.0) >= %s
                ORDER  BY e.embedding <=> %s::vector LIMIT %s""",
                (vec, dept_id, dept_id, min_quality, vec, top_k))
            r = [dict(row) for row in cur.fetchall()]; cur.close(); return r
        finally:
            self._put_conn(conn)

    def log_retrieval(self, chat_id, user_id, dept_id, query_text, chunk_ids, scores):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""INSERT INTO rag_retrieval_log
                           (chat_id,user_id,department_id,query_text,
                            retrieved_chunk_ids,similarity_scores)
                           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (chat_id, user_id, dept_id, query_text,
                         Json(chunk_ids), Json(scores)))
            r = str(cur.fetchone()["id"]); cur.close(); return r
        finally:
            self._put_conn(conn)

    def can_dept_see(self, viewing_dept_id, owning_dept_id):
        if viewing_dept_id == owning_dept_id: return True
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""SELECT 1 FROM dept_access_grants
                           WHERE granting_dept_id=%s AND receiving_dept_id=%s
                             AND (expires_at IS NULL OR expires_at>NOW()) LIMIT 1""",
                        (owning_dept_id, viewing_dept_id))
            r = cur.fetchone() is not None; cur.close(); return r
        finally:
            self._put_conn(conn)

    def get_visible_depts(self, dept_id, limit: int = 1000):
        """Return the set of dept_ids this dept can see (self + grantors).
        Bounded by ``limit`` to guard against accidental fan-out."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""SELECT granting_dept_id::TEXT AS id FROM dept_access_grants
                           WHERE receiving_dept_id=%s AND (expires_at IS NULL OR expires_at>NOW())
                           UNION SELECT %s AS id LIMIT %s""",
                        (dept_id, dept_id, limit))
            r = [row["id"] for row in cur.fetchall()]; cur.close(); return r
        finally:
            self._put_conn(conn)

    def list_documents_for_dept(self, dept_id=None, limit: int = 10000):
        """Return documents for a department ordered by most recent first.
        When ``dept_id`` is None (super-admin), returns all documents."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            _cols = """
                    SELECT d.id::TEXT, d.file_name, d.file_path, d.embed_status,
                           COALESCE(d.file_size, 0) AS file_size,
                           COALESCE(d.last_embedded_at, d.created_at)::TEXT AS uploaded_at,
                           d.party_name, d.party_gstin,
                           d.doc_type, d.doc_month, d.doc_unit, d.doc_date,
                           d.total_amount, d.tax_amount, d.net_amount,
                           u.email AS uploaded_by_email
                    FROM   documents d
                    LEFT JOIN users u ON u.id = d.uploaded_by"""
            if dept_id:
                cur.execute(_cols + """
                    WHERE  d.department_id = %s
                    ORDER  BY d.created_at DESC NULLS LAST
                    LIMIT  %s
                """, (dept_id, limit))
            else:
                cur.execute(_cols + """
                    ORDER  BY d.created_at DESC NULLS LAST
                    LIMIT  %s
                """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_document_by_id(self, doc_id: str, dept_id=None):
        """Fetch a single document row. Optionally scope to a department."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            if dept_id:
                cur.execute("""SELECT d.*, u.email AS uploaded_by_email
                               FROM documents d LEFT JOIN users u ON u.id=d.uploaded_by
                               WHERE d.id=%s::uuid AND d.department_id=%s""",
                            (doc_id, dept_id))
            else:
                cur.execute("""SELECT d.*, u.email AS uploaded_by_email
                               FROM documents d LEFT JOIN users u ON u.id=d.uploaded_by
                               WHERE d.id=%s::uuid""",
                            (doc_id,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def reset_document_status(self, doc_id: str):
        """Delete all derived data for a document so it can be re-ingested cleanly.

        The document row itself is deleted so the orchestrator can insert a
        fresh row without hitting the unique (content_hash, department_id)
        constraint.  The original file stays on disk — the restart endpoint
        already verified its path before calling here.
        """
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""DELETE FROM embeddings
                           WHERE chunk_id IN (
                               SELECT id FROM chunks WHERE document_id=%s::uuid
                           )""", (doc_id,))
            cur.execute("DELETE FROM chunks WHERE document_id=%s::uuid", (doc_id,))
            cur.execute("DELETE FROM documents WHERE id=%s::uuid", (doc_id,))
            cur.close()
        finally:
            self._put_conn(conn)

    def find_stuck_documents(self, stale_minutes: int = 15) -> list:
        """Return documents stuck in non-terminal states older than stale_minutes.
        Used at worker startup to auto-recover from crash mid-pipeline."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("""
                SELECT d.id::TEXT      AS doc_id,
                       d.file_name,
                       d.file_path,
                       d.department_id::TEXT AS dept_id,
                       d.uploaded_by::TEXT   AS user_id,
                       d.source_user_upload_id::TEXT  AS user_upload_id,
                       d.source_admin_upload_id::TEXT AS admin_upload_id,
                       d.embed_status
                FROM   documents d
                WHERE  d.embed_status IN ('pending', 'processing')
                  AND  d.created_at < NOW() - INTERVAL '%s minutes'
                ORDER  BY COALESCE(d.file_size, 0) ASC, d.created_at ASC
            """, (stale_minutes,))
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def reset_document_for_retry(self, doc_id: str):
        """Clear partial pipeline output for a document so it can be re-processed.
        Keeps the document row itself (preserving the FK for upload tracking).
        Resets embed_status to 'pending' and clears all stage progress columns."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            # Remove partial chunks + embeddings (embeddings cascade-deleted with chunks)
            cur.execute("DELETE FROM chunks WHERE document_id=%s::uuid", (doc_id,))
            cur.execute("DELETE FROM colpali_page_embeddings WHERE document_id=%s::uuid", (doc_id,))
            cur.execute("""UPDATE documents
                           SET embed_status='pending', ocr_status='pending',
                               current_stage=NULL, ocr_current_page=NULL,
                               ocr_total_pages=NULL, content_hash=NULL,
                               page_count=0
                           WHERE id=%s::uuid""", (doc_id,))
            cur.close()
        finally:
            self._put_conn(conn)

    def get_audit_log(self, dept_id=None, limit=50):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            conds, params = ["1=1"], []
            if dept_id: conds.append("department_id=%s"); params.append(dept_id)
            params.append(limit)
            cur.execute(f"SELECT * FROM admin_actions WHERE {' AND '.join(conds)} "
                        f"ORDER BY created_at DESC LIMIT %s", params)
            rows = []
            for row in cur.fetchall():
                item = dict(row)
                for k, v in item.items():
                    if not isinstance(v, (str, int, float, bool, type(None), dict, list)):
                        item[k] = str(v)
                rows.append(item)
            cur.close(); return rows
        finally:
            self._put_conn(conn)
