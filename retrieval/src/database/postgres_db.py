import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, Json, execute_values
from src.config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD, EMBEDDING_DIM, EMBEDDING_MODEL, cfg
import logging

logger = logging.getLogger(__name__)

# Common spelling variants for drug names found in Indian medical invoices
_DRUG_SPELLING_MAP: dict[str, list[str]] = {
    "levetracetam":   ["levetracetam", "levetiracetam"],
    "levetiracetam":  ["levetiracetam", "levetracetam"],
    "tramadol":       ["tramadol", "tramadol hcl"],
    "paracetamol":    ["paracetamol", "acetaminophen"],
    "acetaminophen":  ["acetaminophen", "paracetamol"],
    "ibuprofen":      ["ibuprofen"],
    "amoxicillin":    ["amoxicillin", "amoxycillin"],
    "amoxycillin":    ["amoxycillin", "amoxicillin"],
}


def _drug_variants(keyword: str) -> list[str]:
    """Return keyword + known spelling variants (lowercase)."""
    key = keyword.lower()
    return _DRUG_SPELLING_MAP.get(key, [key])


def get_pg_pool(minconn=2, maxconn=20):
    return pool.ThreadedConnectionPool(
        minconn, maxconn,
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DATABASE, user=PG_USER, password=PG_PASSWORD,
    )


