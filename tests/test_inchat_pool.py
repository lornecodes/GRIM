"""Tests for InChatExecutionPool — ephemeral inline job execution."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.pool.events import PoolEventType
from core.pool.inchat import InChatExecutionPool
from core.pool.models import Job, JobResult, JobType


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def pool():
    return InChatExecutionPool(max_turns=5)


@pytest.fixture
def sample_job():
    return Job(job_type=JobType.CODE, instructions="Fix the bug")


def _mock_slot_execute(result: JobResult):
    """Patch AgentSlot to return a canned result."""
    slot_mock = MagicMock()
    slot_mock.execute = AsyncMock(return_value=result)
    return patch("core.pool.inchat.AgentSlot", return_value=slot_mock)


# ── Basic execution ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_success(pool, sample_job):
    result = JobResult(job_id=sample_job.id, success=True, result="Fixed", cost_usd=0.05, num_turns=3)
    with _mock_slot_execute(result):
        out = await pool.run(sample_job)
    assert out.success is True
    assert out.result == "Fixed"
    assert pool.current_job is None


@pytest.mark.asyncio
async def test_run_failure(pool, sample_job):
    result = JobResult(job_id=sample_job.id, success=False, error="Compilation error")
    with _mock_slot_execute(result):
        out = await pool.run(sample_job)
    assert out.success is False
    assert out.error == "Compilation error"


@pytest.mark.asyncio
async def test_run_exception(pool, sample_job):
    slot_mock = MagicMock()
    slot_mock.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("core.pool.inchat.AgentSlot", return_value=slot_mock):
        out = await pool.run(sample_job)
    assert out.success is False
    assert "boom" in out.error


# ── Event emission ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_start_and_complete(pool, sample_job):
    events = []
    async def collector(e):
        events.append(e)

    result = JobResult(job_id=sample_job.id, success=True, result="ok")
    with _mock_slot_execute(result):
        await pool.run(sample_job, on_event=collector)

    types = [e.type for e in events]
    assert PoolEventType.JOB_STARTED in types
    assert PoolEventType.JOB_COMPLETE in types


@pytest.mark.asyncio
async def test_emits_start_and_failed(pool, sample_job):
    events = []
    async def collector(e):
        events.append(e)

    result = JobResult(job_id=sample_job.id, success=False, error="oops")
    with _mock_slot_execute(result):
        await pool.run(sample_job, on_event=collector)

    types = [e.type for e in events]
    assert PoolEventType.JOB_STARTED in types
    assert PoolEventType.JOB_FAILED in types


@pytest.mark.asyncio
async def test_on_event_cleaned_up(pool, sample_job):
    callback = AsyncMock()
    result = JobResult(job_id=sample_job.id, success=True, result="ok")
    with _mock_slot_execute(result):
        await pool.run(sample_job, on_event=callback)
    assert pool.events.subscriber_count == 0


# ── Busy check ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_busy_rejects_second_job(pool, sample_job):
    """If a job is already running, second job gets rejected."""
    # Simulate busy state
    pool._current_job = sample_job
    job2 = Job(job_type=JobType.RESEARCH, instructions="Do research")
    result = await pool.run(job2)
    assert result.success is False
    assert "busy" in result.error.lower()
    pool._current_job = None  # cleanup


def test_busy_property(pool, sample_job):
    assert pool.busy is False
    pool._current_job = sample_job
    assert pool.busy is True
    pool._current_job = None


# ── Cancel ───────────────────────────────────────────────────────


def test_cancel_no_job(pool):
    assert pool.cancel() is False


# ── Properties ───────────────────────────────────────────────────


def test_current_job_initially_none(pool):
    assert pool.current_job is None


def test_events_bus_exists(pool):
    assert pool.events is not None
    assert pool.events.subscriber_count == 0


# ── Import ───────────────────────────────────────────────────────


def test_import_from_pool_package():
    from core.pool import InChatExecutionPool
    assert InChatExecutionPool is not None
