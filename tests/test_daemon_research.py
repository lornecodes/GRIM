"""Tests for Phase 5D: Research-First Workflow.

Tests research context injection, research validation, result_summary storage,
DAEMON_RESEARCH_COMPLETE events, and E2E research→code flow.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.daemon.intelligence import OutputValidator, Verdict
from core.daemon.models import PipelineItem, PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "test.db")


async def _init_store(tmp_path: Path) -> PipelineStore:
    store = _make_store(tmp_path)
    await store.initialize()
    return store


# ── TestResearchValidation ─────────────────────────────────────────────────

class TestResearchValidation:
    """Test OutputValidator.validate_research() heuristics."""

    def setup_method(self):
        self.validator = OutputValidator()

    def test_empty_result_fails(self):
        verdict = self.validator.validate_research("")
        assert verdict.outcome == "fail"
        assert "no output" in verdict.reasoning.lower()

    def test_none_result_fails(self):
        verdict = self.validator.validate_research(None)
        assert verdict.outcome == "fail"

    def test_short_result_fails(self):
        verdict = self.validator.validate_research("Some brief text.")
        assert verdict.outcome == "fail"
        assert "too short" in verdict.reasoning.lower()

    def test_bullet_list_passes(self):
        text = """## Authentication Methods

- OAuth2: Industry standard, supports refresh tokens
- JWT: Stateless, good for microservices
- API Keys: Simple but less secure
- Session-based: Traditional, requires server state

Each method has different trade-offs for our use case.
"""
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"

    def test_numbered_list_passes(self):
        text = """# Findings

1. The PAC regulation coefficient should be 0.618
2. SEC gradient needs normalization
3. RBF threshold at 1.057 matches predictions
4. MED depth constraint verified at 2
"""
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"

    def test_code_blocks_pass(self):
        text = """## Implementation Analysis

The existing pattern uses:
```python
def calculate_gradient(field):
    return np.gradient(field) * SEC_COEFFICIENT
```

This should be extended with the PAC normalization term.
The approach matches what's documented in the ADR.
"""
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"

    def test_headings_pass(self):
        text = """## Overview
The system uses event-driven architecture.

## Key Components
Scanner, Pipeline, Engine, Intelligence layer.

## Recommendations
Proceed with the dependency-aware dispatch model.
"""
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"

    def test_non_answer_with_no_structure_fails(self):
        text = (
            "I was unable to determine the correct approach. "
            "This needs more investigation and I could not find "
            "any clear answer in the available documentation."
        )
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "fail"
        assert "actionable" in verdict.reasoning.lower()

    def test_non_answer_with_structure_passes(self):
        """Non-answer phrases are OK if structured content is also present."""
        text = """## Findings

While the results are inconclusive on some fronts:

- Authentication layer uses JWT with RS256
- Token refresh cycle is 15 minutes
- Rate limiting is not currently implemented

