"""
Tests for scripts/backfill.py — focuses on _process_batch logic.
No real DB or vLLM required; all I/O is mocked.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Make project root and scripts dir importable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the function under test directly from the script module
import importlib.util

_SCRIPT_PATH = os.path.join(_PROJECT_ROOT, "scripts", "backfill.py")
_spec = importlib.util.spec_from_file_location("backfill", _SCRIPT_PATH)
_backfill_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backfill_mod)

_process_batch = _backfill_mod._process_batch


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_doc(file_name="INV-24-25-001.pdf", status="pending",
              file_path="uuid/INV-24-25-001.pdf", dept_id="dept-1"):
    return {
        "id": "doc-id-1",
        "file_name": file_name,
        "file_path": file_path,
        "department_id": dept_id,
        "ocr_status": status,
    }


def _mock_db():
    return MagicMock()


def _mock_embedder():
    return MagicMock()


# ── dry_run ───────────────────────────────────────────────────────────────────

def test_dry_run_counts_as_success_without_ingesting():
    db = _mock_db()
    embedder = _mock_embedder()

    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        n_ok, n_skip, n_err, failed = _process_batch(
            [_make_doc()], db, embedder, dry_run=True, force=False
        )

    mock_ingest.assert_not_called()
    assert n_ok == 1
    assert n_skip == 0
    assert n_err == 0
    assert failed == []


# ── skip logic ────────────────────────────────────────────────────────────────

def test_completed_doc_skipped_without_force():
    doc = _make_doc(status="completed")
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    mock_ingest.assert_not_called()
    assert n_skip == 1
    assert n_ok == 0


def test_completed_doc_processed_with_force():
    doc = _make_doc(status="completed")
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        mock_ingest.return_value = "doc-id-1"
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=True
        )

    mock_ingest.assert_called_once()
    assert n_ok == 1
    assert n_skip == 0


def test_doc_without_file_path_skipped():
    doc = _make_doc(file_path=None)
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    mock_ingest.assert_not_called()
    assert n_skip == 1


def test_doc_with_empty_file_path_skipped():
    doc = _make_doc(file_path="")
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    mock_ingest.assert_not_called()
    assert n_skip == 1


# ── success / error paths ─────────────────────────────────────────────────────

def test_successful_ingestion_counted():
    doc = _make_doc(status="pending")
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        mock_ingest.return_value = "doc-id-1"
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    assert n_ok == 1
    assert n_err == 0
    assert failed == []


def test_ingestion_exception_counted_as_error():
    doc = _make_doc(status="pending")
    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        mock_ingest.side_effect = RuntimeError("OCR server timed out")
        n_ok, n_skip, n_err, failed = _process_batch(
            [doc], _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    assert n_err == 1
    assert n_ok == 0
    assert doc["file_name"] in failed


def test_one_failure_does_not_stop_rest_of_batch():
    docs = [
        _make_doc(file_name="FAIL.pdf",    status="pending"),
        _make_doc(file_name="OK.pdf",      status="pending", file_path="uuid/OK.pdf"),
    ]
    call_count = {"n": 0}

    def _side_effect(**kwargs):
        call_count["n"] += 1
        if kwargs.get("file_name") == "FAIL.pdf":
            raise RuntimeError("simulated failure")
        return "doc-id"

    with patch.object(_backfill_mod, "ingest_from_seaweedfs", side_effect=_side_effect):
        n_ok, n_skip, n_err, failed = _process_batch(
            docs, _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    assert n_err == 1
    assert n_ok == 1
    assert "FAIL.pdf" in failed


# ── mixed batch ───────────────────────────────────────────────────────────────

def test_mixed_batch_counts():
    docs = [
        _make_doc(file_name="DONE.pdf",    status="completed"),    # skipped
        _make_doc(file_name="PENDING.pdf", status="pending",       # success
                  file_path="uuid/PENDING.pdf"),
        _make_doc(file_name="NOPATH.pdf",  status="pending",       # skipped
                  file_path=""),
    ]

    with patch.object(_backfill_mod, "ingest_from_seaweedfs") as mock_ingest:
        mock_ingest.return_value = "doc-id"
        n_ok, n_skip, n_err, failed = _process_batch(
            docs, _mock_db(), _mock_embedder(), dry_run=False, force=False
        )

    assert n_ok == 1
    assert n_skip == 2
    assert n_err == 0
