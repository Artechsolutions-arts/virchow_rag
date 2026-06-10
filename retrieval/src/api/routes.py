import logging
import os
import re
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, BackgroundTasks, Query, Form
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr, field_validator
from src.auth.jwt_auth import (
    require_admin,
    hash_password, verify_password, create_token, get_current_user, decode_token
)
from src.config import MAX_QUESTION_LENGTH

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_SAFE_FILENAME_RE = re.compile(r'^[A-Za-z0-9 _\-\.]{1,200}\.pdf$', re.IGNORECASE)

logger = logging.getLogger(__name__)

_GENERIC_SERVER_ERROR = "An error occurred processing your request. Please try again."


class RegisterRequest(BaseModel):
    # Username is the primary identity (case-insensitive unique). Email is
    # informational only — multiple users may share an email.
    name: str
    password: str
    email: EmailStr | None = None
    department_id: str = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username cannot be empty")
        return v[:120]

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    # ``email`` here is a misnomer kept for backward-compat with the Next.js
    # proxy at /api/auth/login. It can be either an email address or a
    # username (the `users.name` column) — the lookup is permissive.
    email: str
    password: str


def create_router(svc):
    router = APIRouter()

    # ── Auth ──────────────────────────────────────────────────────────────────

    @router.post("/auth/register")
    async def register(req: RegisterRequest):
        dept_id = req.department_id or svc.rbac.get_or_create_default_dept()
        if svc.rbac.get_user_by_name(req.name):
            raise HTTPException(status_code=409, detail="Username already taken")
        # Email is now informational; fall back to a derived placeholder when
        # the client doesn't supply one so the NOT NULL constraint is satisfied.
        email_value = req.email or f"{req.name}@local"
        user_id = svc.rbac.create_user(
            email=email_value,
            name=req.name,
            password_hash=hash_password(req.password),
            department_id=dept_id,
        )
        token = create_token(user_id, email_value, dept_id, role="user")
        return JSONResponse({"token": token, "user_id": user_id, "dept_id": dept_id, "name": req.name})

    @router.post("/auth/login")
    async def login(req: LoginRequest):
        # Accept either an email or a username — the lookup tries both.
        identifier = (req.email or "").strip()
        user = svc.rbac.get_user_by_email_or_name(identifier)
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email/username or password")
        if not user["is_active"]:
            raise HTTPException(status_code=403, detail="Account is disabled")
        svc.rbac.update_last_login(str(user["id"]))
        dept_id = str(user["department_id"])
        token = create_token(str(user["id"]), user["email"], dept_id, role=user.get("role", "user"), is_super_admin=bool(user.get("is_super_admin", False)))
        return JSONResponse({
            "token": token,
            "user_id": str(user["id"]),
            "dept_id": dept_id,
            "name": user["name"],
            "role": user["role"],
            "is_super_admin": user["is_super_admin"],
        })

    @router.post("/auth/refresh")
    async def refresh_token(user: dict = Depends(get_current_user)):
        new_token = create_token(user["sub"], user["email"], user.get("dept_id", ""), role=user.get("role", "user"))
        return JSONResponse({"token": new_token})

    # B-H3: departments was public — now requires authentication
    @router.get("/auth/departments")
    async def list_departments(user: dict = Depends(get_current_user)):
        return JSONResponse(svc.rbac.list_departments())

    # ── Department access grants (admin-only) ────────────────────────────────
    # Let admins give one department read access to another's documents,
    # so e.g. the Sales team can search documents owned by the Default
    # department without re-tagging every row.

    def _require_admin(u: dict):
        if not (u.get("is_super_admin") or u.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="Admin only")

    @router.get("/admin/dept-grants")
    async def list_dept_grants(user: dict = Depends(get_current_user)):
        _require_admin(user)
        return JSONResponse(svc.rbac.list_dept_grants())

    @router.post("/admin/dept-grants")
    async def create_dept_grant(req: Request, user: dict = Depends(get_current_user)):
        _require_admin(user)
        body = await req.json()
        granting = (body.get("granting_dept_id") or "").strip()
        receiving = (body.get("receiving_dept_id") or "").strip()
        if not granting or not receiving:
            raise HTTPException(status_code=400, detail="granting_dept_id and receiving_dept_id are required")
        if granting == receiving:
            raise HTTPException(status_code=400, detail="A department cannot grant access to itself")
        created = svc.rbac.create_dept_grant(granting, receiving, user["sub"])
        return JSONResponse(created, status_code=201)

    @router.delete("/admin/dept-grants/{grant_id}")
    async def delete_dept_grant(grant_id: str, user: dict = Depends(get_current_user)):
        _require_admin(user)
        removed = svc.rbac.delete_dept_grant(grant_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Grant not found")
        return JSONResponse({"ok": True})

    # ── Query ─────────────────────────────────────────────────────────────────

    @router.post("/query")
    async def rag_query(
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            question = body.get("question", "")
            chat_id = body.get("chat_id") or None
        else:
            form = await request.form()
            question = form.get("question", "")
            chat_id = form.get("chat_id") or None
            if chat_id == "null":
                chat_id = None

        # B-C4: validate question input
        question = question.strip() if isinstance(question, str) else ""
        if not question:
            raise HTTPException(status_code=422, detail="Question cannot be empty")
        if len(question) > MAX_QUESTION_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters",
            )

        try:
            result = svc.query(
                question=question,
                user_id=user["sub"],
                dept_id=user["dept_id"],
                chat_id=chat_id,
            )
            return JSONResponse(result)
        except Exception as e:
            # B-H5: log real error internally, return generic message to client
            logger.error(f"Query error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=_GENERIC_SERVER_ERROR)

    # ── Chat history ──────────────────────────────────────────────────────────

    @router.get("/chats")
    async def get_chats(user: dict = Depends(get_current_user)):
        chats = svc.rbac.get_user_chats(user["sub"], user["dept_id"])
        return JSONResponse(chats)

    @router.post("/chats/create")
    async def create_chat_session(user: dict = Depends(get_current_user)):
        chat_id = svc.rbac.create_chat(user["sub"], user["dept_id"], title=None)
        return JSONResponse({"chat_session_id": chat_id})

    @router.get("/chats/{chat_id}/messages")
    async def get_messages(chat_id: str, user: dict = Depends(get_current_user)):
        # B-H4: verify this chat belongs to the requesting user before returning messages
        meta = svc.rbac.get_chat_meta(chat_id, user["sub"])
        if not meta:
            raise HTTPException(status_code=404, detail="Chat not found")
        msgs = svc.rbac.get_messages_full(chat_id, user["dept_id"])
        return JSONResponse(msgs)

    @router.get("/chats/{chat_id}/meta")
    async def get_chat_meta(chat_id: str, user: dict = Depends(get_current_user)):
        meta = svc.rbac.get_chat_meta(chat_id, user["sub"])
        if not meta:
            raise HTTPException(status_code=404, detail="Chat not found")
        return JSONResponse(meta)

    @router.put("/chats/{chat_id}/rename")
    async def rename_chat(chat_id: str, request: Request, user: dict = Depends(get_current_user)):
        body = await request.json()
        title = str(body.get("name", "")).strip()[:60]
        if not title:
            raise HTTPException(status_code=422, detail="Title cannot be empty")
        updated = svc.rbac.rename_chat(chat_id, user["sub"], title)
        if not updated:
            raise HTTPException(status_code=404, detail="Chat not found")
        return JSONResponse({"ok": True})

    @router.delete("/chats/{chat_id}")
    @router.delete("/api/chat/delete-chat-session/{chat_id}")
    async def delete_chat(chat_id: str, user: dict = Depends(get_current_user)):
        deleted = svc.rbac.delete_chat(chat_id, user["sub"])
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat not found")
        return JSONResponse({"ok": True})

    @router.delete("/chats")
    async def delete_all_chats(user: dict = Depends(get_current_user)):
        svc.rbac.delete_all_user_chats(user["sub"], user["dept_id"])
        return JSONResponse({"ok": True})

    # ── Document redirect ─────────────────────────────────────────────────────

    @router.get("/api/documents/{doc_id}")
    async def get_document(doc_id: str, user: dict = Depends(get_current_user)):
        import os
        from fastapi.responses import RedirectResponse

        conn = svc.rbac._get_conn()
        try:
            cur = svc.rbac._cur(conn)
            cur.execute("SELECT file_name, file_path FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            svc.rbac._put_conn(conn)

        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

        seaweed_url = svc._get_seaweedfs_url(row["file_path"])
        return RedirectResponse(url=seaweed_url, status_code=302)

    # ── Document list ─────────────────────────────────────────────────────────

    @router.get("/documents")
    async def list_documents(
        user: dict = Depends(get_current_user),
        limit: int = Query(default=10000, ge=1),
    ):
        admin_roles = {"admin", "curator", "global_curator"}
        dept_filter = None if user.get("role") in admin_roles else user["dept_id"]
        rows = svc.rbac.list_documents_for_dept(dept_filter, limit=limit)
        status_map = {
            "completed": "COMPLETED",
            "failed": "FAILED TO UPLOAD",
        }
        uploads = [
            {
                "id": r["id"],
                "name": r["file_name"],
                "path": r["file_name"],
                "type": "application/pdf",
                "size": r.get("file_size") or 0,
                "uploaded_by": user.get("email", ""),
                "uploaded_at": r.get("uploaded_at") or "",
                "status": status_map.get(r["ocr_status"], "IN PROGRESS"),
                "version": "1",
                "current_stage": r.get("current_stage") or "",
                "ocr_current_page": r.get("ocr_current_page") or 0,
                "ocr_total_pages": r.get("ocr_total_pages") or 0,
                "processing_started_at": r.get("processing_started_at") or "",
                "processing_finished_at": r.get("processing_finished_at") or "",
            }
            for r in rows
        ]
        return {"uploads": uploads, "total": len(uploads), "limit": limit}

    # ── Document upload (TODO-2) ──────────────────────────────────────────────

    @router.post("/documents/upload")
    async def upload_document(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        user: dict = Depends(get_current_user),
    ):
        """
        Accept a PDF upload and ingest it.

        When INGEST_URL is configured, the file is forwarded to the complete
        backend (MPS-accelerated, RabbitMQ-queued). Otherwise falls back to
        the local BackgroundTasks ingestion path.
        """
        raw_name = os.path.basename(file.filename or "")
        if not raw_name or not _SAFE_FILENAME_RE.match(raw_name):
            raise HTTPException(
                status_code=422,
                detail="Only PDF files are accepted. Filename may contain letters, digits, spaces, _ - .",
            )

        pdf_bytes = await file.read()
        if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {_MAX_UPLOAD_BYTES // (1024*1024)} MB)",
            )
        if not pdf_bytes:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")

        dept_id = user["dept_id"]
        user_id = user["sub"]
        file_name = raw_name

        # ── Forward to complete backend if configured ─────────────────────────
        from src.config import cfg as _cfg
        if _cfg.ingest_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{_cfg.ingest_url}/documents/upload",
                        data={"dept_id": dept_id, "user_id": user_id},
                        files={"file": (file_name, pdf_bytes, "application/pdf")},
                    )
                data = resp.json() if resp.content else {}
                logger.info("[Upload] Forwarded %s to complete backend — status %d", file_name, resp.status_code)
                return JSONResponse(data, status_code=resp.status_code)
            except Exception as e:
                logger.error("[Upload] Complete backend unreachable (%s) — falling back to local ingestion", e)
                # Fall through to local path below

        # ── Local fallback: SeaweedFS + BackgroundTasks ───────────────────────
        try:
            file_path = svc.store_in_seaweedfs(pdf_bytes, file_name, dept_id)
        except Exception as e:
            logger.error("[Upload] SeaweedFS store failed for %s: %s", file_name, e)
            raise HTTPException(status_code=502, detail="Failed to store file. Please retry.")

        try:
            svc.rbac.create_document_pending(dept_id, file_name, file_path, user_id,
                                             file_size=len(pdf_bytes))
        except Exception as e:
            logger.warning("[Upload] Failed to create pending record for %s: %s", file_name, e)

        background_tasks.add_task(
            _run_ingestion_task, pdf_bytes, file_name, dept_id, file_path, user_id, svc
        )

        return JSONResponse(
            {"ok": True, "file_name": file_name, "file_path": file_path},
            status_code=202,
        )

    # ── Document restart (retry failed ingestion) ─────────────────────────────

    @router.post("/documents/restart")
    async def restart_document(
        background_tasks: BackgroundTasks,
        upload_ids: str = Form(...),
        user: dict = Depends(get_current_user),
    ):
        """
        Re-queue a failed document for ingestion without re-uploading it.

        When INGEST_URL is configured, delegates to the complete backend
        (which reads the file from disk). Otherwise fetches from SeaweedFS
        and runs the local BackgroundTasks ingestion path.
        """
        doc_id = upload_ids.strip()
        doc = svc.rbac.get_document_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        dept_id = user["dept_id"]
        admin_roles = {"admin", "curator", "global_curator"}
        if user.get("role") not in admin_roles and str(doc.get("department_id")) != dept_id:
            raise HTTPException(status_code=403, detail="Access denied")

        file_name = doc["file_name"]
        user_id   = user["sub"]

        # ── Forward to complete backend if configured ─────────────────────────
        from src.config import cfg as _cfg
        if _cfg.ingest_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{_cfg.ingest_url}/documents/restart",
                        data={"upload_ids": doc_id, "dept_id": dept_id},
                    )
                data = resp.json() if resp.content else {}
                logger.info("[Restart] Forwarded %s to complete backend — status %d", file_name, resp.status_code)
                return JSONResponse(data, status_code=resp.status_code)
            except Exception as e:
                logger.error("[Restart] Complete backend unreachable (%s) — falling back to local ingestion", e)

        # ── Local fallback ────────────────────────────────────────────────────
        file_path  = doc["file_path"]
        seaweed_url = svc._get_seaweedfs_url(file_path)
        try:
            import httpx
            r = httpx.get(seaweed_url, timeout=60)
            r.raise_for_status()
            pdf_bytes = r.content
        except Exception as e:
            logger.error("[Restart] Failed to fetch %s from SeaweedFS: %s", file_name, e)
            raise HTTPException(status_code=502, detail="Could not fetch file from storage")

        svc.rbac.reset_document_status(doc_id)

        background_tasks.add_task(
            _run_ingestion_task, pdf_bytes, file_name, dept_id, file_path, user_id, svc
        )

        return JSONResponse({"ok": True, "file_name": file_name}, status_code=202)

    # ── Admin: create user ────────────────────────────────────────────────────

    @router.post("/admin/users")
    async def admin_create_user(request: Request, user: dict = Depends(require_admin)):
        """
        Create a new user from the admin panel.
        Maps department name → dept_id (creates department if it doesn't exist).
        Maps status → is_active (active=True, hold/terminated=False).
        """
        body = await request.json()
        email = (body.get("email") or "").strip()
        name = (body.get("personal_name") or "").strip()
        password = (body.get("password") or "").strip()
        department = (body.get("department") or "").strip()
        role = (body.get("role") or "user").strip()
        status = (body.get("status") or "active").strip()

        if not email or not name or not password:
            raise HTTPException(status_code=400, detail="email, personal_name, and password are required")
        if role not in ("user", "admin", "hod"):
            raise HTTPException(status_code=400, detail="role must be user, admin, or hod")

        existing = svc.rbac.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

        dept_id = (
            svc.rbac.get_or_create_dept_by_name(department)
            if department
            else svc.rbac.get_or_create_default_dept()
        )
        is_active = status == "active"
        user_id = svc.rbac.create_user(
            email=email,
            name=name,
            password_hash=hash_password(password),
            department_id=dept_id,
            role=role,
            is_active=is_active,
        )
        return JSONResponse({"user_id": user_id, "email": email, "name": name}, status_code=201)

    @router.get("/admin/users-list")
    async def admin_list_users(user: dict = Depends(require_admin)):
        """Return all users in the system for the admin Users page."""
        rows = svc.rbac.list_all_users()
        users = [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "name": r.get("name") or "",
                "is_active": r["is_active"],
                "is_superuser": r.get("is_super_admin", False),
                "is_verified": True,
                "role": r.get("role", "user"),
                "preferences": {},
                "team_name": None,
                "password_configured": True,
            }
            for r in rows
        ]
        return JSONResponse({
            "accepted": users,
            "invited": [],
            "accepted_pages": 1,
            "invited_pages": 1,
        })

    async def _do_update_user(user_id: str, request: Request):
        body = await request.json()
        name = (body.get("personal_name") or "").strip()
        department = (body.get("department") or "").strip()
        role = (body.get("role") or "user").strip()
        status = (body.get("status") or "active").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        if role not in ("user", "admin", "hod"):
            raise HTTPException(status_code=400, detail="Invalid role")

        target = svc.rbac.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        dept_id = (
            svc.rbac.get_or_create_dept_by_name(department)
            if department
            else svc.rbac.get_or_create_default_dept()
        )
        is_active = status == "active"
        svc.rbac.update_user(user_id, name, dept_id, role, is_active)
        return JSONResponse({"ok": True})

    def _require_admin_cookie_or_bearer(request: Request) -> dict:
        """Auth check supporting both Bearer token and fastapiusersauth cookie."""
        auth = request.headers.get("Authorization", "")
        token = None
        if auth.startswith("Bearer "):
            token = auth[7:]
        if not token:
            token = request.cookies.get("fastapiusersauth")
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        payload = decode_token(token)
        user_db = svc.rbac.get_user_by_id(payload["sub"])
        if not user_db:
            raise HTTPException(status_code=401, detail="User not found")
        if user_db.get("role") != "admin" and not user_db.get("is_super_admin"):
            raise HTTPException(status_code=403, detail="Admin access required")
        return payload

    @router.patch("/admin/users/{user_id}")
    async def admin_update_user(user_id: str, request: Request, user: dict = Depends(require_admin)):
        return await _do_update_user(user_id, request)

    @router.patch("/api/manage/admin/users/{user_id}")
    async def admin_update_user_rewrite(user_id: str, request: Request):
        _require_admin_cookie_or_bearer(request)
        return await _do_update_user(user_id, request)

    @router.patch("/api/manage/admin/deactivate-user")
    async def admin_deactivate_user(request: Request):
        _require_admin_cookie_or_bearer(request)
        body = await request.json()
        email = (body.get("user_email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="user_email required")
        found = svc.rbac.set_user_active(email, False)
        if not found:
            raise HTTPException(status_code=404, detail="User not found")
        return JSONResponse({"ok": True})

    @router.patch("/api/manage/admin/activate-user")
    async def admin_activate_user(request: Request):
        _require_admin_cookie_or_bearer(request)
        body = await request.json()
        email = (body.get("user_email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="user_email required")
        found = svc.rbac.set_user_active(email, True)
        if not found:
            raise HTTPException(status_code=404, detail="User not found")
        return JSONResponse({"ok": True})

    @router.delete("/api/manage/admin/delete-user")
    async def admin_delete_user(request: Request):
        _require_admin_cookie_or_bearer(request)
        body = await request.json()
        email = (body.get("user_email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="user_email required")
        found = svc.rbac.delete_user_by_email(email)
        if not found:
            raise HTTPException(status_code=404, detail="User not found")
        return JSONResponse({"ok": True})

    @router.post("/api/password/reset_password")
    async def admin_reset_password(request: Request):
        _require_admin_cookie_or_bearer(request)
        body = await request.json()
        email = (body.get("user_email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="user_email required")
        user = svc.rbac.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        import secrets, string
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
        svc.rbac.update_password(email, hash_password(new_password))
        return JSONResponse({"user_id": str(user["id"]), "new_password": new_password})

    @router.get("/admin/users-accepted")
    async def admin_list_accepted_users(user: dict = Depends(require_admin)):
        """Return all users in FullUserSnapshot format for the admin users table."""
        rows = svc.rbac.list_users_full()
        snapshots = [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "role": r.get("role", "user"),
                "is_active": r["is_active"],
                "password_configured": True,
                "personal_name": r.get("name") or None,
                "department": r.get("department") or None,
                "company": None,
                "status": "active" if r["is_active"] else "inactive",
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "updated_at": r["last_login"].isoformat() if r.get("last_login") else (
                    r["created_at"].isoformat() if r.get("created_at") else None
                ),
                "groups": [],
                "is_scim_synced": False,
            }
            for r in rows
        ]
        return JSONResponse(snapshots)

    @router.get("/admin/users-counts")
    async def admin_user_counts(user: dict = Depends(require_admin)):
        """Return role and status counts for the admin Users page summary."""
        return JSONResponse(svc.rbac.count_users_by_role_and_status())

    # ── Admin: failed extractions (TODO-1) ───────────────────────────────────

    @router.get("/admin/failed-extractions")
    async def get_failed_extractions(
        limit: int = Query(default=50, ge=1, le=500),
        user: dict = Depends(require_admin),
    ):
        """
        Returns the most recent failed LLM extraction records for operator review.
        Scoped to the requesting user's department.
        """
        rows = svc.rbac.get_failed_extractions(user["dept_id"], limit=limit)
        return JSONResponse(rows)

    # ── Health ────────────────────────────────────────────────────────────────

    @router.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    @router.get("/api/health")
    async def api_health():
        return JSONResponse({"status": "ok"})

    # ── Web-app compat endpoints (called server-side by Next.js) ─────────────

    @router.get("/api/auth/type")
    async def auth_type():
        return JSONResponse({
            "auth_type": "basic",
            "requires_verification": False,
            "anonymous_user_enabled": False,
            "password_min_length": 8,
            "has_users": True,
            "oauth_enabled": False,
        })

    @router.get("/api/me")
    async def web_me(request: Request):
        token = request.cookies.get("fastapiusersauth")
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            payload = decode_token(token)
        except HTTPException:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = svc.rbac.get_user_by_id(payload["sub"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        name = user.get("name", "")
        return JSONResponse({
            "id": str(user["id"]),
            "email": user["email"],
            "is_active": user["is_active"],
            "is_superuser": user.get("is_super_admin", False),
            "is_verified": True,
            "role": user.get("role", "basic"),
            "is_anonymous_user": False,
            "team_name": None,
            "password_configured": True,
            "preferences": {
                "auto_scroll": True,
                "temperature_override_enabled": False,
                "default_app_mode": "chat",
            },
            "personalization": {
                "name": name,
                "theme_preference": None,
                "auto_scroll": True,
                "default_app_mode": "chat",
                "pinned_assistants": None,
            },
        })

    @router.get("/api/chat/get-chat-session/{chat_id}")
    async def get_chat_session_compat(chat_id: str, request: Request):
        token = None
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.cookies.get("fastapiusersauth")
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            user = decode_token(token)
        except HTTPException:
            raise HTTPException(status_code=401, detail="Invalid token")

        import re as _re
        _SINGLE_PREFIX_RE = _re.compile(
            r'^\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n',
            _re.IGNORECASE,
        )
        _MULTI_PREFIX_RE = _re.compile(
            r'\n\n\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n',
            _re.IGNORECASE,
        )

        def _strip_prefix(content: str, role: str) -> str:
            if role != "assistant":
                return content
            if _SINGLE_PREFIX_RE.match(content) and not _MULTI_PREFIX_RE.search(content):
                return _SINGLE_PREFIX_RE.sub("", content, count=1).strip()
            return content

        msgs = svc.rbac.get_messages_full(chat_id, user["dept_id"])
        meta = svc.rbac.get_chat_meta(chat_id, user["sub"]) or {}
        messages = [
            {
                "message_id": idx + 1,
                "message_type": "user" if m["role"] == "user" else "assistant",
                "research_type": None,
                "parent_message": idx if idx > 0 else None,
                "latest_child_message": idx + 2 if idx < len(msgs) - 1 else None,
                "message": _strip_prefix(m["content"], m["role"]),
                "rephrased_query": None,
                "context_docs": None,
                "time_sent": m.get("created_at"),
                "overridden_model": "",
                "alternate_assistant_id": None,
                "chat_session_id": chat_id,
                "citations": None,
                "files": [],
                "tool_call": None,
                "current_feedback": None,
                "sub_questions": [],
                "comments": None,
                "parentMessageId": None,
                "refined_answer_improvement": None,
                "is_agentic": None,
            }
            for idx, m in enumerate(msgs)
        ]
        return JSONResponse({
            "chat_session_id": chat_id,
            "description": meta.get("title") or "New Chat",
            "persona_id": 0,
            "persona_name": "Virchow Assistant",
            "messages": messages,
            "time_created": meta.get("created_at"),
            "time_updated": meta.get("updated_at"),
            "shared_status": "private",
            "current_temperature_override": None,
            "current_alternate_model": "",
            "owner_name": None,
            "packets": [],
        })

    @router.patch("/api/chat/chat-session/{chat_id}")
    async def patch_chat_session(chat_id: str, request: Request):
        """Accept share/unshare or other session updates from the frontend."""
        token = request.cookies.get("fastapiusersauth") or (
            request.headers.get("Authorization", "")[7:] or None
        )
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            decode_token(token)
        except HTTPException:
            raise HTTPException(status_code=401, detail="Invalid token")
        body = await request.json()
        return JSONResponse({"chat_session_id": chat_id, **body})

    # ── Admin: User history ───────────────────────────────────────────────────

    @router.get("/api/admin/users-history")
    async def admin_list_users_history(user: dict = Depends(require_admin)):
        """Return all users with their chat counts for the admin history view."""
        users = svc.rbac.list_all_users()
        result = []
        for u in users:
            chats = svc.rbac.get_user_chats(u["id"], u.get("department_id", ""))
            result.append({
                "id": u["id"],
                "email": u["email"],
                "name": u.get("name") or u["email"],
                "role": u.get("role", "user"),
                "department_id": u.get("department_id"),
                "chat_count": len(chats),
            })
        return JSONResponse(result)

    @router.get("/api/admin/users-history/{user_id}")
    async def admin_get_user_history(user_id: str, user: dict = Depends(require_admin)):
        """Return full chat history (with messages) for one user."""
        target = svc.rbac.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        chats = svc.rbac.get_user_chats(user_id, target.get("department_id", ""))
        history = []
        for chat in chats:
            cid = str(chat.get("id") or chat.get("chat_id", ""))
            msgs = svc.rbac.get_messages_full(cid, target.get("department_id", ""))
            history.append({
                "chat_id": cid,
                "title": chat.get("title") or "New Chat",
                "created_at": str(chat.get("created_at", "")),
                "messages": [
                    {
                        "role": m["role"],
                        "content": m["content"],
                        "created_at": str(m.get("created_at", "")),
                    }
                    for m in msgs
                ],
            })
        return JSONResponse({
            "user": {
                "id": target["id"],
                "email": target["email"],
                "name": target.get("name") or target["email"],
                "role": target.get("role", "user"),
            },
            "chats": history,
        })

    @router.get("/api/settings")
    async def web_settings():
        return JSONResponse({
            "anonymous_user_enabled": False,
            "invite_only_enabled": False,
            "notifications": [],
            "needs_reindexing": False,
            "gpu_enabled": False,
            "application_status": "active",
            "auto_scroll": True,
            "temperature_override_enabled": False,
            "query_history_type": "disabled",
        })

    @router.get("/api/enterprise-settings")
    async def web_enterprise_settings():
        return JSONResponse({
            "whitelabeling": None,
            "custom_header_content": None,
            "custom_header_logo": None,
            "two_factor_auth_enabled": False,
            "anonymous_user_enabled": False,
            "enable_paid_enterprise_edition_features": False,
        })

    # ── Frontend (single-page chat app) ───────────────────────────────────────

    @router.get("/", response_class=HTMLResponse)
    async def frontend():
        return HTMLResponse(content=_HTML)

    return router


def _run_ingestion_task(pdf_bytes: bytes, file_name: str, dept_id: str,
                        file_path: str, user_id: str, svc) -> None:
    """Background task: run full ingestion pipeline for one PDF."""
    from src.ingestion.ingestion_pipeline import ingest_document
    logger.info("[Upload] Background ingestion starting for %s", file_name)
    try:
        document_id = ingest_document(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            dept_id=dept_id,
            file_path=file_path,
            db=svc.rbac,
            embedder=svc.embedder,
            user_id=user_id,
            skip_vllm_check=False,
        )
        logger.info("[Upload] Ingestion complete: %s → %s", file_name, document_id)
    except Exception as e:
        logger.error("[Upload] Ingestion failed for %s: %s", file_name, e, exc_info=True)
        try:
            svc.rbac.upsert_document(dept_id, file_name, file_path,
                                     {"ocr_status": "failed"}, user_id=user_id)
        except Exception as inner:
            logger.error("[Upload] Failed to mark %s as failed in DB: %s", file_name, inner)


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Virchow — Knowledge Assistant</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: rgba(255,255,255,0.08);
    --primary: #3b82f6; --primary-dim: rgba(59,130,246,0.15);
    --text: #f1f5f9; --muted: #64748b; --success: #10b981; --error: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui,sans-serif; background: var(--bg); color: var(--text);
         height: 100vh; overflow: hidden; }
  #app { display: flex; height: 100vh; }

  /* ── Auth overlay ── */
  #auth-overlay {
    position: fixed; inset: 0; background: var(--bg);
    display: flex; align-items: center; justify-content: center; z-index: 999;
  }
  .auth-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 1rem; padding: 2.5rem; width: 380px;
  }
  .auth-card h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 0.25rem; }
  .auth-card p  { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.75rem; }
  .field { margin-bottom: 1rem; }
  .field label { display: block; font-size: 0.75rem; color: var(--muted);
                 text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.4rem; }
  .field input, .field select {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 0.5rem; padding: 0.65rem 0.85rem; color: var(--text);
    font-size: 0.95rem; outline: none;
  }
  .field input:focus, .field select:focus { border-color: var(--primary); }
  .btn {
    width: 100%; padding: 0.75rem; border: none; border-radius: 0.5rem;
    background: var(--primary); color: #fff; font-size: 1rem; font-weight: 600;
    cursor: pointer; transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .tab-row { display: flex; gap: 0.5rem; margin-bottom: 1.75rem; }
  .tab {
    flex: 1; padding: 0.5rem; border: 1px solid var(--border); border-radius: 0.5rem;
    background: none; color: var(--muted); cursor: pointer; font-size: 0.9rem;
  }
  .tab.active { background: var(--primary-dim); color: var(--primary); border-color: var(--primary); }
  .auth-msg { margin-top: 1rem; font-size: 0.85rem; text-align: center; min-height: 1.2em; }

  /* ── Sidebar ── */
  aside {
    width: 240px; background: var(--surface); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; flex-shrink: 0;
  }
  .sidebar-header {
    padding: 1.25rem 1rem; border-bottom: 1px solid var(--border);
    font-weight: 700; font-size: 1.1rem;
    background: linear-gradient(to right, #60a5fa, #a78bfa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .new-chat-btn {
    margin: 0.75rem; padding: 0.6rem; border: 1px dashed var(--border);
    border-radius: 0.5rem; background: none; color: var(--muted); cursor: pointer;
    font-size: 0.85rem; text-align: left;
  }
  .new-chat-btn:hover { border-color: var(--primary); color: var(--primary); }
  #chat-list { flex: 1; overflow-y: auto; padding: 0 0.5rem; }
  .chat-item {
    padding: 0.6rem 0.75rem; border-radius: 0.5rem; cursor: pointer;
    font-size: 0.85rem; color: var(--muted); white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; margin-bottom: 0.25rem;
  }
  .chat-item:hover, .chat-item.active { background: var(--primary-dim); color: var(--text); }
  .sidebar-footer {
    padding: 1rem; border-top: 1px solid var(--border); font-size: 0.8rem; color: var(--muted);
  }
  .logout-btn {
    margin-top: 0.5rem; background: none; border: 1px solid var(--border);
    border-radius: 0.4rem; color: var(--muted); padding: 0.35rem 0.75rem;
    cursor: pointer; font-size: 0.8rem;
  }
  .logout-btn:hover { color: var(--error); border-color: var(--error); }

  /* ── Main chat area ── */
  main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #messages {
    flex: 1; overflow-y: auto; padding: 1.5rem;
    display: flex; flex-direction: column; gap: 1rem;
  }
  .bubble {
    max-width: 720px; padding: 0.9rem 1.1rem; border-radius: 1rem;
    line-height: 1.65; font-size: 0.95rem; white-space: pre-wrap;
  }
  .bubble.user { align-self: flex-end; background: var(--primary); color: #fff;
                  border-bottom-right-radius: 4px; }
  .bubble.bot  { align-self: flex-start; background: var(--surface);
                  border-bottom-left-radius: 4px; }
  .bubble .sources {
    margin-top: 0.6rem; padding-top: 0.5rem; border-top: 1px solid rgba(255,255,255,0.1);
    font-size: 0.75rem; color: #93c5fd;
  }
  .typing-dots { display: flex; gap: 5px; padding: 0.9rem 1.1rem;
                  background: var(--surface); border-radius: 1rem; width: fit-content; }
  .dot { width: 7px; height: 7px; background: var(--muted); border-radius: 50%;
          animation: bounce 1.3s infinite ease-in-out; }
  .dot:nth-child(2) { animation-delay: 0.15s; }
  .dot:nth-child(3) { animation-delay: 0.3s; }
  @keyframes bounce { 0%,80%,100% { transform: scale(0.6); } 40% { transform: scale(1); } }

  #input-area {
    padding: 1rem 1.5rem; border-top: 1px solid var(--border);
    display: flex; gap: 0.75rem; align-items: flex-end;
  }
  #msg-input {
    flex: 1; background: var(--surface); border: 1px solid var(--border);
    border-radius: 0.75rem; padding: 0.75rem 1rem; color: var(--text);
    font-size: 0.95rem; font-family: inherit; resize: none; outline: none;
    max-height: 160px;
  }
  #msg-input:focus { border-color: var(--primary); }
  #send-btn {
    width: 42px; height: 42px; border-radius: 0.65rem; border: none;
    background: var(--primary); color: #fff; cursor: pointer; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
  }
  #send-btn:disabled { opacity: 0.5; cursor: default; }

  .empty-state {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: var(--muted);
    gap: 0.5rem;
  }
  .empty-state h2 { font-size: 1.4rem; color: var(--text); }
</style>
</head>
<body>

<!-- Auth overlay -->
<div id="auth-overlay">
  <div class="auth-card">
    <h1>Virchow</h1>
    <p>Knowledge Assistant — sign in to continue</p>

    <div class="tab-row">
      <button class="tab active" onclick="showTab('login')">Sign In</button>
      <button class="tab" onclick="showTab('register')">Register</button>
    </div>

    <!-- Login form -->
    <div id="login-form">
      <div class="field"><label>Email</label><input id="l-email" type="email" placeholder="you@example.com"></div>
      <div class="field"><label>Password</label><input id="l-pass" type="password" placeholder="••••••••"></div>
      <button class="btn" onclick="doLogin()">Sign In</button>
    </div>

    <!-- Register form -->
    <div id="register-form" style="display:none">
      <div class="field"><label>Full name</label><input id="r-name" type="text" placeholder="Jane Doe"></div>
      <div class="field"><label>Email</label><input id="r-email" type="email" placeholder="you@example.com"></div>
      <div class="field"><label>Password</label><input id="r-pass" type="password" placeholder="Min 8 characters"></div>
      <div class="field"><label>Department</label>
        <select id="r-dept"><option value="">Loading…</option></select>
      </div>
      <button class="btn" onclick="doRegister()">Create Account</button>
    </div>

    <p class="auth-msg" id="auth-msg"></p>
  </div>
</div>

<!-- Main app -->
<div id="app" style="display:none">
  <aside>
    <div class="sidebar-header">Virchow</div>
    <button class="new-chat-btn" onclick="newChat()">+ New Chat</button>
    <div id="chat-list"></div>
    <div class="sidebar-footer">
      <div id="user-label"></div>
      <button class="logout-btn" onclick="logout()">Sign out</button>
    </div>
  </aside>

  <main>
    <div id="messages">
      <div class="empty-state">
        <h2>How can I help you?</h2>
        <p>Ask anything about your knowledge base.</p>
      </div>
    </div>

    <div id="input-area">
      <textarea id="msg-input" rows="1" placeholder="Ask a question…" maxlength="2000"></textarea>
      <button id="send-btn" onclick="sendMessage()">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>
  </main>
</div>

<script>
  // B-M-Frontend: token stored in sessionStorage (cleared on tab close) instead of localStorage
  let token = sessionStorage.getItem('vk_token');
  let session = JSON.parse(sessionStorage.getItem('vk_session') || 'null');
  let currentChatId = null;
  let busy = false;

  // ── Boot ──────────────────────────────────────────────────────────────────
  window.onload = async () => {
    if (token && session) {
      await loadDepts();
      enterApp();
    } else {
      await loadDepts();
    }
    document.getElementById('msg-input').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
  };

  async function loadDepts() {
    if (!token) return; // departments endpoint now requires auth
    try {
      const r = await authFetch('/auth/departments');
      if (!r.ok) return;
      const depts = await r.json();
      const sel = document.getElementById('r-dept');
      if (depts.length === 0) {
        sel.innerHTML = '<option value="">Default (auto-created)</option>';
      } else {
        sel.innerHTML = depts.map(d =>
          `<option value="${escHtml(d.id)}">${escHtml(d.name)}</option>`
        ).join('');
      }
    } catch(e) { /* ignore — depts list is optional */ }
  }

  // ── Auth tabs ─────────────────────────────────────────────────────────────
  function showTab(t) {
    document.querySelectorAll('.tab').forEach((b,i) => b.classList.toggle('active', (i===0) === (t==='login')));
    document.getElementById('login-form').style.display    = t === 'login'    ? '' : 'none';
    document.getElementById('register-form').style.display = t === 'register' ? '' : 'none';
    document.getElementById('auth-msg').textContent = '';
  }

  function setAuthMsg(txt, ok) {
    const el = document.getElementById('auth-msg');
    el.textContent = txt;
    el.style.color = ok ? 'var(--success)' : 'var(--error)';
  }

  async function doLogin() {
    const email = document.getElementById('l-email').value.trim();
    const pass  = document.getElementById('l-pass').value;
    if (!email || !pass) return setAuthMsg('Fill in all fields', false);
    try {
      const r = await fetch('/auth/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, password: pass})
      });
      const d = await r.json();
      if (!r.ok) return setAuthMsg(d.detail || 'Login failed', false);
      await saveSession(d);
    } catch(e) { setAuthMsg('Network error', false); }
  }

  async function doRegister() {
    const name  = document.getElementById('r-name').value.trim();
    const email = document.getElementById('r-email').value.trim();
    const pass  = document.getElementById('r-pass').value;
    const dept  = document.getElementById('r-dept').value;
    if (!name || !email || !pass) return setAuthMsg('Fill in all fields', false);
    if (pass.length < 8) return setAuthMsg('Password must be at least 8 characters', false);
    try {
      const r = await fetch('/auth/register', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, email, password: pass, department_id: dept || null})
      });
      const d = await r.json();
      if (!r.ok) return setAuthMsg(d.detail || 'Registration failed', false);
      await saveSession(d);
    } catch(e) { setAuthMsg('Network error', false); }
  }

  async function saveSession(d) {
    token = d.token;
    session = {user_id: d.user_id, dept_id: d.dept_id, name: d.name};
    sessionStorage.setItem('vk_token', token);
    sessionStorage.setItem('vk_session', JSON.stringify(session));
    await loadDepts();
    enterApp();
  }

  function enterApp() {
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    document.getElementById('user-label').textContent = session.name;
    loadChats();
  }

  function logout() {
    sessionStorage.removeItem('vk_token');
    sessionStorage.removeItem('vk_session');
    location.reload();
  }

  // ── Chats ─────────────────────────────────────────────────────────────────
  async function loadChats() {
    try {
      const r = await authFetch('/chats');
      if (!r.ok) return;
      const chats = await r.json();
      const list = document.getElementById('chat-list');
      list.innerHTML = chats.map(c =>
        `<div class="chat-item" data-id="${escHtml(c.id)}" onclick="openChat('${escHtml(c.id)}')">${escHtml(c.title || 'Untitled')}</div>`
      ).join('');
    } catch(e) { /* ignore */ }
  }

  function newChat() {
    currentChatId = null;
    document.getElementById('messages').innerHTML = `
      <div class="empty-state">
        <h2>How can I help you?</h2>
        <p>Ask anything about your knowledge base.</p>
      </div>`;
    document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
  }

  async function openChat(id) {
    currentChatId = id;
    document.querySelectorAll('.chat-item').forEach(el => {
      el.classList.toggle('active', el.dataset.id === id);
    });
    try {
      const r = await authFetch(`/chats/${encodeURIComponent(id)}/messages`);
      if (!r.ok) { appendBubble('bot', 'Failed to load chat history.'); return; }
      const msgs = await r.json();
      const box = document.getElementById('messages');
      box.innerHTML = '';
      msgs.forEach(m => appendBubble(m.role === 'user' ? 'user' : 'bot', m.content));
      scrollBottom();
    } catch(e) { appendBubble('bot', 'Network error loading chat.'); }
  }

  // ── Messaging ─────────────────────────────────────────────────────────────
  async function sendMessage() {
    const input = document.getElementById('msg-input');
    const text  = input.value.trim();
    if (!text || busy) return;
    if (text.length > 2000) { alert('Question is too long (max 2000 characters).'); return; }

    const box = document.getElementById('messages');
    if (box.querySelector('.empty-state')) box.innerHTML = '';

    appendBubble('user', text);
    input.value = '';
    input.style.height = '';

    const dots = document.createElement('div');
    dots.className = 'typing-dots';
    dots.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
    box.appendChild(dots);
    scrollBottom();

    busy = true;
    document.getElementById('send-btn').disabled = true;

    try {
      const fd = new FormData();
      fd.append('question', text);
      if (currentChatId) fd.append('chat_id', currentChatId);

      const r = await authFetch('/query', {method: 'POST', body: fd});
      const d = await r.json();
      dots.remove();

      if (!r.ok) throw new Error(d.detail || 'Query failed');

      currentChatId = d.chat_id;
      appendBubble('bot', d.answer, d.citations || []);
      loadChats();
    } catch(e) {
      dots.remove();
      appendBubble('bot', 'Sorry, something went wrong. Please try again.');
      console.error('sendMessage error:', e);
    } finally {
      busy = false;
      document.getElementById('send-btn').disabled = false;
      scrollBottom();
    }
  }

  function appendBubble(role, text, sources) {
    const b = document.createElement('div');
    b.className = `bubble ${role}`;
    b.textContent = text;
    if (sources && sources.length) {
      const s = document.createElement('div');
      s.className = 'sources';
      s.textContent = 'Sources: ' + sources.map(c => c.name || c).join(', ');
      b.appendChild(s);
    }
    document.getElementById('messages').appendChild(b);
  }

  function scrollBottom() {
    const box = document.getElementById('messages');
    box.scrollTop = box.scrollHeight;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function authFetch(url, opts = {}) {
    return fetch(url, {
      ...opts,
      headers: {...(opts.headers || {}), 'Authorization': 'Bearer ' + token}
    });
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Auto-resize textarea
  document.addEventListener('DOMContentLoaded', () => {
    const ta = document.getElementById('msg-input');
    if (ta) ta.addEventListener('input', () => {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
    });
  });
</script>
</body>
</html>"""
