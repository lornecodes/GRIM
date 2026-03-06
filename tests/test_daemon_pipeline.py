"""Unit tests for the daemon pipeline — models, store, state transitions.

Uses real SQLite (temp files). No live API calls.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.daemon.models import (
    InvalidTransition,
    PipelineItem,
    PipelineStatus,
    PRIORITY_ORDER,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
)
from core.daemon.pipeline import PipelineStore


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "test_pipeline.db"


@pytest.fixture
async def store(tmp_db) -> PipelineStore:
    s = PipelineStore(tmp_db)
    await s.initialize()
    return s


# ── Model tests ──────────────────────────────────────────────────


class TestPipelineItem:
    """Test PipelineItem pydantic model."""

    def test_id_auto_generated(self):
        i1 = PipelineItem(story_id="story-a", project_id="proj-x")
        i2 = PipelineItem(story_id="story-b", project_id="proj-x")
        assert i1.id != i2.id
        assert i1.id.startswith("pipeline-")

    def test_id_custom(self):
        item = PipelineItem(id="pipeline-custom", story_id="s", project_id="p")
        assert item.id == "pipeline-custom"

    def test_timestamps_auto(self):
        item = PipelineItem(story_id="s", project_id="p")
        assert item.created_at.tzinfo is not None
        assert item.updated_at.tzinfo is not None

    def test_default_status(self):
        item = PipelineItem(story_id="s", project_id="p")
        assert item.status == PipelineStatus.BACKLOG

    def test_default_priority(self):
        item = PipelineItem(story_id="s", project_id="p")
        assert item.priority == 2  # medium

    def test_serialization(self):
        item = PipelineItem(
            story_id="story-mewtwo-009",
            project_id="proj-mewtwo",
            priority=1,
            assignee="code",
        )
        d = item.model_dump()
        assert d["story_id"] == "story-mewtwo-009"
        assert d["assignee"] == "code"
        assert d["status"] == "backlog"


class TestPipelineStatus:
    """Test status enum and transition rules."""

    def test_all_statuses_have_transitions(self):
        for status in PipelineStatus:
            assert status in VALID_TRANSITIONS

    def test_terminal_statuses_exist(self):
        assert PipelineStatus.MERGED in TERMINAL_STATUSES
        assert PipelineStatus.FAILED in TERMINAL_STATUSES

    def test_merged_has_no_forward_transitions(self):
        assert len(VALID_TRANSITIONS[PipelineStatus.MERGED]) == 0

    def test_failed_can_retry(self):
        assert PipelineStatus.READY in VALID_TRANSITIONS[PipelineStatus.FAILED]

    def test_backlog_to_ready(self):
        assert PipelineStatus.READY in VALID_TRANSITIONS[PipelineStatus.BACKLOG]

    def test_ready_to_dispatched(self):
        assert PipelineStatus.DISPATCHED in VALID_TRANSITIONS[PipelineStatus.READY]

    def test_dispatched_can_review_fail_block(self):
        allowed = VALID_TRANSITIONS[PipelineStatus.DISPATCHED]
        assert PipelineStatus.REVIEW in allowed
        assert PipelineStatus.FAILED in allowed
        assert PipelineStatus.BLOCKED in allowed

    def test_blocked_can_requeue_or_fail(self):
        allowed = VALID_TRANSITIONS[PipelineStatus.BLOCKED]
        assert PipelineStatus.READY in allowed
        assert PipelineStatus.FAILED in allowed

    def test_review_can_merge_or_fail(self):
        allowed = VALID_TRANSITIONS[PipelineStatus.REVIEW]
        assert PipelineStatus.MERGED in allowed
        assert PipelineStatus.FAILED in allowed


class TestPriorityOrder:
    """Test priority ordering constants."""

    def test_critical_is_lowest(self):
        assert PRIORITY_ORDER["critical"] == 0

    def test_low_is_highest(self):
        assert PRIORITY_ORDER["low"] == 3

    def test_ordering(self):
        assert PRIORITY_ORDER["critical"] < PRIORITY_ORDER["high"]
        assert PRIORITY_ORDER["high"] < PRIORITY_ORDER["medium"]
        assert PRIORITY_ORDER["medium"] < PRIORITY_ORDER["low"]


class TestInvalidTransition:
    """Test InvalidTransition exception."""

    def test_message(self):
        err = InvalidTransition(PipelineStatus.BACKLOG, PipelineStatus.MERGED)
        assert "backlog" in str(err)
        assert "merged" in str(err)

    def test_attrs(self):
        err = InvalidTransition(PipelineStatus.READY, PipelineStatus.MERGED)
        assert err.current == PipelineStatus.READY
        assert err.target == PipelineStatus.MERGED


# ── Store tests ──────────────────────────────────────────────────


class TestPipelineStoreInit:
    """Test store initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_db(self, tmp_db):
        store = PipelineStore(tmp_db)
        await store.initialize()
        assert tmp_db.exists()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, tmp_db):
        store = PipelineStore(tmp_db)
        await store.initialize()
        await store.initialize()  # should not raise

    @pytest.mark.asyncio
    async def test_initialize_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "deep" / "nested" / "pipeline.db"
        store = PipelineStore(db_path)
        await store.initialize()
        assert db_path.exists()


