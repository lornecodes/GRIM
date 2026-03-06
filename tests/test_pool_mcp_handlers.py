"""
Handler tests for Pool MCP server — uses a test SQLite DB.

Read handlers test against real SQLite data.
Write handlers mock the HTTP proxy.

Run:
    PYTHONPATH=mcp/pool/src python -m pytest tests/test_pool_mcp_handlers.py -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Bootstrap
grim_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(grim_root / "mcp" / "pool" / "src"))

from pool_mcp.db import PoolDB

# ── Test fixtures ────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 2,
    workspace_id TEXT,
    instructions TEXT NOT NULL,
    plan TEXT,
    kronos_domains TEXT,
    kronos_fdo_ids TEXT,
    assigned_slot TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    clarification_question TEXT,
    clarification_answer TEXT,
    result TEXT,
    error TEXT,
    transcript TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _make_test_db(tmp_path: Path) -> str:
    """Create a test SQLite DB with sample jobs."""
    db_path = str(tmp_path / "pool_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE)

    # Insert sample jobs
    jobs = [
        ("job-001", "code", "complete", 2, "ws-001", "Write tests",
         None, None, None, "slot-0", 0, 2, None, None,
         "All tests pass", None,
         json.dumps([
             {"seq": 1, "type": "text", "text": "Starting..."},
             {"seq": 2, "type": "tool_use", "text": "Edit file.py"},
             {"seq": 3, "type": "text", "text": "Done!"},
         ]),
         "2026-03-05T10:00:00", "2026-03-05T10:05:00"),
        ("job-002", "research", "running", 1, None, "Investigate PAC",
         None, '["physics"]', '["pac-comprehensive"]', "slot-1", 0, 2,
         None, None, None, None,
         json.dumps([{"seq": 1, "type": "text", "text": "Searching vault..."}]),
         "2026-03-05T10:10:00", "2026-03-05T10:12:00"),
        ("job-003", "code", "queued", 3, None, "Fix bug #42",
         None, None, None, None, 0, 2, None, None, None, None, None,
         "2026-03-05T10:15:00", "2026-03-05T10:15:00"),
        ("job-004", "audit", "failed", 2, "ws-004", "Security review",
         None, None, None, "slot-2", 1, 2, None, None, None,
         "Agent crashed: OOM", None,
         "2026-03-05T09:00:00", "2026-03-05T09:30:00"),
        ("job-005", "code", "blocked", 2, None, "Add feature X",
         None, None, None, "slot-0", 0, 2,
         "Which auth method: JWT or session?", None, None, None, None,
         "2026-03-05T10:20:00", "2026-03-05T10:22:00"),
    ]
    conn.executemany(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        jobs,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def test_db(tmp_path):
    db_path = _make_test_db(tmp_path)
    return PoolDB(db_path)


@pytest.fixture
def handlers_with_db(test_db, monkeypatch):
    """Patch pool_db in server module to use test DB."""
    import pool_mcp.server as srv
    monkeypatch.setattr(srv, "pool_db", test_db)
    monkeypatch.setattr(srv, "_engines_initialized", True)
    return srv


# ── PoolDB unit tests ────────────────────────────────────────────────────────


class TestPoolDB:
    def test_list_jobs_all(self, test_db):
        jobs = test_db.list_jobs()
        assert len(jobs) == 5

    def test_list_jobs_by_status(self, test_db):
        jobs = test_db.list_jobs(status="queued")
        assert len(jobs) == 1
        assert jobs[0]["id"] == "job-003"

    def test_list_jobs_by_type(self, test_db):
        jobs = test_db.list_jobs(job_type="code")
        assert len(jobs) == 3

    def test_list_jobs_limit(self, test_db):
        jobs = test_db.list_jobs(limit=2)
        assert len(jobs) == 2

    def test_list_jobs_since(self, test_db):
        jobs = test_db.list_jobs(since="2026-03-05T10:10:00")
        assert all(j["created_at"] >= "2026-03-05T10:10:00" for j in jobs)

    def test_list_jobs_no_transcript_in_list(self, test_db):
        jobs = test_db.list_jobs()
        for j in jobs:
            assert "transcript" not in j
            assert "transcript_lines" in j

    def test_get_job_found(self, test_db):
        job = test_db.get_job("job-001")
        assert job is not None
        assert job["id"] == "job-001"
        assert job["job_type"] == "code"
        assert job["status"] == "complete"
        assert isinstance(job["transcript"], list)
        assert len(job["transcript"]) == 3

    def test_get_job_not_found(self, test_db):
        assert test_db.get_job("nonexistent") is None

    def test_get_transcript(self, test_db):
        result = test_db.get_transcript("job-001")
        assert result["total"] == 3
        assert len(result["lines"]) == 3
        assert result["has_more"] is False

    def test_get_transcript_pagination(self, test_db):
        result = test_db.get_transcript("job-001", offset=1, limit=1)
        assert result["total"] == 3
        assert len(result["lines"]) == 1
        assert result["offset"] == 1
        assert result["has_more"] is True
        assert result["lines"][0]["type"] == "tool_use"

    def test_get_transcript_empty(self, test_db):
        result = test_db.get_transcript("job-003")
        assert result["total"] == 0
        assert result["lines"] == []

    def test_get_transcript_not_found(self, test_db):
        result = test_db.get_transcript("nonexistent")
        assert "error" in result

    def test_get_stats(self, test_db):
        stats = test_db.get_stats()
        assert stats["total_jobs"] == 5
        assert stats["queued"] == 1
        assert stats["running"] == 1
        assert stats["complete"] == 1
        assert stats["failed"] == 1
        assert stats["blocked"] == 1

    def test_get_metrics(self, test_db):
        metrics = test_db.get_metrics()
        assert metrics["total_finished"] == 2  # complete + failed
        assert metrics["completed"] == 1
        assert metrics["failed"] == 1
        assert metrics["completion_rate"] == 0.5
        assert "code" in metrics["by_type"]

    def test_json_fields_parsed(self, test_db):
        job = test_db.get_job("job-002")
        assert job["kronos_domains"] == ["physics"]
        assert job["kronos_fdo_ids"] == ["pac-comprehensive"]

    def test_close(self, test_db):
        test_db.list_jobs()  # force connection
        test_db.close()
        # Should be able to reconnect
        jobs = test_db.list_jobs()
        assert len(jobs) == 5


# ── Read handler tests ───────────────────────────────────────────────────────


class TestReadHandlers:
    def test_pool_status(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_status({}))
        assert result["total_jobs"] == 5
        assert result["running"] == 1

    def test_pool_list_jobs(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_list_jobs({}))
        assert result["count"] == 5

    def test_pool_list_jobs_filtered(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_list_jobs(
            {"status": "running"}
        ))
        assert result["count"] == 1
        assert result["jobs"][0]["id"] == "job-002"

    def test_pool_list_jobs_limit(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_list_jobs({"limit": 2}))
        assert result["count"] == 2

    def test_pool_job_detail(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_detail(
            {"job_id": "job-001"}
        ))
        assert result["id"] == "job-001"
        assert isinstance(result["transcript"], list)

    def test_pool_job_detail_missing_id(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_detail({}))
        assert "error" in result

    def test_pool_job_detail_not_found(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_detail(
            {"job_id": "nope"}
        ))
        assert "error" in result

    def test_pool_job_logs(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_logs(
            {"job_id": "job-001"}
        ))
        assert result["total"] == 3
        assert len(result["lines"]) == 3

    def test_pool_job_logs_pagination(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_logs(
            {"job_id": "job-001", "offset": 2, "limit": 10}
        ))
        assert len(result["lines"]) == 1
        assert result["has_more"] is False

    def test_pool_job_logs_missing_id(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_logs({}))
        assert "error" in result

    def test_pool_metrics(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_metrics({}))
        assert result["completion_rate"] == 0.5


# ── Write handler tests (mocked HTTP) ───────────────────────────────────────


class TestWriteHandlers:
    def test_pool_submit(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"job_id": "job-new", "status": "queued"}):
            result = json.loads(handlers_with_db.handle_pool_submit(
                {"job_type": "code", "instructions": "Write tests"}
            ))
            assert result["job_id"] == "job-new"

    def test_pool_submit_missing_fields(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_submit({}))
        assert "error" in result

    def test_pool_submit_missing_instructions(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_submit(
            {"job_type": "code"}
        ))
        assert "error" in result

    def test_pool_cancel(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"cancelled": "job-003"}):
            result = json.loads(handlers_with_db.handle_pool_cancel(
                {"job_id": "job-003"}
            ))
            assert result["cancelled"] == "job-003"

    def test_pool_cancel_missing_id(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_cancel({}))
        assert "error" in result

    def test_pool_clarify(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"unblocked": "job-005"}):
            result = json.loads(handlers_with_db.handle_pool_clarify(
                {"job_id": "job-005", "answer": "Use JWT"}
            ))
            assert result["unblocked"] == "job-005"

    def test_pool_clarify_missing_answer(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_clarify(
            {"job_id": "job-005"}
        ))
        assert "error" in result

    def test_pool_retry(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"retried": "job-004"}):
            result = json.loads(handlers_with_db.handle_pool_retry(
                {"job_id": "job-004"}
            ))
            assert result["retried"] == "job-004"

    def test_pool_retry_missing_id(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_retry({}))
        assert "error" in result

    def test_pool_review_approve(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"reviewed": "job-001", "action": "approve"}):
            result = json.loads(handlers_with_db.handle_pool_review(
                {"job_id": "job-001", "action": "approve"}
            ))
            assert result["action"] == "approve"

    def test_pool_review_reject(self, handlers_with_db):
        with patch.object(handlers_with_db, "_http_post",
                          return_value={"reviewed": "job-001", "action": "reject"}):
            result = json.loads(handlers_with_db.handle_pool_review(
                {"job_id": "job-001", "action": "reject"}
            ))
            assert result["action"] == "reject"

    def test_pool_review_invalid_action(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_review(
            {"job_id": "job-001", "action": "explode"}
        ))
        assert "error" in result

    def test_pool_review_missing_id(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_review({}))
        assert "error" in result


# ── Null safety ──────────────────────────────────────────────────────────────


class TestNullSafety:
    def test_null_job_id_detail(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_detail(
            {"job_id": None}
        ))
        assert "error" in result

    def test_null_job_id_logs(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_job_logs(
            {"job_id": None}
        ))
        assert "error" in result

    def test_null_submit_fields(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_submit(
            {"job_type": None, "instructions": None}
        ))
        assert "error" in result

    def test_null_cancel(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_cancel(
            {"job_id": None}
        ))
        assert "error" in result

    def test_null_review(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_review(
            {"job_id": None, "action": None}
        ))
        assert "error" in result

    def test_null_clarify(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_clarify(
            {"job_id": None, "answer": None}
        ))
        assert "error" in result


# ── DB edge cases ────────────────────────────────────────────────────────────


class TestDBEdgeCases:
    def test_db_not_found(self, tmp_path):
        db = PoolDB(str(tmp_path / "nonexistent.db"))
        with pytest.raises(FileNotFoundError):
            db.list_jobs()

    def test_empty_db(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_CREATE_TABLE)
        conn.commit()
        conn.close()

        db = PoolDB(db_path)
        assert db.list_jobs() == []
        stats = db.get_stats()
        assert stats["total_jobs"] == 0
        metrics = db.get_metrics()
        assert metrics["completion_rate"] == 0


# ── Skill handler tests ─────────────────────────────────────────────────────


class TestSkillHandlers:
    def test_pool_skills_lists_skills(self, handlers_with_db, monkeypatch):
        from pool_mcp.skills import SkillsEngine
        grim_root = Path(__file__).resolve().parent.parent
        engine = SkillsEngine(str(grim_root / "mcp" / "pool" / "skills"))
        monkeypatch.setattr(handlers_with_db, "skills_engine", engine)

        result = json.loads(handlers_with_db.handle_pool_skills({}))
        assert "skills" in result
        names = {s["name"] for s in result["skills"]}
        assert "pool-manage" in names
        assert "agent-monitor" in names
        assert "workspace-manage" in names

    def test_pool_skill_load_found(self, handlers_with_db, monkeypatch):
        from pool_mcp.skills import SkillsEngine
        grim_root = Path(__file__).resolve().parent.parent
        engine = SkillsEngine(str(grim_root / "mcp" / "pool" / "skills"))
        monkeypatch.setattr(handlers_with_db, "skills_engine", engine)

        result = json.loads(handlers_with_db.handle_pool_skill_load(
            {"name": "pool-manage"}
        ))
        assert result["name"] == "pool-manage"
        assert "protocol" in result
        assert len(result["protocol"]) > 100
        assert "phases" in result

    def test_pool_skill_load_not_found(self, handlers_with_db, monkeypatch):
        from pool_mcp.skills import SkillsEngine
        grim_root = Path(__file__).resolve().parent.parent
        engine = SkillsEngine(str(grim_root / "mcp" / "pool" / "skills"))
        monkeypatch.setattr(handlers_with_db, "skills_engine", engine)

        result = json.loads(handlers_with_db.handle_pool_skill_load(
            {"name": "nonexistent"}
        ))
        assert "error" in result
        assert "available" in result

    def test_pool_skill_load_missing_name(self, handlers_with_db):
        result = json.loads(handlers_with_db.handle_pool_skill_load({}))
        assert "error" in result

    def test_pool_skills_no_engine(self, handlers_with_db, monkeypatch):
        monkeypatch.setattr(handlers_with_db, "skills_engine", None)
        result = json.loads(handlers_with_db.handle_pool_skills({}))
        assert "error" in result