Needs more investigation on the caching layer.
"""
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"

    def test_long_unstructured_passes(self):
        """Long enough text passes even without structure."""
        text = "A " * 120  # 240 chars
        verdict = self.validator.validate_research(text)
        assert verdict.outcome == "pass"
        assert "lacks structured" in verdict.reasoning.lower()

    def test_whitespace_only_fails(self):
        verdict = self.validator.validate_research("   \n\n  \t  ")
        assert verdict.outcome == "fail"


# ── TestResearchContext ──────────────────────────────────────────────────────

class TestResearchContext:
    """Test ContextBuilder._resolve_research_context()."""

    @pytest.fixture
    def builder(self, tmp_path):
        from core.daemon.context import ContextBuilder
        store = MagicMock()
        return ContextBuilder(tmp_path / "vault", tmp_path / "workspace", pipeline_store=store)

    def test_no_pipeline_store(self, tmp_path):
        from core.daemon.context import ContextBuilder
        builder = ContextBuilder(tmp_path / "vault", tmp_path / "workspace")
        result = builder._resolve_research_context({"depends_on": '["story-a"]'})
        assert result == ""

    def test_no_dependencies(self, builder):
        result = builder._resolve_research_context({"title": "Test"})
        assert result == ""

    def test_empty_dependencies(self, builder):
        result = builder._resolve_research_context({"depends_on": ""})
        assert result == ""

    def test_empty_json_array(self, builder):
        result = builder._resolve_research_context({"depends_on": "[]"})
        assert result == ""

    def test_research_dep_included(self, builder):
        """Research dependency with result_summary is included."""
        mock_item = MagicMock()
        mock_item.assignee = "research"
        mock_item.result_summary = "Found that JWT is the best approach for our use case."

        # Mock the sync helper to return our item
        builder._get_pipeline_item_sync = MagicMock(return_value=mock_item)

        result = builder._resolve_research_context({"depends_on": '["story-research-001"]'})
        assert "## Prior Research" in result
        assert "story-research-001" in result
        assert "JWT" in result

    def test_non_research_dep_excluded(self, builder):
        """Non-research dependencies are skipped."""
        mock_item = MagicMock()
        mock_item.assignee = "code"
        mock_item.result_summary = "Some code result"

        builder._get_pipeline_item_sync = MagicMock(return_value=mock_item)

        result = builder._resolve_research_context({"depends_on": '["story-code-001"]'})
        assert result == ""

    def test_no_result_summary_excluded(self, builder):
        """Research dep without result_summary is skipped."""
        mock_item = MagicMock()
        mock_item.assignee = "research"
        mock_item.result_summary = ""

        builder._get_pipeline_item_sync = MagicMock(return_value=mock_item)

        result = builder._resolve_research_context({"depends_on": '["story-research-001"]'})
        assert result == ""

    def test_pipeline_item_not_found(self, builder):
        """Missing pipeline item is gracefully handled."""
        builder._get_pipeline_item_sync = MagicMock(return_value=None)

        result = builder._resolve_research_context({"depends_on": '["story-missing"]'})
        assert result == ""

    def test_multiple_research_deps(self, builder):
        """Multiple research deps are all included."""
        items = {
            "story-r1": MagicMock(assignee="research", result_summary="Finding A: use JWT"),
            "story-r2": MagicMock(assignee="research", result_summary="Finding B: add rate limits"),
        }
        builder._get_pipeline_item_sync = MagicMock(side_effect=lambda sid: items.get(sid))

        result = builder._resolve_research_context({"depends_on": '["story-r1", "story-r2"]'})
        assert "Finding A" in result
        assert "Finding B" in result

    def test_depends_on_as_list(self, builder):
        """depends_on as a Python list (not JSON string) works."""
        mock_item = MagicMock()
        mock_item.assignee = "research"
        mock_item.result_summary = "Research result"

        builder._get_pipeline_item_sync = MagicMock(return_value=mock_item)

        result = builder._resolve_research_context({"depends_on": ["story-r1"]})
        assert "## Prior Research" in result

    def test_budget_limit_respected(self, builder):
        """Research context respects MAX_RESEARCH_CONTEXT_CHARS budget."""
        from core.daemon.context import MAX_RESEARCH_CONTEXT_CHARS

        # Create two research deps where second would exceed budget
        items = {
            "story-r1": MagicMock(
                assignee="research",
                result_summary="A" * (MAX_RESEARCH_CONTEXT_CHARS - 100),
            ),
            "story-r2": MagicMock(
                assignee="research",
                result_summary="B" * 500,
            ),
        }
        builder._get_pipeline_item_sync = MagicMock(side_effect=lambda sid: items.get(sid))

        result = builder._resolve_research_context({"depends_on": '["story-r1", "story-r2"]'})
        assert "## Prior Research" in result
        assert "story-r1" in result
        # Second dep should be dropped due to budget
        assert "story-r2" not in result

    def test_exception_in_lookup_handled(self, builder):
        """Exceptions during pipeline lookup are handled gracefully."""
        builder._get_pipeline_item_sync = MagicMock(side_effect=Exception("DB error"))

        result = builder._resolve_research_context({"depends_on": '["story-r1"]'})
        assert result == ""


# ── TestContextBuilderIntegration ────────────────────────────────────────────

class TestContextBuilderIntegration:
    """Test that research context appears in full build() output."""

    def test_research_context_in_build(self, tmp_path):
        from core.daemon.context import ContextBuilder

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        mock_store = MagicMock()
        builder = ContextBuilder(vault_path, tmp_path, pipeline_store=mock_store)

        # Mock vault to return empty project
        mock_vault = MagicMock()
        mock_vault.get.return_value = None
        builder._vault = mock_vault

        # Mock research context
        mock_item = MagicMock()
        mock_item.assignee = "research"
        mock_item.result_summary = "Research found: use event-driven architecture"
        builder._get_pipeline_item_sync = MagicMock(return_value=mock_item)

        story_data = {
            "id": "story-impl-001",
            "title": "Implement event system",
            "description": "Build the event bus",
            "depends_on": '["story-research-001"]',
            "assignee": "code",
        }

        result = builder.build(story_data, "proj-test")
        assert "Prior Research" in result
        assert "event-driven architecture" in result

    def test_no_research_when_no_deps(self, tmp_path):
        from core.daemon.context import ContextBuilder

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        builder = ContextBuilder(vault_path, tmp_path, pipeline_store=MagicMock())
        mock_vault = MagicMock()
        mock_vault.get.return_value = None
        builder._vault = mock_vault

        story_data = {
            "id": "story-001",
            "title": "A story",
            "assignee": "code",
        }

        result = builder.build(story_data, "proj-test")
        assert "Prior Research" not in result


# ── TestResultSummaryStorage ────────────────────────────────────────────────

class TestResultSummaryStorage:
    """Test that result_summary is persisted in pipeline store."""

    @pytest.mark.asyncio
    async def test_result_summary_stored(self, tmp_path):
        store = await _init_store(tmp_path)
        item = await store.add("story-1", "proj-1", assignee="research")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="j1")

        # Store result summary
        updated = await store.update_fields(item.id, result_summary="Found JWT best")
        assert updated.result_summary == "Found JWT best"

        # Verify it persists on re-fetch
        fetched = await store.get(item.id)
        assert fetched.result_summary == "Found JWT best"

    @pytest.mark.asyncio
    async def test_result_summary_in_advance(self, tmp_path):
        store = await _init_store(tmp_path)
        item = await store.add("story-1", "proj-1", assignee="research")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(
            item.id, PipelineStatus.DISPATCHED,
            job_id="j1", result_summary="Research findings",
        )

        fetched = await store.get(item.id)
        assert fetched.result_summary == "Research findings"

    @pytest.mark.asyncio
    async def test_result_summary_default_empty(self, tmp_path):
        store = await _init_store(tmp_path)
        item = await store.add("story-1", "proj-1")
        assert item.result_summary == ""

    @pytest.mark.asyncio
    async def test_result_summary_via_get_by_story(self, tmp_path):
        store = await _init_store(tmp_path)
        item = await store.add("story-1", "proj-1")
        await store.update_fields(item.id, result_summary="Test summary")

        fetched = await store.get_by_story("story-1")
        assert fetched.result_summary == "Test summary"


# ── TestEngineResearchHandling ──────────────────────────────────────────────

class TestEngineResearchHandling:
    """Test engine behavior for research job completion."""

    @pytest.fixture
    def engine_deps(self, tmp_path):
        db_path = tmp_path / "pipeline.db"
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        pool_queue = AsyncMock()
        pool_events = PoolEventBus()

        return {
            "db_path": db_path,
            "vault_path": vault_path,
            "pool_queue": pool_queue,
            "pool_events": pool_events,
        }

    @pytest.fixture
    def make_engine(self, engine_deps):
        async def _make(**config_overrides):
            from core.daemon.engine import ManagementEngine

            config = MagicMock()
            config.daemon_poll_interval = 999
            config.daemon_auto_approve_threshold = 3
            config.daemon_auto_resolve = True
            config.daemon_validate_output = True
            config.daemon_max_daemon_retries = 2
            config.daemon_resolve_model = "claude-sonnet-4-6"
            config.daemon_validate_model = "claude-opus-4-6"
            config.daemon_resolve_confidence_threshold = 0.7
            config.daemon_default_owner = "grim"
            config.daemon_nudge_after_days = 3
            config.daemon_max_concurrent_jobs = 1
            config.daemon_auto_dispatch = True
            config.daemon_db_path = engine_deps["db_path"]
            config.vault_path = engine_deps["vault_path"]
            config.daemon_project_filter = []
            config.workspace_root = engine_deps["vault_path"].parent

            for key, value in config_overrides.items():
                setattr(config, key, value)

            engine = ManagementEngine(
                config=config,
                pool_queue=engine_deps["pool_queue"],
                pool_events=engine_deps["pool_events"],
                vault_path=engine_deps["vault_path"],
            )
            await engine._store.initialize()
            return engine
        return _make

    @pytest.mark.asyncio
    async def test_research_complete_stores_summary(self, make_engine, engine_deps):
        engine = await make_engine()
        store = engine._store

        # Add and advance a research story
        item = await store.add("story-r1", "proj-1", assignee="research")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="j1")

        # Mock pool queue to return job with result
        mock_job = MagicMock()
        mock_job.result = "Research found: use JWT with RS256"
        engine._pool_queue.get = AsyncMock(return_value=mock_job)

        # Mock story details
        engine._get_story_details = MagicMock(return_value={
            "id": "story-r1",
            "title": "Research auth",
            "acceptance_criteria": [],
        })

        # Handle completion
        await engine._handle_complete(item, "j1", {"workspace_id": "ws1"})

        # Verify result_summary was stored
        updated = await store.get(item.id)
        assert updated.result_summary == "Research found: use JWT with RS256"

    @pytest.mark.asyncio
    async def test_research_complete_emits_event(self, make_engine, engine_deps):
        engine = await make_engine()
        store = engine._store

        item = await store.add("story-r1", "proj-1", assignee="research")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="j1")

        mock_job = MagicMock()
        mock_job.result = "Research findings here"
        engine._pool_queue.get = AsyncMock(return_value=mock_job)
        engine._get_story_details = MagicMock(return_value={
            "id": "story-r1",
            "title": "Research",
            "acceptance_criteria": [],
        })

        # Capture events
        events: list[PoolEvent] = []

        async def capture(event):
            events.append(event)

        engine_deps["pool_events"].subscribe(capture)

        await engine._handle_complete(item, "j1", {"workspace_id": "ws1"})

        research_events = [e for e in events if e.type == PoolEventType.DAEMON_RESEARCH_COMPLETE]
        assert len(research_events) == 1
        assert research_events[0].data["story_id"] == "story-r1"
        assert research_events[0].data["has_result"] is True

    @pytest.mark.asyncio
    async def test_non_research_no_research_event(self, make_engine, engine_deps):
        engine = await make_engine()
        store = engine._store

        item = await store.add("story-c1", "proj-1", assignee="code")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="j1")

        mock_job = MagicMock()
        mock_job.result = "Code written"
        engine._pool_queue.get = AsyncMock(return_value=mock_job)
        engine._get_story_details = MagicMock(return_value={
            "id": "story-c1",
            "title": "Code task",
            "acceptance_criteria": [],
        })

        events: list[PoolEvent] = []

        async def capture(event):
            events.append(event)

        engine_deps["pool_events"].subscribe(capture)

        await engine._handle_complete(item, "j1", {"workspace_id": "ws1"})

        research_events = [e for e in events if e.type == PoolEventType.DAEMON_RESEARCH_COMPLETE]
        assert len(research_events) == 0

    @pytest.mark.asyncio
    async def test_result_summary_truncated_at_2000(self, make_engine, engine_deps):
        engine = await make_engine()
        store = engine._store

        item = await store.add("story-r1", "proj-1", assignee="research")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="j1")

        long_result = "X" * 5000
        mock_job = MagicMock()
        mock_job.result = long_result
        engine._pool_queue.get = AsyncMock(return_value=mock_job)
        engine._get_story_details = MagicMock(return_value={
            "id": "story-r1",
            "title": "Research",
            "acceptance_criteria": [],
        })

        await engine._handle_complete(item, "j1", {"workspace_id": "ws1"})

        updated = await store.get(item.id)
        assert len(updated.result_summary) == 2000


# ── TestResearchEventFormatting ──────────────────────────────────────────────

class TestResearchEventFormatting:
    """Test Discord formatting for daemon_research_complete events."""

    def test_research_complete_with_result(self):
        from clients.daemon_commands import format_daemon_event

        result = format_daemon_event({
            "type": "daemon_research_complete",
            "job_id": "j1",
            "data": {
                "story_id": "story-r1",
                "has_result": True,
            },
        })
        assert result is not None
        assert "Research Done" in result
        assert "story-r1" in result
        assert "Results captured" in result

    def test_research_complete_without_result(self):
        from clients.daemon_commands import format_daemon_event

        result = format_daemon_event({
            "type": "daemon_research_complete",
            "job_id": "j1",
            "data": {
                "story_id": "story-r2",
                "has_result": False,
            },
        })
        assert result is not None
        assert "No results captured" in result

    def test_research_event_type_detected(self):
        from clients.daemon_commands import is_daemon_event

        assert is_daemon_event({"type": "daemon_research_complete"})

    def test_research_event_in_daemon_types(self):
        from clients.daemon_commands import DAEMON_EVENT_TYPES

        assert "daemon_research_complete" in DAEMON_EVENT_TYPES


# ── TestResearchEventType ────────────────────────────────────────────────────

class TestResearchEventType:
    """Test DAEMON_RESEARCH_COMPLETE event type."""

    def test_event_type_exists(self):
        assert hasattr(PoolEventType, "DAEMON_RESEARCH_COMPLETE")
        assert PoolEventType.DAEMON_RESEARCH_COMPLETE.value == "daemon_research_complete"

    def test_event_creation(self):
        event = PoolEvent(
            type=PoolEventType.DAEMON_RESEARCH_COMPLETE,
            job_id="j1",
            data={"story_id": "story-r1", "has_result": True},
        )
        d = event.to_dict()
        assert d["event_type"] == "daemon_research_complete"
        assert d["story_id"] == "story-r1"