class TestPipelineStoreAdd:
    """Test adding items to the store."""

    @pytest.mark.asyncio
    async def test_add_returns_item(self, store):
        item = await store.add("story-001", "proj-a")
        assert item.story_id == "story-001"
        assert item.project_id == "proj-a"
        assert item.status == PipelineStatus.BACKLOG
        assert item.id.startswith("pipeline-")

    @pytest.mark.asyncio
    async def test_add_with_priority_and_assignee(self, store):
        item = await store.add("story-002", "proj-a", priority=0, assignee="code")
        assert item.priority == 0
        assert item.assignee == "code"

    @pytest.mark.asyncio
    async def test_add_duplicate_story_raises(self, store):
        await store.add("story-001", "proj-a")
        with pytest.raises(Exception):  # aiosqlite IntegrityError
            await store.add("story-001", "proj-b")

    @pytest.mark.asyncio
    async def test_add_persists(self, store):
        item = await store.add("story-001", "proj-a")
        fetched = await store.get(item.id)
        assert fetched is not None
        assert fetched.story_id == "story-001"


class TestPipelineStoreAdvance:
    """Test state transitions with guards."""

    @pytest.mark.asyncio
    async def test_backlog_to_ready(self, store):
        item = await store.add("story-001", "proj-a")
        advanced = await store.advance(item.id, PipelineStatus.READY)
        assert advanced.status == PipelineStatus.READY

    @pytest.mark.asyncio
    async def test_ready_to_dispatched_with_job_id(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        advanced = await store.advance(
            item.id, PipelineStatus.DISPATCHED, job_id="job-abc12345"
        )
        assert advanced.status == PipelineStatus.DISPATCHED
        assert advanced.job_id == "job-abc12345"

    @pytest.mark.asyncio
    async def test_dispatched_to_review(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-x")
        advanced = await store.advance(
            item.id, PipelineStatus.REVIEW, workspace_id="ws-001"
        )
        assert advanced.status == PipelineStatus.REVIEW
        assert advanced.workspace_id == "ws-001"

    @pytest.mark.asyncio
    async def test_review_to_merged(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.REVIEW)
        advanced = await store.advance(item.id, PipelineStatus.MERGED)
        assert advanced.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_dispatched_to_failed_with_error(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        advanced = await store.advance(
            item.id, PipelineStatus.FAILED, error="agent crashed"
        )
        assert advanced.status == PipelineStatus.FAILED
        assert advanced.error == "agent crashed"

    @pytest.mark.asyncio
    async def test_dispatched_to_blocked(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        advanced = await store.advance(item.id, PipelineStatus.BLOCKED)
        assert advanced.status == PipelineStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_blocked_to_ready(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.BLOCKED)
        advanced = await store.advance(item.id, PipelineStatus.READY)
        assert advanced.status == PipelineStatus.READY

    @pytest.mark.asyncio
    async def test_failed_to_ready_retry(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.FAILED)
        advanced = await store.advance(
            item.id, PipelineStatus.READY, attempts=1
        )
        assert advanced.status == PipelineStatus.READY
        assert advanced.attempts == 1

    @pytest.mark.asyncio
    async def test_invalid_backlog_to_dispatched(self, store):
        item = await store.add("story-001", "proj-a")
        with pytest.raises(InvalidTransition):
            await store.advance(item.id, PipelineStatus.DISPATCHED)

    @pytest.mark.asyncio
    async def test_invalid_backlog_to_merged(self, store):
        item = await store.add("story-001", "proj-a")
        with pytest.raises(InvalidTransition):
            await store.advance(item.id, PipelineStatus.MERGED)

    @pytest.mark.asyncio
    async def test_invalid_ready_to_review(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        with pytest.raises(InvalidTransition):
            await store.advance(item.id, PipelineStatus.REVIEW)

    @pytest.mark.asyncio
    async def test_invalid_merged_to_anything(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.REVIEW)
        await store.advance(item.id, PipelineStatus.MERGED)
        with pytest.raises(InvalidTransition):
            await store.advance(item.id, PipelineStatus.READY)

    @pytest.mark.asyncio
    async def test_advance_nonexistent_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            await store.advance("pipeline-nonexistent", PipelineStatus.READY)

    @pytest.mark.asyncio
    async def test_advance_updates_timestamp(self, store):
        item = await store.add("story-001", "proj-a")
        original = item.updated_at
        advanced = await store.advance(item.id, PipelineStatus.READY)
        assert advanced.updated_at >= original


class TestPipelineStoreQueries:
    """Test lookup and listing methods."""

    @pytest.mark.asyncio
    async def test_get_by_story(self, store):
        item = await store.add("story-001", "proj-a")
        fetched = await store.get_by_story("story-001")
        assert fetched is not None
        assert fetched.id == item.id

    @pytest.mark.asyncio
    async def test_get_by_story_not_found(self, store):
        assert await store.get_by_story("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_by_job(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-xyz")
        fetched = await store.get_by_job("job-xyz")
        assert fetched is not None
        assert fetched.story_id == "story-001"

    @pytest.mark.asyncio
    async def test_get_by_job_not_found(self, store):
        assert await store.get_by_job("nonexistent") is None

    @pytest.mark.asyncio
    async def test_next_ready_priority_order(self, store):
        await store.add("story-low", "proj-a", priority=3)
        await store.add("story-high", "proj-a", priority=1)
        await store.add("story-crit", "proj-a", priority=0)

        # Advance all to READY
        items = await store.list_items()
        for item in items:
            await store.advance(item.id, PipelineStatus.READY)

        first = await store.next_ready()
        assert first is not None
        assert first.story_id == "story-crit"

    @pytest.mark.asyncio
    async def test_next_ready_none_when_empty(self, store):
        assert await store.next_ready() is None

    @pytest.mark.asyncio
    async def test_next_ready_skips_non_ready(self, store):
        item = await store.add("story-001", "proj-a")
        # still BACKLOG
        assert await store.next_ready() is None

    @pytest.mark.asyncio
    async def test_list_items_all(self, store):
        await store.add("story-001", "proj-a")
        await store.add("story-002", "proj-a")
        await store.add("story-003", "proj-b")
        items = await store.list_items()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_list_items_status_filter(self, store):
        item1 = await store.add("story-001", "proj-a")
        await store.add("story-002", "proj-a")
        await store.advance(item1.id, PipelineStatus.READY)

        ready_items = await store.list_items(status_filter=PipelineStatus.READY)
        assert len(ready_items) == 1
        assert ready_items[0].story_id == "story-001"

    @pytest.mark.asyncio
    async def test_list_items_project_filter(self, store):
        await store.add("story-001", "proj-a")
        await store.add("story-002", "proj-b")

        items = await store.list_items(project_filter="proj-a")
        assert len(items) == 1
        assert items[0].project_id == "proj-a"

    @pytest.mark.asyncio
    async def test_list_items_with_limit(self, store):
        for i in range(10):
            await store.add(f"story-{i:03d}", "proj-a")
        items = await store.list_items(limit=5)
        assert len(items) == 5

    @pytest.mark.asyncio
    async def test_count_by_status(self, store):
        await store.add("story-001", "proj-a")
        await store.add("story-002", "proj-a")
        item3 = await store.add("story-003", "proj-a")
        await store.advance(item3.id, PipelineStatus.READY)

        counts = await store.count_by_status()
        assert counts.get("backlog", 0) == 2
        assert counts.get("ready", 0) == 1


class TestPipelineStoreRemove:
    """Test remove and prune methods."""

    @pytest.mark.asyncio
    async def test_remove_existing(self, store):
        item = await store.add("story-001", "proj-a")
        assert await store.remove(item.id)
        assert await store.get(item.id) is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, store):
        assert not await store.remove("pipeline-nonexistent")

    @pytest.mark.asyncio
    async def test_prune_merged_removes_old(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.REVIEW)
        await store.advance(item.id, PipelineStatus.MERGED)

        # Prune with 0 days = remove everything merged
        count = await store.prune_merged(days=0)
        assert count == 1
        assert await store.get(item.id) is None

    @pytest.mark.asyncio
    async def test_prune_merged_keeps_recent(self, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED)
        await store.advance(item.id, PipelineStatus.REVIEW)
        await store.advance(item.id, PipelineStatus.MERGED)

        # Prune with 30 days = keep recent
        count = await store.prune_merged(days=30)
        assert count == 0
        assert await store.get(item.id) is not None
