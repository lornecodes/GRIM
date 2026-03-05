"""Unit tests for the execution pool — models, queue, slot, pool.

Uses real SQLite (temp files), mocked ClaudeSDKClient for agent execution.
No live API calls.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.pool.models import (
    ClarificationNeeded,
    Job,
    JobPriority,
    JobResult,
    JobStatus,
    JobType,
    PRIORITY_ORDER,
    TERMINAL_STATUSES,
)
from core.pool.queue import JobQueue


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "test_pool.db"


@pytest.fixture
async def queue(tmp_db) -> JobQueue:
    q = JobQueue(tmp_db)
    await q.initialize()
    return q


def _make_job(**kwargs) -> Job:
    defaults = dict(job_type=JobType.CODE, instructions="do something")
    defaults.update(kwargs)
    return Job(**defaults)


# ── Job model tests ──────────────────────────────────────────────

class TestJobModel:
    """Test Job Pydantic model."""

    def test_id_auto_generated(self):
        j1 = _make_job()
        j2 = _make_job()
        assert j1.id != j2.id
        assert j1.id.startswith("job-")

    def test_id_custom(self):
        job = _make_job(id="job-custom99")
        assert job.id == "job-custom99"

    def test_timestamps_auto(self):
        job = _make_job()
        assert job.created_at.tzinfo is not None
        assert job.updated_at.tzinfo is not None

    def test_default_status(self):
        job = _make_job()
        assert job.status == JobStatus.QUEUED

    def test_serialization(self):
        job = _make_job(
            kronos_domains=["physics", "ai-systems"],
            kronos_fdo_ids=["pac-comprehensive"],
        )
        d = job.model_dump(mode="json")
        assert d["job_type"] == "code"
        assert d["kronos_domains"] == ["physics", "ai-systems"]
        # Round-trip
        job2 = Job.model_validate(d)
        assert job2.id == job.id

    def test_job_types(self):
        for jt in JobType:
            job = _make_job(job_type=jt)
            assert job.job_type == jt

    def test_priority_order_complete(self):
        for jp in JobPriority:
            assert jp in PRIORITY_ORDER


class TestJobResult:
    def test_success(self):
        r = JobResult(job_id="j1", success=True, result="done", cost_usd=0.05, num_turns=3)
        assert r.success
        assert r.cost_usd == 0.05

    def test_failure(self):
        r = JobResult(job_id="j1", success=False, error="timeout")
        assert not r.success
        assert r.error == "timeout"


# ── JobQueue tests ───────────────────────────────────────────────

class TestJobQueue:
    """Test SQLite-backed JobQueue."""

    @pytest.mark.asyncio
    async def test_initialize_creates_db(self, tmp_db):
        q = JobQueue(tmp_db)
        await q.initialize()
        assert tmp_db.exists()

    @pytest.mark.asyncio
    async def test_initialize_creates_parent_dirs(self, tmp_path):
        db = tmp_path / "nested" / "dir" / "pool.db"
        q = JobQueue(db)
        await q.initialize()
        assert db.exists()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, tmp_db):
        q = JobQueue(tmp_db)
        await q.initialize()
        await q.initialize()  # should not fail

    @pytest.mark.asyncio
    async def test_submit_returns_id(self, queue):
        job = _make_job()
        job_id = await queue.submit(job)
        assert job_id == job.id

    @pytest.mark.asyncio
    async def test_get_after_submit(self, queue):
        job = _make_job(instructions="test instruction")
        await queue.submit(job)
        fetched = await queue.get(job.id)
        assert fetched is not None
        assert fetched.instructions == "test instruction"
        assert fetched.status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, queue):
        result = await queue.get("job-nonexist")
        assert result is None

    @pytest.mark.asyncio
    async def test_next_returns_queued_job(self, queue):
        job = _make_job()
        await queue.submit(job)
        pulled = await queue.next()
        assert pulled is not None
        assert pulled.id == job.id
        assert pulled.status == JobStatus.ASSIGNED

    @pytest.mark.asyncio
    async def test_next_empty_queue(self, queue):
        result = await queue.next()
        assert result is None

    @pytest.mark.asyncio
    async def test_next_skips_assigned(self, queue):
        j1 = _make_job()
        j2 = _make_job()
        await queue.submit(j1)
        await queue.submit(j2)

        pulled1 = await queue.next()
        assert pulled1.id == j1.id

        pulled2 = await queue.next()
        assert pulled2.id == j2.id

        pulled3 = await queue.next()
        assert pulled3 is None

    @pytest.mark.asyncio
    async def test_next_priority_order(self, queue):
        low = _make_job(priority=JobPriority.LOW)
        critical = _make_job(priority=JobPriority.CRITICAL)
        normal = _make_job(priority=JobPriority.NORMAL)

        # Submit in wrong order
        await queue.submit(low)
        await queue.submit(normal)
        await queue.submit(critical)

        p1 = await queue.next()
        assert p1.priority == JobPriority.CRITICAL

        p2 = await queue.next()
        assert p2.priority == JobPriority.NORMAL

        p3 = await queue.next()
        assert p3.priority == JobPriority.LOW

    @pytest.mark.asyncio
    async def test_next_workspace_aware(self, queue):
        j1 = _make_job(workspace_id="ws-grim")
        j2 = _make_job(workspace_id="ws-fracton")
        j3 = _make_job(workspace_id="ws-grim")  # same as j1
        await queue.submit(j1)
        await queue.submit(j2)
        await queue.submit(j3)

        # Pull with ws-grim busy
        pulled = await queue.next(busy_workspaces={"ws-grim"})
        assert pulled is not None
        assert pulled.workspace_id == "ws-fracton"

    @pytest.mark.asyncio
    async def test_next_null_workspace_not_blocked(self, queue):
        j1 = _make_job(workspace_id=None)
        await queue.submit(j1)
        pulled = await queue.next(busy_workspaces={"ws-grim"})
        assert pulled is not None

    @pytest.mark.asyncio
    async def test_update_status(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(job.id, JobStatus.RUNNING, assigned_slot="slot-0")

        fetched = await queue.get(job.id)
        assert fetched.status == JobStatus.RUNNING
        assert fetched.assigned_slot == "slot-0"

    @pytest.mark.asyncio
    async def test_update_status_with_result(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(
            job.id,
            JobStatus.COMPLETE,
            result="all done",
            transcript=[{"role": "assistant", "content": []}],
        )

        fetched = await queue.get(job.id)
        assert fetched.status == JobStatus.COMPLETE
        assert fetched.result == "all done"
        assert len(fetched.transcript) == 1

    @pytest.mark.asyncio
    async def test_cancel_queued(self, queue):
        job = _make_job()
        await queue.submit(job)
        result = await queue.cancel(job.id)
        assert result is True
        fetched = await queue.get(job.id)
        assert fetched.status == JobStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_running_fails(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(job.id, JobStatus.RUNNING)
        result = await queue.cancel(job.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, queue):
        result = await queue.cancel("job-nope1234")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_blocked(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(job.id, JobStatus.BLOCKED)
        result = await queue.cancel(job.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_list_all(self, queue):
        for i in range(5):
            await queue.submit(_make_job(instructions=f"job {i}"))
        jobs = await queue.list_jobs()
        assert len(jobs) == 5

    @pytest.mark.asyncio
    async def test_list_by_status(self, queue):
        j1 = _make_job()
        j2 = _make_job()
        await queue.submit(j1)
        await queue.submit(j2)
        await queue.update_status(j1.id, JobStatus.COMPLETE)

        queued = await queue.list_jobs(status_filter=JobStatus.QUEUED)
        assert len(queued) == 1
        complete = await queue.list_jobs(status_filter=JobStatus.COMPLETE)
        assert len(complete) == 1

    @pytest.mark.asyncio
    async def test_list_with_limit(self, queue):
        for i in range(10):
            await queue.submit(_make_job())
        jobs = await queue.list_jobs(limit=3)
        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_clarification_flow(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(job.id, JobStatus.RUNNING)

        # Request clarification
        await queue.request_clarification(job.id, "Which branch?")
        fetched = await queue.get(job.id)
        assert fetched.status == JobStatus.BLOCKED
        assert fetched.clarification_question == "Which branch?"

        # Provide answer
        await queue.provide_clarification(job.id, "main")
        fetched = await queue.get(job.id)
        assert fetched.status == JobStatus.QUEUED
        assert fetched.clarification_answer == "main"

    @pytest.mark.asyncio
    async def test_kronos_domains_roundtrip(self, queue):
        job = _make_job(kronos_domains=["physics", "ai-systems"])
        await queue.submit(job)
        fetched = await queue.get(job.id)
        assert fetched.kronos_domains == ["physics", "ai-systems"]

    @pytest.mark.asyncio
    async def test_kronos_fdo_ids_roundtrip(self, queue):
        job = _make_job(kronos_fdo_ids=["pac-comprehensive", "grim-architecture"])
        await queue.submit(job)
        fetched = await queue.get(job.id)
        assert fetched.kronos_fdo_ids == ["pac-comprehensive", "grim-architecture"]

    @pytest.mark.asyncio
    async def test_retry_count_update(self, queue):
        job = _make_job()
        await queue.submit(job)
        await queue.update_status(job.id, JobStatus.QUEUED, retry_count=1)
        fetched = await queue.get(job.id)
        assert fetched.retry_count == 1


# ── AgentSlot tests ──────────────────────────────────────────────

class TestAgentSlot:
    """Test AgentSlot with mocked ClaudeSDKClient."""

    def test_slot_initial_state(self):
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0")
        assert not slot.busy
        assert slot.current_job_id is None

    @pytest.mark.asyncio
    async def test_slot_marks_busy_during_execution(self):
        """Verify slot sets busy=True during execute, False after."""
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0", kronos_mcp_command="")

        busy_during = False

        # Create mock that captures busy state
        mock_result_msg = MagicMock()
        mock_result_msg.total_cost_usd = 0.01
        mock_result_msg.num_turns = 1

        mock_text_block = MagicMock()
        mock_text_block.text = "Done"

        mock_assistant_msg = MagicMock()
        mock_assistant_msg.content = [mock_text_block]

        async def mock_receive():
            nonlocal busy_during
            busy_during = slot.busy
            yield mock_assistant_msg
            yield mock_result_msg

        mock_client = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = mock_receive
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
            with patch("claude_agent_sdk.ClaudeAgentOptions"):
                with patch("claude_agent_sdk.AssistantMessage", type(mock_assistant_msg)):
                    with patch("claude_agent_sdk.ResultMessage", type(mock_result_msg)):
                        with patch("claude_agent_sdk.TextBlock", type(mock_text_block)):
                            job = _make_job()
                            result = await slot.execute(job)

        assert busy_during is True
        assert slot.busy is False

    @pytest.mark.asyncio
    async def test_slot_returns_error_on_exception(self):
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0", kronos_mcp_command="")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("SDK crash"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
            with patch("claude_agent_sdk.ClaudeAgentOptions"):
                job = _make_job()
                result = await slot.execute(job)

        assert result.success is False
        assert "SDK crash" in result.error
        assert slot.busy is False


# ── Prompt builder tests ─────────────────────────────────────────

class TestPromptBuilders:
    def test_build_prompt_basic(self):
        from core.pool.slot import _build_prompt

        job = _make_job(instructions="write fizzbuzz")
        prompt = _build_prompt(job)
        assert "write fizzbuzz" in prompt

    def test_build_prompt_with_plan(self):
        from core.pool.slot import _build_prompt

        job = _make_job(instructions="implement feature", plan="Step 1: do X\nStep 2: do Y")
        prompt = _build_prompt(job)
        assert "implement feature" in prompt
        assert "Step 1: do X" in prompt

    def test_build_prompt_with_clarification(self):
        from core.pool.slot import _build_prompt

        job = _make_job(
            instructions="fix bug",
            clarification_question="Which file?",
            clarification_answer="auth.py",
        )
        prompt = _build_prompt(job)
        assert "Which file?" in prompt
        assert "auth.py" in prompt

    def test_build_system_prompt_basic(self):
        from core.pool.slot import _build_system_prompt

        job = _make_job()
        result = _build_system_prompt(job, "You are a coder.")
        assert "You are a coder." in result

    def test_build_system_prompt_with_context(self):
        from core.pool.slot import _build_system_prompt

        job = _make_job(
            kronos_domains=["physics"],
            kronos_fdo_ids=["pac-comprehensive"],
            workspace_id="ws-grim",
        )
        result = _build_system_prompt(job, "Base prompt")
        assert "physics" in result
        assert "pac-comprehensive" in result
        assert "ws-grim" in result


# ── ExecutionPool tests ──────────────────────────────────────────

class TestExecutionPool:
    """Test ExecutionPool dispatch logic with mocked slots."""

    @pytest.fixture
    def mock_config(self):
        @dataclass
        class MockConfig:
            pool_enabled: bool = True
            pool_num_slots: int = 2
            pool_poll_interval: float = 0.1
            pool_db_path: Path = Path("unused")
            pool_max_turns_per_job: int = 5
            pool_job_timeout_secs: int = 10
            kronos_mcp_command: str = ""
            vault_path: Optional[Path] = None
            skills_path: Optional[Path] = None
            workspace_root: Optional[Path] = None

        return MockConfig()

    @pytest.mark.asyncio
    async def test_pool_start_stop(self, tmp_db, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_db)
        pool = ExecutionPool(q, mock_config)
        await pool.start()

        assert pool.status["running"] is True
        assert len(pool.status["slots"]) == 2

        await pool.stop()
        assert pool.status["running"] is False

    @pytest.mark.asyncio
    async def test_pool_submit(self, tmp_db, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_db)
        pool = ExecutionPool(q, mock_config)
        await pool.start()

        job = _make_job()
        job_id = await pool.submit(job)
        assert job_id == job.id

        fetched = await q.get(job_id)
        assert fetched is not None

        await pool.stop()

    @pytest.mark.asyncio
    async def test_pool_status_shows_idle_slots(self, tmp_db, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_db)
        pool = ExecutionPool(q, mock_config)
        await pool.start()

        status = pool.status
        assert all(not s["busy"] for s in status["slots"])
        assert status["active_jobs"] == 0

        await pool.stop()

    @pytest.mark.asyncio
    async def test_pool_queue_accessor(self, tmp_db, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_db)
        pool = ExecutionPool(q, mock_config)
        assert pool.queue is q


# ── Pool tool tests ──────────────────────────────────────────────

class TestPoolTools:
    """Test LangChain pool tools with mock pool."""

    @pytest.mark.asyncio
    async def test_pool_submit_no_pool(self):
        from core.tools.context import tool_context
        from core.tools.pool_tools import pool_submit

        old = tool_context.execution_pool
        tool_context.execution_pool = None
        try:
            result = await pool_submit.ainvoke({
                "job_type": "code",
                "instructions": "test",
            })
            assert "[ERROR]" in result
        finally:
            tool_context.execution_pool = old

    @pytest.mark.asyncio
    async def test_pool_status_no_pool(self):
        from core.tools.context import tool_context
        from core.tools.pool_tools import pool_status

        old = tool_context.execution_pool
        tool_context.execution_pool = None
        try:
            result = await pool_status.ainvoke({})
            assert "[ERROR]" in result
        finally:
            tool_context.execution_pool = old

    @pytest.mark.asyncio
    async def test_pool_submit_invalid_type(self):
        from core.tools.context import tool_context
        from core.tools.pool_tools import pool_submit

        mock_pool = MagicMock()
        old = tool_context.execution_pool
        tool_context.execution_pool = mock_pool
        try:
            result = await pool_submit.ainvoke({
                "job_type": "invalid",
                "instructions": "test",
            })
            assert "[ERROR]" in result
            assert "Invalid job_type" in result
        finally:
            tool_context.execution_pool = old

    @pytest.mark.asyncio
    async def test_pool_cancel_no_pool(self):
        from core.tools.context import tool_context
        from core.tools.pool_tools import pool_cancel

        old = tool_context.execution_pool
        tool_context.execution_pool = None
        try:
            result = await pool_cancel.ainvoke({"job_id": "job-test1234"})
            assert "[ERROR]" in result
        finally:
            tool_context.execution_pool = old

    @pytest.mark.asyncio
    async def test_pool_list_no_pool(self):
        from core.tools.context import tool_context
        from core.tools.pool_tools import pool_list_jobs

        old = tool_context.execution_pool
        tool_context.execution_pool = None
        try:
            result = await pool_list_jobs.ainvoke({})
            assert "[ERROR]" in result
        finally:
            tool_context.execution_pool = old
