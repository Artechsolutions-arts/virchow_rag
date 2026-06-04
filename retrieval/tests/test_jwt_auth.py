"""
Tests for src/auth/jwt_auth.py

Covers: require_admin dependency, create_token role encoding, decode_token.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from src.auth.jwt_auth import (
    create_token,
    decode_token,
    require_admin,
    hash_password,
    verify_password,
)


# ── require_admin ───────────────────────────────────────────────────────────

def test_require_admin_passes_for_admin():
    user = {"sub": "u1", "email": "admin@test.com", "dept_id": "d1", "role": "admin"}
    result = require_admin(user=user)
    assert result == user


def test_require_admin_rejects_non_admin():
    user = {"sub": "u2", "email": "user@test.com", "dept_id": "d1", "role": "user"}
    with pytest.raises(HTTPException) as exc_info:
        require_admin(user=user)
    assert exc_info.value.status_code == 403


def test_require_admin_rejects_missing_role():
    user = {"sub": "u3", "email": "anon@test.com", "dept_id": "d1"}
    with pytest.raises(HTTPException) as exc_info:
        require_admin(user=user)
    assert exc_info.value.status_code == 403


def test_require_admin_rejects_hod_role():
    user = {"sub": "u4", "email": "hod@test.com", "dept_id": "d1", "role": "hod"}
    with pytest.raises(HTTPException):
        require_admin(user=user)


# ── create_token / decode_token ─────────────────────────────────────────────

def test_create_token_encodes_role():
    token = create_token("u1", "test@test.com", "dept-1", role="admin")
    payload = decode_token(token)
    assert payload["role"] == "admin"


def test_create_token_default_role_is_user():
    token = create_token("u1", "test@test.com", "dept-1")
    payload = decode_token(token)
    assert payload["role"] == "user"


def test_decode_token_invalid_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        decode_token("invalid.token.here")
    assert exc_info.value.status_code == 401


# ── hash_password / verify_password ────────────────────────────────────────

def test_hash_and_verify_password():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed)
    assert not verify_password("wrongpassword", hashed)