def create_schema(conn):
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS public;")
    cur.execute("SET search_path TO public;")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;")
    cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;')

    cur.execute("""CREATE TABLE IF NOT EXISTS departments (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name TEXT NOT NULL UNIQUE, description TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    cur.execute("""CREATE TABLE IF NOT EXISTS dept_access_grants (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        granting_dept_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        receiving_dept_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        access_type TEXT NOT NULL DEFAULT 'read',
        expires_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (granting_dept_id, receiving_dept_id));""")

    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        email TEXT NOT NULL,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','hod','user')),
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        last_login TIMESTAMP);""")
    # username is the unique identity (case-insensitive). Email is NOT unique —
    # multiple users may share an email but each must have a distinct username.
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS users_name_lower_key
                   ON users (lower(name));""")

    # Small runtime-tunable settings (e.g. selected LLM model). Avoids needing
    # a redeploy + env-var change for things admins should tweak from the UI.
    cur.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_by UUID REFERENCES users(id) ON DELETE SET NULL);""")

    cur.execute("""CREATE TABLE IF NOT EXISTS chat (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title TEXT,
        rag_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chat_id UUID NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant')),
        content TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    cur.execute("""CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        title TEXT, file_name TEXT NOT NULL,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
        content_hash TEXT, page_count INTEGER NOT NULL DEFAULT 0,
        embed_status TEXT NOT NULL DEFAULT 'completed',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    cur.execute("""CREATE TABLE IF NOT EXISTS chunks (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        chunk_token_count INTEGER,
        page_num INTEGER NOT NULL DEFAULT 0);""")

    cur.execute(f"""CREATE TABLE IF NOT EXISTS embeddings (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        embedding vector({EMBEDDING_DIM}),
        embedding_model TEXT NOT NULL DEFAULT '{EMBEDDING_MODEL}',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    cur.execute("""CREATE TABLE IF NOT EXISTS rag_retrieval_log (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        chat_id UUID NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        query_text TEXT NOT NULL,
        retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]',
        similarity_scores JSONB NOT NULL DEFAULT '[]',
        created_at TIMESTAMP NOT NULL DEFAULT NOW());""")

    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_users_dept     ON users(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_chat_user      ON chat(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_chat_dept      ON chat(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_msg_chat       ON messages(chat_id);",
        "CREATE INDEX IF NOT EXISTS idx_doc_dept       ON documents(department_id);",
        "CREATE INDEX IF NOT EXISTS idx_chunk_doc      ON chunks(document_id);",
        "CREATE INDEX IF NOT EXISTS idx_emb_dept       ON embeddings(department_id);",
        # HNSW skipped: qwen3-embedding:8b is 4096 dims, exceeds pgvector HNSW limit of 2000.
        # Exact cosine search (<=> operator) is used — fine for current dataset scale.
    ]:
        cur.execute(sql)

    conn.commit()
    cur.close()
    logger.info("[Schema] Tables + indexes ready")


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

    def _cur(self, conn):
        return conn.cursor(cursor_factory=RealDictCursor)

    # ── User auth ─────────────────────────────────────────────────────────────

    def get_user_by_email(self, email: str):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id, email, name, password_hash, department_id, is_active, is_super_admin, role "
                "FROM users WHERE email=%s", (email,)
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def get_user_by_email_or_name(self, identifier: str):
        """Login lookup. Username is the unique identity now, so a name match
        always wins. Email is matched as a fallback for legacy convenience —
        but if multiple users share the same email, the first one (by
        creation date) is returned, which is non-deterministic across writes.
        Match is case-insensitive against both columns."""
        if not identifier:
            return None
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id, email, name, password_hash, department_id, "
                "       is_active, is_super_admin, role "
                "FROM users "
                "WHERE lower(name) = lower(%s) OR lower(email) = lower(%s) "
                "ORDER BY (lower(name) = lower(%s)) DESC, created_at ASC "
                "LIMIT 1",
                (identifier, identifier, identifier),
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def get_user_by_name(self, name: str):
        """Direct case-insensitive lookup by username (the unique identity)."""
        if not name:
            return None
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id, email, name, password_hash, department_id, "
                "       is_active, is_super_admin, role "
                "FROM users WHERE lower(name) = lower(%s)",
                (name,),
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def list_all_users(self) -> list:
        """Return all users (active and inactive) for admin views."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id, email, name, department_id, role, is_active, is_super_admin "
                "FROM users ORDER BY email"
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def list_users_full(self) -> list:
        """Return all users with department name, created_at, for the admin table."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT u.id, u.email, u.name, u.role, u.is_active, u.is_super_admin, "
                "u.created_at, u.last_login, d.name AS department "
                "FROM users u LEFT JOIN departments d ON u.department_id = d.id "
                "ORDER BY u.email"
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def count_users_by_role_and_status(self) -> dict:
        """Return role_counts and status_counts for the admin users page."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT role, COUNT(*) FROM users GROUP BY role")
            role_counts = {row["role"]: row["count"] for row in cur.fetchall()}
            cur.execute("SELECT is_active, COUNT(*) FROM users GROUP BY is_active")
            raw = {row["is_active"]: row["count"] for row in cur.fetchall()}
            status_counts = {
                "active": raw.get(True, 0),
                "inactive": raw.get(False, 0),
            }
            cur.close()
            return {"role_counts": role_counts, "status_counts": status_counts}
        finally:
            self._put_conn(conn)

    def get_user_by_id(self, user_id: str):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id, email, name, department_id, is_active, is_super_admin, role "
                "FROM users WHERE id=%s", (user_id,)
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def create_user(self, email: str, name: str, password_hash: str,
                    department_id: str, role: str = "user",
                    is_active: bool = True) -> str:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO users (email,name,password_hash,department_id,role,is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (email, name, password_hash, department_id, role, is_active)
            )
            row = cur.fetchone()
            cur.close()
            return str(row["id"])
        finally:
            self._put_conn(conn)

    def update_user(self, user_id: str, name: str, department_id: str,
                    role: str, is_active: bool) -> None:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE users SET name=%s, department_id=%s, role=%s, is_active=%s "
                "WHERE id=%s",
                (name, department_id, role, is_active, user_id)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def set_user_active(self, email: str, is_active: bool) -> bool:
        """Set a user's active status by email. Returns True if user found."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE users SET is_active=%s WHERE email=%s",
                (is_active, email)
            )
            affected = cur.rowcount
            cur.close()
            return affected > 0
        finally:
            self._put_conn(conn)

    def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email. Returns True if user found and deleted."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("DELETE FROM users WHERE email=%s", (email,))
            affected = cur.rowcount
            cur.close()
            return affected > 0
        finally:
            self._put_conn(conn)

    def update_password(self, email: str, password_hash: str) -> bool:
        """Update a user's password by email. Returns True if user found."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE users SET password_hash=%s WHERE email=%s",
                (password_hash, email)
            )
            affected = cur.rowcount
            cur.close()
            return affected > 0
        finally:
            self._put_conn(conn)

    def get_or_create_dept_by_name(self, name: str) -> str:
        """Return dept_id for the given department name, creating it if it doesn't exist."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT id FROM departments WHERE LOWER(name)=LOWER(%s) LIMIT 1", (name,))
            row = cur.fetchone()
            if row:
                cur.close()
                return str(row["id"])
            cur.execute(
                "INSERT INTO departments (name) VALUES (%s) RETURNING id", (name,)
            )
            row = cur.fetchone()
            cur.close()
            return str(row["id"])
        finally:
            self._put_conn(conn)

    def update_last_login(self, user_id: str):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))
            cur.close()
        finally:
            self._put_conn(conn)

    def get_or_create_default_dept(self) -> str:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT id FROM departments WHERE name='Default' LIMIT 1")
            row = cur.fetchone()
            if row:
                cur.close()
                return str(row["id"])
            cur.execute(
                "INSERT INTO departments (name, description) VALUES ('Default','Default department') RETURNING id"
            )
            row = cur.fetchone()
            cur.close()
            return str(row["id"])
        finally:
            self._put_conn(conn)

    def list_departments(self) -> list:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT id::TEXT, name FROM departments WHERE is_active=TRUE ORDER BY name")
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    # ── Department access grants ──────────────────────────────────────────────

    def list_dept_grants(self) -> list:
        """Return every active read-grant with both department names so the
        admin UI can render a human-readable table without a second roundtrip."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT g.id::TEXT AS id, "
                "       g.granting_dept_id::TEXT AS granting_dept_id, "
                "       gd.name AS granting_dept_name, "
                "       g.receiving_dept_id::TEXT AS receiving_dept_id, "
                "       rd.name AS receiving_dept_name, "
                "       g.access_type, g.expires_at::TEXT AS expires_at, "
                "       g.created_at::TEXT AS created_at "
                "FROM dept_access_grants g "
                "JOIN departments gd ON gd.id = g.granting_dept_id "
                "JOIN departments rd ON rd.id = g.receiving_dept_id "
                "WHERE (g.expires_at IS NULL OR g.expires_at > NOW()) "
                "ORDER BY gd.name, rd.name"
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def create_dept_grant(self, granting_dept_id: str, receiving_dept_id: str,
                          granted_by: str = None) -> dict:
        """Create (or upsert) a read grant. Returns the row including names."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO dept_access_grants "
                "  (granting_dept_id, receiving_dept_id, granted_by, access_type) "
                "VALUES (%s, %s, %s, 'read') "
                "ON CONFLICT (granting_dept_id, receiving_dept_id) DO UPDATE "
                "  SET access_type = EXCLUDED.access_type "
                "RETURNING id::TEXT",
                (granting_dept_id, receiving_dept_id, granted_by),
            )
            grant_id = cur.fetchone()["id"]
            cur.execute(
                "SELECT g.id::TEXT AS id, "
                "       g.granting_dept_id::TEXT AS granting_dept_id, "
                "       gd.name AS granting_dept_name, "
                "       g.receiving_dept_id::TEXT AS receiving_dept_id, "
                "       rd.name AS receiving_dept_name, "
                "       g.access_type, g.created_at::TEXT AS created_at "
                "FROM dept_access_grants g "
                "JOIN departments gd ON gd.id = g.granting_dept_id "
                "JOIN departments rd ON rd.id = g.receiving_dept_id "
                "WHERE g.id = %s",
                (grant_id,),
            )
            row = dict(cur.fetchone())
            cur.close()
            return row
        finally:
            self._put_conn(conn)

    def delete_dept_grant(self, grant_id: str) -> bool:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("DELETE FROM dept_access_grants WHERE id = %s",
                        (grant_id,))
            removed = cur.rowcount > 0
            cur.close()
            return removed
        finally:
            self._put_conn(conn)

    # ── App settings (small runtime-tunable key/value store) ──────────────────

    def get_setting(self, key: str) -> str | None:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            cur.close()
            return row["value"] if row else None
        finally:
            self._put_conn(conn)

    def set_setting(self, key: str, value: str, updated_by: str = None):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO app_settings (key, value, updated_by) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE "
                "  SET value = EXCLUDED.value, "
                "      updated_at = NOW(), "
                "      updated_by = EXCLUDED.updated_by",
                (key, value, updated_by),
            )
            cur.close()
        finally:
            self._put_conn(conn)

    # ── Chat ──────────────────────────────────────────────────────────────────

    def create_chat(self, user_id: str, department_id: str, title: str = None) -> str:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO chat (user_id,department_id,title) VALUES (%s,%s,%s) RETURNING id",
                (user_id, department_id, title)
            )
            row = cur.fetchone()
            cur.close()
            return str(row["id"])
        finally:
            self._put_conn(conn)

    def add_message(self, chat_id: str, role: str, content: str) -> str:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO messages (chat_id,role,content) VALUES (%s,%s,%s) RETURNING id",
                (chat_id, role, content)
            )
            row = cur.fetchone()
            cur.close()
            return str(row["id"])
        finally:
            self._put_conn(conn)

    def get_messages(self, chat_id: str, dept_id: str) -> list:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT m.role, m.content, m.created_at::TEXT "
                "FROM messages m JOIN chat c ON c.id=m.chat_id "
                "WHERE m.chat_id=%s AND c.department_id=%s "
                "ORDER BY m.created_at",
                (chat_id, dept_id)
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_user_chats(self, user_id: str, dept_id: str) -> list:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT c.id::TEXT, c.title, c.created_at::TEXT, c.updated_at::TEXT FROM chat c "
                "WHERE c.user_id=%s AND c.department_id=%s "
                "AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id) "
                "ORDER BY c.updated_at DESC LIMIT 50",
                (user_id, dept_id)
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def rename_chat(self, chat_id: str, user_id: str, title: str) -> bool:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE chat SET title=%s, updated_at=NOW() WHERE id=%s AND user_id=%s",
                (title, chat_id, user_id)
            )
            updated = cur.rowcount > 0
            cur.close()
            return updated
        finally:
            self._put_conn(conn)

    def delete_chat(self, chat_id: str, user_id: str) -> bool:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "DELETE FROM chat WHERE id=%s AND user_id=%s", (chat_id, user_id)
            )
            deleted = cur.rowcount > 0
            cur.close()
            return deleted
        finally:
            self._put_conn(conn)

    def delete_all_user_chats(self, user_id: str, dept_id: str):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "DELETE FROM chat WHERE user_id=%s AND department_id=%s",
                (user_id, dept_id)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def update_chat_title_if_empty(self, chat_id: str, title: str):
        """Set title only when it is currently NULL (auto-title on first message)."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE chat SET title=%s, updated_at=NOW() WHERE id=%s AND title IS NULL",
                (title[:60], chat_id)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def get_messages_full(self, chat_id: str, dept_id: str) -> list:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT m.id::TEXT, m.role, m.content, m.created_at::TEXT "
                "FROM messages m JOIN chat c ON c.id=m.chat_id "
                "WHERE m.chat_id=%s AND c.department_id=%s "
                "ORDER BY m.created_at",
                (chat_id, dept_id)
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_chat_meta(self, chat_id: str, user_id: str) -> dict:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "SELECT id::TEXT, title, created_at::TEXT, updated_at::TEXT "
                "FROM chat WHERE id=%s AND user_id=%s",
                (chat_id, user_id)
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    # ── Vector search ─────────────────────────────────────────────────────────

    @staticmethod
    def _accessible_dept_clause(dept_id: str) -> tuple:
        """Return (sql_fragment, [dept_id, dept_id]) for embeddings RBAC filter.

        Callers: unpack and insert dept_params at the correct position in params list.
        Example:
            clause, dept_params = self._accessible_dept_clause(dept_id)
            params = [embedding] + dept_params + [top_k]
        """
        sql = """(e.department_id = %s
                   OR e.department_id IN (
                       SELECT granting_dept_id FROM dept_access_grants
                       WHERE receiving_dept_id = %s
                         AND (expires_at IS NULL OR expires_at > NOW())))"""
        return sql, [dept_id, dept_id]

    @staticmethod
    def _accessible_dept_clause_docs(dept_id: str) -> tuple:
        """Return (sql_fragment, [dept_id, dept_id]) for documents RBAC filter."""
        sql = """(d.department_id = %s
                   OR d.department_id IN (
                       SELECT granting_dept_id FROM dept_access_grants
                       WHERE receiving_dept_id = %s
                         AND (expires_at IS NULL OR expires_at > NOW())))"""
        return sql, [dept_id, dept_id]

    def _quality_filter(self) -> str:
        """Exclude chunks below the configured OCR quality minimum and noise chunks."""
        threshold = cfg.ocr_quality_min
        return (
            f"(c.quality_score IS NULL OR c.quality_score >= {threshold})"
            f" AND (c.chunk_token_count IS NULL OR c.chunk_token_count > 10)"
        )

    def get_available_months(self, dept_id: str) -> list:
        """Return distinct non-null doc_month values accessible to dept_id, sorted."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            dept_clause, dept_params = self._accessible_dept_clause_docs(dept_id)
            cur.execute(
                f"""
                SELECT DISTINCT d.doc_month
                FROM documents d
                WHERE d.doc_month IS NOT NULL
                  AND {dept_clause}
                ORDER BY d.doc_month
                """,
                dept_params,
            )
            rows = [r[0] for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def vector_search(self, query_embedding, dept_id: str, top_k: int = 10,
                      months: list = None) -> list:
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            month_filter = "AND d.doc_month = ANY(%s)" if months else ""
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            params = [str(query_embedding)] + dept_params
            if months:
                params.append(months)
            params += [str(query_embedding), top_k]
            cur.execute(
                f"""
                SELECT e.chunk_id, c.chunk_text, c.document_id, c.page_num,
                       d.file_name, d.file_path, d.id as document_id,
                       d.doc_type, d.doc_month, d.party_name,
                       c.quality_score,
                       1 - (e.embedding <=> %s::vector) AS similarity
                FROM   embeddings e
                JOIN   chunks c ON c.id = e.chunk_id
                JOIN   documents d ON d.id = c.document_id
                WHERE  {dept_clause}
                  AND  {self._quality_filter()}
                {month_filter}
                ORDER  BY e.embedding <=> %s::vector
                LIMIT  %s
                """,
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def vector_search_by_filename(self, query_embedding, dept_id: str,
                                   filename_pattern: str, top_k: int = 10) -> list:
        """Vector search restricted to documents whose file_name contains filename_pattern."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            cur.execute(
                f"""
                SELECT e.chunk_id, c.chunk_text, c.document_id, c.page_num,
                       d.file_name, d.file_path, d.id as document_id,
                       d.doc_type, d.doc_month, d.party_name,
                       c.quality_score,
                       1 - (e.embedding <=> %s::vector) AS similarity
                FROM   embeddings e
                JOIN   chunks c ON c.id = e.chunk_id
                JOIN   documents d ON d.id = c.document_id
                WHERE  d.file_name ILIKE %s
                  AND  {dept_clause}
                  AND  {self._quality_filter()}
                ORDER  BY e.embedding <=> %s::vector
                LIMIT  %s
                """,
                [str(query_embedding), f'%{filename_pattern}%'] + dept_params + [str(query_embedding), top_k]
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def keyword_search_in_files(self, keywords: list, dept_id: str,
                                file_names: list, top_k: int = 5) -> list:
        """AND-first keyword search restricted to specific file names (active documents)."""
        if not keywords or not file_names:
            return []
        conn = self._get_conn()
        try:
            patterns = [f'%{kw.lower()}%' for kw in keywords]
            cur = self._cur(conn)
            placeholders = ",".join(["%s"] * len(file_names))
            and_conditions = " AND ".join(["c.chunk_text ILIKE %s"] * len(patterns))
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            cur.execute(
                f"""
                SELECT DISTINCT ON (c.id)
                       c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                       d.file_name, d.file_path, d.id AS document_id,
                       d.doc_type, d.doc_month, d.party_name,
                       c.quality_score, 0.90 AS similarity
                FROM   chunks c
                JOIN   documents d ON d.id = c.document_id
                JOIN   embeddings e ON e.chunk_id = c.id
                WHERE  {and_conditions}
                  AND  d.file_name IN ({placeholders})
                  AND  {dept_clause}
                  AND  {self._quality_filter()}
                ORDER  BY c.id
                LIMIT  %s
                """,
                patterns + file_names + dept_params + [top_k],
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            for r in rows:
                r["_keyword_hit"] = True
            return rows
        finally:
            self._put_conn(conn)

    def keyword_search(self, keywords: list, dept_id: str, top_k: int = 5,
                       months: list = None) -> list:
        """ILIKE keyword search to catch docs with poor embeddings (garbled/HTML content).
        Tries AND (all keywords in same chunk) first; falls back to ANY (at least one) if empty."""
        if not keywords:
            return []
        conn = self._get_conn()
        try:
            patterns = [f'%{kw.lower()}%' for kw in keywords]
            cur = self._cur(conn)
            month_filter = "AND d.doc_month = ANY(%s)" if months else ""

            # ── AND attempt: every keyword must appear in the same chunk ──────────
            and_conditions = " AND ".join(["c.chunk_text ILIKE %s"] * len(patterns))
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            and_params = patterns + dept_params
            if months:
                and_params.append(months)
            and_params.append(top_k)
            cur.execute(
                f"""
                SELECT DISTINCT ON (c.id)
                       c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                       d.file_name, d.file_path, d.id AS document_id,
                       d.doc_type, d.doc_month, d.party_name,
                       c.quality_score, 0.90 AS similarity
                FROM   chunks c
                JOIN   documents d ON d.id = c.document_id
                JOIN   embeddings e ON e.chunk_id = c.id
                WHERE  {and_conditions}
                  AND  {dept_clause}
                  AND  {self._quality_filter()}
                {month_filter}
                ORDER  BY c.id
                LIMIT  %s
                """,
                and_params,
            )
            rows = [dict(r) for r in cur.fetchall()]

            if not rows:
                # ── Prefix-fuzzy for long keywords (before ANY) ───────────────────
                # Two tiers handle both truncation variants and transposition typos:
                #   Tier A (6-char): "tramad" matches "tramadol" for clean truncations
                #   Tier B (4-char): "tram" catches transposition typos like "tramodal"
                #     where "tramodal"[:6]="tramod" misses but "tramodal"[:4]="tram" hits.
                #   Must run BEFORE ANY so a misspelled drug name isn't drowned out by
                #   generic words like "prices" finding pump purchase orders.
                _fuzzy_sql = f"""
                        SELECT DISTINCT ON (c.id)
                               c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                               d.file_name, d.file_path, d.id AS document_id,
                               d.doc_type, d.doc_month, d.party_name,
                               c.quality_score, 0.82 AS similarity
                        FROM   chunks c
                        JOIN   documents d ON d.id = c.document_id
                        JOIN   embeddings e ON e.chunk_id = c.id
                        WHERE  c.chunk_text ILIKE ANY(%s)
                          AND  {dept_clause}
                          AND  {self._quality_filter()}
                        {month_filter}
                        ORDER  BY c.id
                        LIMIT  %s
                        """
                # Tier A: 6-char prefix for keywords ≥ 7 chars
                fuzzy_patterns = [f'%{kw[:6].lower()}%' for kw in keywords if len(kw) >= 7]
                if fuzzy_patterns:
                    fuzzy_params: list = [fuzzy_patterns] + dept_params
                    if months:
                        fuzzy_params.append(months)
                    fuzzy_params.append(top_k)
                    cur.execute(_fuzzy_sql, fuzzy_params)
                    rows = [dict(r) for r in cur.fetchall()]

                # Tier B: 4-char prefix for keywords ≥ 8 chars (transposition typos)
                if not rows:
                    fuzzy4_patterns = [f'%{kw[:4].lower()}%' for kw in keywords if len(kw) >= 8]
                    if fuzzy4_patterns:
                        fuzzy4_params: list = [fuzzy4_patterns] + dept_params
                        if months:
                            fuzzy4_params.append(months)
                        fuzzy4_params.append(top_k)
                        cur.execute(_fuzzy_sql, fuzzy4_params)
                        rows = [dict(r) for r in cur.fetchall()]

            if not rows:
                # ── ANY fallback: at least one keyword must appear ────────────────
                any_params: list = [patterns] + dept_params
                if months:
                    any_params.append(months)
                any_params.append(top_k)
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (c.id)
                           c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                           d.file_name, d.file_path, d.id AS document_id,
                           d.doc_type, d.doc_month, d.party_name,
                           c.quality_score, 0.85 AS similarity
                    FROM   chunks c
                    JOIN   documents d ON d.id = c.document_id
                    JOIN   embeddings e ON e.chunk_id = c.id
                    WHERE  c.chunk_text ILIKE ANY(%s)
                      AND  {dept_clause}
                      AND  {self._quality_filter()}
                    {month_filter}
                    ORDER  BY c.id
                    LIMIT  %s
                    """,
                    any_params,
                )
                rows = [dict(r) for r in cur.fetchall()]

            cur.close()
            for r in rows:
                r["_keyword_hit"] = True
            return rows
        finally:
            self._put_conn(conn)

    def keyword_search_by_filename_pattern(self, keywords: list, dept_id: str,
                                            filename_pattern: str, top_k: int = 5) -> list:
        """Keyword search restricted to a filename ILIKE pattern (e.g. 'MAY-U3-RM-24-25-31')."""
        if not keywords or not filename_pattern:
            return []
        conn = self._get_conn()
        try:
            patterns = [f'%{kw.lower()}%' for kw in keywords]
            cur = self._cur(conn)
            and_conditions = " AND ".join(["c.chunk_text ILIKE %s"] * len(patterns))
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            cur.execute(
                f"""
                SELECT DISTINCT ON (c.id)
                       c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                       d.file_name, d.file_path, d.id AS document_id,
                       d.doc_type, d.doc_month, d.party_name,
                       c.quality_score, 0.90 AS similarity
                FROM   chunks c
                JOIN   documents d ON d.id = c.document_id
                JOIN   embeddings e ON e.chunk_id = c.id
                WHERE  {and_conditions}
                  AND  d.file_name ILIKE %s
                  AND  {dept_clause}
                  AND  {self._quality_filter()}
                ORDER  BY c.id
                LIMIT  %s
                """,
                patterns + [f'%{filename_pattern}%'] + dept_params + [top_k],
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            for r in rows:
                r["_keyword_hit"] = True
            return rows
        finally:
            self._put_conn(conn)

    # ── ColPali visual search ─────────────────────────────────────────────────

    def colpali_search(self, query_embedding: list, dept_id: str,
                       top_k: int = 10) -> list:
        """
        Cosine-similarity search against colpali_page_embeddings.
        Returns rows with doc_id, page_num, similarity, file_name, file_path.
        """
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            dept_clause, dept_params = self._accessible_dept_clause_docs(dept_id)
            vec = str(query_embedding)
            params = [vec] + dept_params + [vec, top_k]
            cur.execute(
                f"""
                SELECT cp.document_id, cp.page_num,
                       d.file_name, d.file_path,
                       1 - (cp.embedding <=> %s::vector) AS similarity
                FROM   colpali_page_embeddings cp
                JOIN   documents d ON d.id = cp.document_id
                WHERE  {dept_clause}
                ORDER  BY cp.embedding <=> %s::vector
                LIMIT  %s
                """,
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_chunks_for_colpali_pages(self, hits: list, dept_id: str,
                                     chunks_per_page: int = 3) -> list:
        """
        For each ColPali hit (doc_id, page_num), fetch the best text chunks
        from that page so they can be passed to the LLM alongside visual results.
        Returns chunk rows in the same shape as vector_search results.
        """
        if not hits:
            return []
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            chunk_rows: list = []
            dept_clause, dept_params = self._accessible_dept_clause(dept_id)
            for hit in hits:
                doc_id   = str(hit["document_id"])
                page_num = int(hit["page_num"])
                similarity = float(hit.get("similarity", 0.0))
                cur.execute(
                    f"""
                    SELECT c.id AS chunk_id, c.chunk_text, c.document_id, c.page_num,
                           d.file_name, d.file_path, d.id AS document_id,
                           d.doc_type, d.doc_month, d.party_name,
                           c.quality_score, %s AS similarity
                    FROM   chunks c
                    JOIN   documents d ON d.id = c.document_id
                    JOIN   embeddings e ON e.chunk_id = c.id
                    WHERE  c.document_id = %s
                      AND  c.page_num = %s
                      AND  {dept_clause}
                      AND  {self._quality_filter()}
                    ORDER  BY c.chunk_index
                    LIMIT  %s
                    """,
                    [similarity, doc_id, page_num] + dept_params + [chunks_per_page],
                )
                chunk_rows.extend(dict(r) for r in cur.fetchall())
            cur.close()
            return chunk_rows
        finally:
            self._put_conn(conn)

    def analytical_query(self, dept_id: str, months: list = None,
                         fiscal_year: str = None, doc_type: str = None,
                         party_name: str = None, product_keyword: str = None) -> list:
        """Return document-level structured rows for SQL-based analytical answers."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            dept_clause, dept_params = self._accessible_dept_clause_docs(dept_id)
            conditions = [dept_clause]
            params: list = dept_params[:]

            if months:
                conditions.append("d.doc_month = ANY(%s)")
                params.append(months)
            if fiscal_year:
                conditions.append("d.fiscal_year = %s")
                params.append(fiscal_year)
            if doc_type:
                conditions.append("d.doc_type ILIKE %s")
                params.append(f'%{doc_type}%')
            if party_name:
                conditions.append("d.party_name ILIKE %s")
                params.append(f'%{party_name}%')
            if product_keyword:
                # Use GIN index on product_names for fast lookup
                variants = _drug_variants(product_keyword)
                conditions.append("d.product_names && %s::text[]")
                params.append(variants)

            where_clause = " AND ".join(conditions)
            cur.execute(
                f"""
                SELECT d.file_name, d.doc_type, d.doc_month, d.fiscal_year,
                       d.party_name, d.doc_number, d.doc_date::TEXT,
                       d.total_amount, d.tax_amount, d.net_amount, d.doc_unit,
                       d.id::TEXT AS document_id, d.file_path
                FROM   documents d
                WHERE  {where_clause}
                ORDER  BY d.doc_date DESC NULLS LAST
                LIMIT  100
                """,
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    # ── Retrieval log ─────────────────────────────────────────────────────────

    def log_retrieval(self, chat_id, user_id, dept_id, query_text, chunk_ids, scores):
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO rag_retrieval_log "
                "(chat_id,user_id,department_id,query_text,retrieved_chunk_ids,similarity_scores) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (chat_id, user_id, dept_id, query_text, Json(chunk_ids), Json(scores))
            )
            cur.close()
        finally:
            self._put_conn(conn)

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def create_document_pending(self, dept_id: str, file_name: str,
                                file_path: str, user_id: str,
                                file_size: int = 0) -> str:
        """
        Insert a minimal placeholder record with ocr_status='pending' so the
        file shows in the upload list immediately after being stored in SeaweedFS.
        Uses ON CONFLICT DO NOTHING so re-uploads don't reset an already-processing doc.
        Returns the document UUID.
        """
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """INSERT INTO documents
                    (department_id, file_name, file_path, title, uploaded_by,
                     ocr_status, file_size, created_at)
                   VALUES (%s, %s, %s, %s, %s::uuid, 'pending', %s, NOW())
                   ON CONFLICT (file_name, department_id) DO UPDATE SET
                     file_path  = EXCLUDED.file_path,
                     file_size  = EXCLUDED.file_size,
                     created_at = CASE
                       WHEN documents.ocr_status = 'completed' THEN documents.created_at
                       ELSE NOW()
                     END,
                     ocr_status = CASE
                       WHEN documents.ocr_status = 'completed' THEN 'completed'
                       ELSE 'pending'
                     END
                   RETURNING id""",
                (dept_id, file_name, file_path, file_name, user_id, file_size),
            )
            doc_id = str(cur.fetchone()["id"])
            cur.close()
            return doc_id
        finally:
            self._put_conn(conn)

    def upsert_document(self, dept_id: str, file_name: str, file_path: str,
                        meta: dict, user_id: str = None) -> str:
        """
        Atomically insert or update a document record matched by (file_name, department_id).
        Returns the document UUID as a string.
        Requires migration_006's UNIQUE(file_name, department_id) constraint.

        user_id is required for new inserts (uploaded_by NOT NULL). When a
        placeholder was created by create_document_pending first, ON CONFLICT UPDATE
        runs and user_id is not needed.
        """
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            # Use provided user_id or fall back to a nil UUID so the NOT NULL
            # constraint is satisfied even when called from legacy paths.
            uploader = user_id or "00000000-0000-0000-0000-000000000000"
            cur.execute(
                """INSERT INTO documents
                    (department_id, file_name, file_path, title, uploaded_by,
                     doc_month, doc_unit, doc_type, fiscal_year, serial_no,
                     party_name, party_name_canonical, party_gstin, doc_date,
                     doc_number, total_amount, tax_amount, net_amount,
                     payment_terms, ref_doc_number, ocr_quality_score, ocr_status)
                   VALUES (%s,%s,%s,%s,%s::uuid, %s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s)
                   ON CONFLICT (file_name, department_id) DO UPDATE SET
                     file_path             = EXCLUDED.file_path,
                     doc_month             = EXCLUDED.doc_month,
                     doc_unit              = EXCLUDED.doc_unit,
                     doc_type              = EXCLUDED.doc_type,
                     fiscal_year           = EXCLUDED.fiscal_year,
                     serial_no             = EXCLUDED.serial_no,
                     party_name            = EXCLUDED.party_name,
                     party_name_canonical  = EXCLUDED.party_name_canonical,
                     party_gstin           = EXCLUDED.party_gstin,
                     doc_date              = EXCLUDED.doc_date,
                     doc_number            = EXCLUDED.doc_number,
                     total_amount          = EXCLUDED.total_amount,
                     tax_amount            = EXCLUDED.tax_amount,
                     net_amount            = EXCLUDED.net_amount,
                     payment_terms         = EXCLUDED.payment_terms,
                     ref_doc_number        = EXCLUDED.ref_doc_number,
                     ocr_quality_score     = EXCLUDED.ocr_quality_score,
                     ocr_status            = EXCLUDED.ocr_status
                   RETURNING id""",
                (
                    dept_id, file_name, file_path, file_name, uploader,
                    meta.get("doc_month"), meta.get("doc_unit"), meta.get("doc_type"),
                    meta.get("fiscal_year"), meta.get("serial_no"),
                    meta.get("party_name"), meta.get("party_name_canonical"),
                    meta.get("party_gstin"), meta.get("doc_date"),
                    meta.get("doc_number"), meta.get("total_amount"),
                    meta.get("tax_amount"), meta.get("net_amount"),
                    meta.get("payment_terms"), meta.get("ref_doc_number"),
                    meta.get("ocr_quality_score"), meta.get("ocr_status", "completed"),
                )
            )
            doc_id = str(cur.fetchone()["id"])
            cur.close()
            return doc_id
        finally:
            self._put_conn(conn)

    def delete_chunks_for_document(self, document_id: str) -> None:
        """Delete all chunks (and cascade-delete embeddings) for re-ingestion idempotency."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            # embeddings CASCADE DELETE from chunks via FK
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            cur.close()
        finally:
            self._put_conn(conn)

    def insert_chunk(self, document_id: str, chunk_index: int, chunk_text: str,
                     quality_score: float, page_num: int) -> str:
        """Insert one chunk. Returns chunk UUID."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """INSERT INTO chunks (document_id, chunk_index, chunk_text,
                                      chunk_token_count, page_num, quality_score)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (document_id, chunk_index, chunk_text,
                 len(chunk_text.split()), page_num, quality_score)
            )
            chunk_id = str(cur.fetchone()["id"])
            cur.close()
            return chunk_id
        finally:
            self._put_conn(conn)

    def insert_embedding(self, chunk_id: str, dept_id: str, embedding: list) -> None:
        """Insert one embedding vector for a chunk."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """INSERT INTO embeddings (chunk_id, department_id, embedding, embedding_model)
                   VALUES (%s, %s, %s::vector, %s)""",
                (chunk_id, dept_id, str(embedding), EMBEDDING_MODEL)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def delete_line_items_for_document(self, document_id: str) -> None:
        """Delete existing line items before re-ingestion (idempotency)."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "DELETE FROM document_line_items WHERE document_id = %s", (document_id,)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def insert_line_items(self, document_id: str, items: list) -> None:
        """Bulk insert line items for a document (single round-trip via execute_values)."""
        if not items:
            return
        rows = [
            (
                document_id,
                item.get("line_number"),
                item.get("description"),
                item.get("hsn_code"),
                item.get("quantity"),
                item.get("unit_of_measure"),
                item.get("unit_price"),
                item.get("amount"),
                item.get("tax_rate"),
            )
            for item in items
        ]
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            execute_values(
                cur,
                """INSERT INTO document_line_items
                    (document_id, line_number, description, hsn_code,
                     quantity, unit_of_measure, unit_price, amount, tax_rate)
                   VALUES %s""",
                rows,
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def insert_document_reference(self, source_doc_id: str, ref_doc_number: str) -> None:
        """Record a cross-document reference. Ignore duplicates."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """INSERT INTO document_references (source_doc_id, ref_doc_number)
                   VALUES (%s, %s)
                   ON CONFLICT (source_doc_id, ref_doc_number) DO NOTHING""",
                (source_doc_id, ref_doc_number)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def log_failed_extraction(self, document_id: str, step: str, error: str) -> None:
        """Log an extraction failure for operator review via /admin/failed-extractions."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "INSERT INTO failed_extractions (document_id, step, error) VALUES (%s,%s,%s)",
                (document_id, step, error[:1000])
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def get_failed_extractions(self, dept_id: str, limit: int = 100) -> list:
        """Return recent failed extractions scoped to the given department."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """SELECT fe.id::TEXT, fe.document_id::TEXT, d.file_name,
                          fe.step, fe.error, fe.attempted_at::TEXT
                   FROM failed_extractions fe
                   JOIN documents d ON d.id = fe.document_id
                   WHERE d.department_id = %s
                   ORDER BY fe.attempted_at DESC
                   LIMIT %s""",
                (dept_id, limit)
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_documents_for_backfill(self, limit: int = 20, offset: int = 0) -> list:
        """Return documents for the Phase 3 backfill script, ordered by creation date."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """SELECT id::TEXT, file_name, file_path, department_id::TEXT,
                          ocr_status, created_at::TEXT
                   FROM documents
                   ORDER BY created_at ASC
                   LIMIT %s OFFSET %s""",
                (limit, offset)
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_document_by_id(self, doc_id: str) -> dict | None:
        """Return a single document row by UUID, or None if not found."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                """SELECT id::TEXT, file_name, file_path, department_id::TEXT,
                          ocr_status, uploaded_by::TEXT
                   FROM documents
                   WHERE id = %s::uuid""",
                (doc_id,)
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            self._put_conn(conn)

    def reset_document_status(self, doc_id: str) -> None:
        """Reset a failed document back to 'pending' so ingestion can be retried."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute(
                "UPDATE documents SET ocr_status = 'pending' WHERE id = %s::uuid",
                (doc_id,)
            )
            cur.close()
        finally:
            self._put_conn(conn)

    def list_documents_for_dept(self, dept_id: str = None, limit: int = 10000) -> list:
        """Return recent documents for a department (or all departments if dept_id is None)."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            # COALESCE: documents created by the complete backend use embed_status;
            # older docs from rag_pipeline use ocr_status. Read whichever is set.
            select = """SELECT id::TEXT, file_name, file_path,
                               COALESCE(ocr_status, embed_status, 'pending') AS ocr_status,
                               COALESCE(file_size, 0) AS file_size,
                               COALESCE(last_embedded_at, created_at)::TEXT AS uploaded_at,
                               total_amount, party_name,
                               COALESCE(current_stage, '') AS current_stage,
                               COALESCE(ocr_current_page, 0) AS ocr_current_page,
                               COALESCE(ocr_total_pages, 0) AS ocr_total_pages,
                               processing_started_at::TEXT AS processing_started_at,
                               last_embedded_at::TEXT AS processing_finished_at"""
            if dept_id is None:
                cur.execute(
                    f"""{select}
                       FROM documents
                       ORDER BY created_at DESC NULLS LAST""",
                )
            else:
                cur.execute(
                    f"""{select}
                       FROM documents
                       WHERE department_id = %s
                       ORDER BY created_at DESC NULLS LAST""",
                    (dept_id,)
                )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            self._put_conn(conn)

    def get_document_count(self) -> int:
        """Total number of documents in the documents table."""
        conn = self._get_conn()
        try:
            cur = self._cur(conn)
            cur.execute("SELECT COUNT(*) AS n FROM documents")
            row = cur.fetchone()
            cur.close()
            return int(row["n"]) if row else 0
        finally:
            self._put_conn(conn)
