"""Tests for the daemon intelligence module — Phase 3 of Mewtwo.

Tests ClarificationResolver, OutputValidator, RetryEnricher, and
their integration with ManagementEngine.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.daemon.intelligence import (
    ClarificationResolver,
    OutputValidator,
    Resolution,
    RetryEnricher,
    Verdict,
    _MIN_KEYWORD_OVERLAP,
    _safe_response_text,
)


# ── Resolution dataclass ─────────────────────────────────────


class TestResolution:

    def test_defaults(self):
        r = Resolution(answered=False)
        assert r.answered is False
        assert r.answer is None
        assert r.confidence == 0.0
        assert r.source == "escalated"

    def test_full(self):
        r = Resolution(answered=True, answer="yes", confidence=0.9, source="mechanical")
        assert r.answered is True
        assert r.answer == "yes"
        assert r.confidence == 0.9
        assert r.source == "mechanical"


# ── Verdict dataclass ─────────────────────────────────────────


class TestVerdict:

    def test_defaults(self):
        v = Verdict()
        assert v.outcome == "pass"
        assert v.reasoning == ""
        assert v.missing_criteria == []

    def test_full(self):
        v = Verdict(outcome="fail", reasoning="Missing tests", missing_criteria=["Tests pass"])
        assert v.outcome == "fail"
        assert v.missing_criteria == ["Tests pass"]


# ── ClarificationResolver ────────────────────────────────────


class TestMechanicalMatch:
    """Tests for ClarificationResolver._mechanical_match()."""

    def setup_method(self):
        self.resolver = ClarificationResolver()

    def test_exact_keyword_overlap(self):
        boundaries = (
            "### Agent Handles Autonomously\n"
            "- Adding new read-only tools that query existing SQLite tables\n"
            "- Updating tool schemas parameter names descriptions return formats\n"
            "\n"
            "### Escalate to Human\n"
            "- Adding new write tools each one needs review for state machine safety\n"
            "- Changing the SQLite schema or adding new tables affects pool writer\n"
        )
        question = "Should I add a new read-only tool to query the SQLite tables?"
        result = self.resolver._mechanical_match(question, boundaries)
        assert result is not None
        assert "read-only tools" in result

    def test_no_overlap(self):
        boundaries = "Agent handles: updating documentation. Escalate: changing API endpoints."
        question = "Should I use React or Vue for the frontend?"
        result = self.resolver._mechanical_match(question, boundaries)
        assert result is None

    def test_empty_boundaries(self):
        result = self.resolver._mechanical_match("Any question?", "")
        assert result is None

    def test_whitespace_boundaries(self):
        result = self.resolver._mechanical_match("Any question?", "   \n  ")
        assert result is None

    def test_short_question(self):
        """Questions with fewer than 2 content words should return None."""
        boundaries = "Some boundary text with keywords."
        result = self.resolver._mechanical_match("Help?", boundaries)
        assert result is None

    def test_multiple_paragraphs_best_match(self):
        boundaries = (
            "Paragraph about authentication and security tokens.\n"
            "Handles user session management.\n"
            "\n"
            "Paragraph about database schema migration and table creation.\n"
            "Manages SQLite connection pool setup.\n"
            "\n"
            "Paragraph about frontend styling and CSS themes.\n"
            "Handles component layout rendering.\n"
        )
        question = "How should I handle the database schema migration for new tables?"
        result = self.resolver._mechanical_match(question, boundaries)
        assert result is not None
        assert "database" in result.lower()

    def test_paragraph_splitting(self):
        """Paragraphs split on blank lines."""
        boundaries = "First paragraph content.\n\nSecond paragraph content."
        # _mechanical_match splits on \n\s*\n
        resolver = ClarificationResolver()
        keywords = resolver._extract_keywords(boundaries)
        assert "first" in keywords or "second" in keywords


class TestExtractKeywords:

    def test_filters_stop_words(self):
        resolver = ClarificationResolver()
        keywords = resolver._extract_keywords("Should I use the existing database?")
        assert "should" not in keywords
        assert "the" not in keywords
        assert "existing" in keywords
        assert "database" in keywords

    def test_lowercase(self):
        resolver = ClarificationResolver()
        keywords = resolver._extract_keywords("SQLite Database Tables")
        assert "sqlite" in keywords
        assert "database" in keywords
        assert "tables" in keywords

    def test_single_char_filtered(self):
        resolver = ClarificationResolver()
        keywords = resolver._extract_keywords("a b c database")
        assert "a" not in keywords
        assert "b" not in keywords
        assert "database" in keywords


class TestResolverResolve:

    @pytest.mark.asyncio
    async def test_empty_question_escalates(self):
        resolver = ClarificationResolver()
        result = await resolver.resolve("", "boundaries", "context")
        assert result.answered is False
        assert result.source == "escalated"

    @pytest.mark.asyncio
    async def test_whitespace_question_escalates(self):
        resolver = ClarificationResolver()
        result = await resolver.resolve("   ", "boundaries", "context")
        assert result.answered is False

    @pytest.mark.asyncio
    async def test_mechanical_match_succeeds(self):
        """Mechanical match should short-circuit LLM call."""
        boundaries = (
            "### Agent Handles Autonomously\n"
            "- Adding read-only tools that query existing SQLite database tables\n"
            "- Updating tool schemas parameter names descriptions return formats\n"
        )
        resolver = ClarificationResolver()
        result = await resolver.resolve(
            "Can I add a new read-only tool to query SQLite tables?",
            boundaries,
            "Some ADR context",
        )
        assert result.answered is True
        assert result.source == "mechanical"
        assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_llm_fallback_on_no_mechanical(self):
        """When mechanical fails, should try LLM."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Yes, use the existing pattern.")]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            resolver = ClarificationResolver()
            result = await resolver.resolve(
                "Should I use WebSockets or SSE?",
                "Unrelated boundary text about databases.",
                "The design specifies WebSocket for real-time updates.",
            )
            assert result.answered is True
            assert result.source == "llm"
            assert "existing pattern" in result.answer

    @pytest.mark.asyncio
    async def test_llm_cannot_answer(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="CANNOT_ANSWER")]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            resolver = ClarificationResolver()
            result = await resolver.resolve(
                "What color should the button be?",
                "",
                "ADR about database design.",
            )
            assert result.answered is False
            assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_llm_error_escalates(self):
        """API errors should gracefully escalate."""
        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(side_effect=Exception("API timeout"))
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            resolver = ClarificationResolver()
            result = await resolver.resolve(
                "Complex question here?",
                "",
                "Some context.",
            )
            assert result.answered is False
            assert result.source == "escalated"

    @pytest.mark.asyncio
    async def test_no_context_escalates(self):
        """No boundaries and no ADR context should escalate without LLM call."""
        resolver = ClarificationResolver()
        result = await resolver.resolve("Any question?", "", "")
        assert result.answered is False
        assert result.source == "escalated"

    @pytest.mark.asyncio
    async def test_model_config(self):
        """Resolver should use configured model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Answer here.")]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            resolver = ClarificationResolver(model="claude-haiku-4-5-20251001")
            await resolver.resolve("Q?", "", "context")

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


# ── OutputValidator ───────────────────────────────────────────


class TestOutputValidator:

    @pytest.mark.asyncio
    async def test_no_criteria_passes(self):
        """No acceptance criteria means automatic pass."""
        validator = OutputValidator()
        verdict = await validator.validate([], "Some result")
        assert verdict.outcome == "pass"
        assert "No acceptance criteria" in verdict.reasoning

    @pytest.mark.asyncio
    async def test_llm_pass(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: pass\nREASONING: All criteria met\nMISSING: none"
        )]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator()
            verdict = await validator.validate(
                ["Tests pass", "Docs updated"],
                "Implemented feature with tests and docs.",
                "3 files changed",
                ["src/feature.py", "tests/test_feature.py", "docs/README.md"],
            )
            assert verdict.outcome == "pass"
            assert verdict.missing_criteria == []

    @pytest.mark.asyncio
    async def test_llm_fail(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: fail\nREASONING: No test files found\nMISSING: Tests pass"
        )]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator()
            verdict = await validator.validate(
                ["Tests pass"],
                "Implemented feature.",
                "1 file changed",
                ["src/feature.py"],
            )
            assert verdict.outcome == "fail"
            assert "Tests pass" in verdict.missing_criteria

    @pytest.mark.asyncio
    async def test_llm_partial(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: partial\nREASONING: Tests pass but docs missing\nMISSING: Docs updated"
        )]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator()
            verdict = await validator.validate(
                ["Tests pass", "Docs updated"],
                "Implemented with tests.",
            )
            assert verdict.outcome == "partial"
            assert "Docs updated" in verdict.missing_criteria

    @pytest.mark.asyncio
    async def test_api_error_passes_by_default(self):
        """API errors should gracefully pass."""
        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator()
            verdict = await validator.validate(
                ["Tests pass"],
                "Some result.",
            )
            assert verdict.outcome == "pass"
            assert "unavailable" in verdict.reasoning.lower()

    @pytest.mark.asyncio
    async def test_model_config(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: pass\nREASONING: All good\nMISSING: none"
        )]

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator(model="claude-sonnet-4-6")
            await validator.validate(["Criteria"], "Result")

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "claude-sonnet-4-6"


class TestExecutionEvidenceValidation:
    """Test execution evidence checking in OutputValidator."""

    @pytest.mark.asyncio
    async def test_validate_passes_execution_flag(self):
        """The is_execution_story flag should reach _llm_validate."""
        validator = OutputValidator()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: pass\nREASONING: Good\nMISSING: none"
        )]
        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await validator.validate(
                ["Tests pass"], "All done", is_execution_story=True,
            )
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "execution output" in call_kwargs["system"].lower()

    @pytest.mark.asyncio
    async def test_validate_default_not_execution(self):
        """Default should be is_execution_story=False (no execution clause)."""
        validator = OutputValidator()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: pass\nREASONING: Good\nMISSING: none"
        )]
        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await validator.validate(["Tests pass"], "All done")
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "execution output" not in call_kwargs["system"].lower()

    @pytest.mark.asyncio
    async def test_execution_clause_mentions_fail(self):
        """Execution clause should instruct FAIL for missing evidence."""
        validator = OutputValidator()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: pass\nREASONING: Good\nMISSING: none"
        )]
        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            await validator.validate(
                ["Run experiment"], "Output here", is_execution_story=True,
            )
            call_kwargs = mock_client.messages.create.call_args.kwargs
            system = call_kwargs["system"]
            assert "FAIL" in system
            assert "running" in system.lower()

    @pytest.mark.asyncio
    async def test_no_criteria_skips_execution_check(self):
        """No acceptance criteria → auto-pass regardless of execution flag."""
        validator = OutputValidator()
        verdict = await validator.validate(
            [], "Some result", is_execution_story=True,
        )
        assert verdict.outcome == "pass"
        assert "No acceptance criteria" in verdict.reasoning


class TestParseVerdict:

    def test_all_fields(self):
        text = "VERDICT: fail\nREASONING: Missing tests\nMISSING: Tests pass, Docs updated"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"
        assert v.reasoning == "Missing tests"
        assert v.missing_criteria == ["Tests pass", "Docs updated"]

    def test_pass_none_missing(self):
        text = "VERDICT: pass\nREASONING: All criteria met\nMISSING: none"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "pass"
        assert v.missing_criteria == []

    def test_partial(self):
        text = "VERDICT: partial\nREASONING: Some done\nMISSING: Docs"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "partial"

    def test_malformed_defaults_fail(self):
        """Unparseable response should default to fail (fail-safe)."""
        text = "The work looks great! Everything seems fine."
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"

    def test_case_insensitive(self):
        text = "verdict: FAIL\nreasoning: Bad\nmissing: Tests"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"

    def test_extra_whitespace(self):
        text = "  VERDICT:   pass  \n  REASONING:  Good work  \n  MISSING:  none  "
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "pass"
        assert v.reasoning == "Good work"


# ── RetryEnricher ─────────────────────────────────────────────


class TestRetryEnricher:

    def setup_method(self):
        self.enricher = RetryEnricher()

    def test_basic_enrichment(self):
        result = self.enricher.enrich_instructions(
            "Original instructions here.",
            error="Test failed: assertion error",
            attempt=1,
        )
        assert "Original instructions here." in result
        assert "## Previous Attempt Feedback" in result
        assert "Attempt 1" in result
        assert "assertion error" in result

    def test_with_validation_feedback(self):
        result = self.enricher.enrich_instructions(
            "Instructions",
            error="Tests failed",
            validation_feedback="Missing test coverage for edge cases",
            attempt=2,
        )
        assert "### Validation Result" in result
        assert "Missing test coverage" in result
        assert "Attempt 2" in result

    def test_with_missing_criteria(self):
        result = self.enricher.enrich_instructions(
            "Instructions",
            missing_criteria=["Tests pass", "Docs updated"],
            attempt=1,
        )
        assert "### Unmet Acceptance Criteria" in result
        assert "- Tests pass" in result
        assert "- Docs updated" in result

    def test_no_error_no_feedback(self):
        """Even with no error/feedback, should still add the section."""
        result = self.enricher.enrich_instructions("Instructions", attempt=1)
        assert "## Previous Attempt Feedback" in result
        assert "address the issues" in result

    def test_preserves_original(self):
        """Original instructions should be at the start, unmodified."""
        original = "# Agent Instructions\n\nDo the thing.\n\n## Story\nBuild feature X."
        result = self.enricher.enrich_instructions(
            original, error="Failed", attempt=1,
        )
        assert result.startswith(original)

    def test_multiple_attempts(self):
        """Attempt number should be reflected in the section heading."""
        r1 = self.enricher.enrich_instructions("I", error="E1", attempt=1)
        r2 = self.enricher.enrich_instructions(r1, error="E2", attempt=2)
        assert "Attempt 1" in r2
        assert "Attempt 2" in r2

    def test_all_fields(self):
        result = self.enricher.enrich_instructions(
            "Instructions",
            error="Connection timeout",
            validation_feedback="Only 2 of 4 criteria met",
            missing_criteria=["Performance test", "API docs"],
            attempt=3,
        )
        assert "### Error" in result
        assert "Connection timeout" in result
        assert "### Validation Result" in result
        assert "2 of 4 criteria" in result
        assert "### Unmet Acceptance Criteria" in result
        assert "- Performance test" in result
        assert "- API docs" in result
        assert "Attempt 3" in result


# ── Engine Integration ────────────────────────────────────────


class TestEngineBlockedHandler:
    """Tests for ManagementEngine._handle_blocked()."""

    @pytest.fixture
    def engine_deps(self, tmp_path):
        """Create mock dependencies for ManagementEngine."""
        from core.daemon.models import PipelineStatus
        from core.daemon.pipeline import PipelineStore

        pool_queue = AsyncMock()
        pool_queue.provide_clarification = AsyncMock()
        pool_queue.get = AsyncMock(return_value=None)

        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        cfg = MagicMock()
        cfg.daemon_db_path = tmp_path / "test.db"
        cfg.daemon_poll_interval = 999
        cfg.daemon_max_concurrent_jobs = 1
        cfg.daemon_auto_dispatch = True
        cfg.daemon_project_filter = []
        cfg.vault_path = tmp_path / "vault"
        cfg.workspace_root = tmp_path
        # Intelligence config
        cfg.daemon_auto_resolve = True
        cfg.daemon_validate_output = True
        cfg.daemon_resolve_model = "claude-sonnet-4-6"
        cfg.daemon_validate_model = "claude-opus-4-6"
        cfg.daemon_max_daemon_retries = 1
        cfg.daemon_resolve_confidence_threshold = 0.7

        return cfg, pool_queue, pool_events

    @pytest.fixture
    async def engine(self, engine_deps, tmp_path):
        from core.daemon.engine import ManagementEngine

        cfg, pool_queue, pool_events = engine_deps
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=tmp_path / "vault",
        )
        await eng.store.initialize()
        return eng

    @pytest.mark.asyncio
    async def test_handle_blocked_auto_resolves(self, engine, engine_deps):
        """Blocked job with matching boundaries should auto-resolve."""
        _, pool_queue, pool_events = engine_deps

        # Mock the resolver to return a successful resolution
        mock_resolver = AsyncMock()
        mock_resolver.resolve = AsyncMock(return_value=Resolution(
            answered=True, answer="Use the existing pattern.", confidence=0.85, source="mechanical",
        ))
        mock_resolver._confidence_threshold = 0.7
        if engine._intelligence:
            engine._intelligence["resolver"] = mock_resolver

        # Add a pipeline item in DISPATCHED state
        from core.daemon.models import PipelineStatus
        await engine.store.add(
            story_id="story-test-001",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await engine.store.list_items()
        item = items[0]
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-abc")

        # Call the blocked handler
        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id="job-abc",
            data={"question": "Should I use pattern A or B?"},
        )
        await engine._handle_pool_event(event)

        # If intelligence is wired, check resolution
        if engine._intelligence:
            pool_queue.provide_clarification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_blocked_escalates(self, engine, engine_deps):
        """Unresolvable question should escalate."""
        _, pool_queue, pool_events = engine_deps

        mock_resolver = AsyncMock()
        mock_resolver.resolve = AsyncMock(return_value=Resolution(
            answered=False, source="escalated",
        ))
        mock_resolver._confidence_threshold = 0.7
        if engine._intelligence:
            engine._intelligence["resolver"] = mock_resolver

        from core.daemon.models import PipelineStatus
        await engine.store.add(
            story_id="story-test-002",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await engine.store.list_items()
        item = items[0]
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-def")

        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id="job-def",
            data={"question": "Completely unclear question"},
        )
        await engine._handle_pool_event(event)

        # Should NOT provide clarification
        pool_queue.provide_clarification.assert_not_called()
        # Item should be BLOCKED
        updated = await engine.store.get_by_job("job-def")
        assert updated.status == PipelineStatus.BLOCKED


class TestEngineCompleteHandler:
    """Tests for ManagementEngine._handle_complete()."""

    @pytest.fixture
    async def engine(self, tmp_path):
        from core.daemon.engine import ManagementEngine

        pool_queue = AsyncMock()
        pool_queue.get = AsyncMock(return_value=MagicMock(result="Tests pass. All done."))
        pool_queue.submit = AsyncMock()

        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        cfg = MagicMock()
        cfg.daemon_db_path = tmp_path / "test.db"
        cfg.daemon_poll_interval = 999
        cfg.daemon_max_concurrent_jobs = 1
        cfg.daemon_auto_dispatch = True
        cfg.daemon_project_filter = []
        cfg.vault_path = tmp_path / "vault"
        cfg.workspace_root = tmp_path
        cfg.daemon_auto_resolve = True
        cfg.daemon_validate_output = True
        cfg.daemon_resolve_model = "claude-sonnet-4-6"
        cfg.daemon_validate_model = "claude-opus-4-6"
        cfg.daemon_max_daemon_retries = 1
        cfg.daemon_resolve_confidence_threshold = 0.7

        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=tmp_path / "vault",
        )
        await eng.store.initialize()
        return eng, pool_queue, pool_events

    @pytest.mark.asyncio
    async def test_complete_pass_advances_review(self, engine):
        eng, pool_queue, pool_events = engine

        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=Verdict(
            outcome="pass", reasoning="All criteria met",
        ))
        if eng._intelligence:
            eng._intelligence["validator"] = mock_validator

        from core.daemon.models import PipelineStatus
        await eng.store.add(
            story_id="story-test-003",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await eng.store.list_items()
        item = items[0]
        await eng.store.advance(item.id, PipelineStatus.READY)
        await eng.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-pass")

        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-pass",
            data={"workspace_id": "ws-123"},
        )
        await eng._handle_pool_event(event)

        updated = await eng.store.get_by_job("job-pass")
        # Job passes review → _handle_review auto-merges non-code jobs
        assert updated.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_complete_fail_retries(self, engine):
        eng, pool_queue, pool_events = engine

        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=Verdict(
            outcome="fail", reasoning="No tests", missing_criteria=["Tests pass"],
        ))
        if eng._intelligence:
            eng._intelligence["validator"] = mock_validator

        from core.daemon.models import PipelineStatus
        await eng.store.add(
            story_id="story-test-004",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await eng.store.list_items()
        item = items[0]
        await eng.store.advance(item.id, PipelineStatus.READY)
        await eng.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-fail")

        # Mock story details for rebuild
        mock_te = MagicMock()
        mock_te.get_items_batch = MagicMock(return_value={
            "story-test-004": {
                "id": "story-test-004",
                "title": "Test story",
                "description": "Do the thing",
                "acceptance_criteria": ["Tests pass"],
                "assignee": "code",
            },
        })
        eng._task_engine = mock_te

        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-fail",
            data={"workspace_id": "ws-456"},
        )
        await eng._handle_pool_event(event)

        # If intelligence is wired, should have retried (submitted new job)
        if eng._intelligence:
            pool_queue.submit.assert_called()


class TestEngineFailedHandler:
    """Tests for ManagementEngine._handle_failed()."""

    @pytest.fixture
    async def engine(self, tmp_path):
        from core.daemon.engine import ManagementEngine

        pool_queue = AsyncMock()
        pool_queue.submit = AsyncMock()

        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        cfg = MagicMock()
        cfg.daemon_db_path = tmp_path / "test.db"
        cfg.daemon_poll_interval = 999
        cfg.daemon_max_concurrent_jobs = 1
        cfg.daemon_auto_dispatch = True
        cfg.daemon_project_filter = []
        cfg.vault_path = tmp_path / "vault"
        cfg.workspace_root = tmp_path
        cfg.daemon_auto_resolve = True
        cfg.daemon_validate_output = True
        cfg.daemon_resolve_model = "claude-sonnet-4-6"
        cfg.daemon_validate_model = "claude-opus-4-6"
        cfg.daemon_max_daemon_retries = 1
        cfg.daemon_resolve_confidence_threshold = 0.7

        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=tmp_path / "vault",
        )
        await eng.store.initialize()

        # Mock story details
        mock_te = MagicMock()
        mock_te.get_items_batch = MagicMock(return_value={
            "story-fail-001": {
                "id": "story-fail-001",
                "title": "Failing story",
                "description": "This keeps failing",
                "acceptance_criteria": ["It works"],
                "assignee": "code",
            },
        })
        mock_te.update_item = MagicMock()
        eng._task_engine = mock_te

        return eng, pool_queue, pool_events

    @pytest.mark.asyncio
    async def test_failed_retries_under_limit(self, engine):
        eng, pool_queue, _ = engine

        from core.daemon.models import PipelineStatus
        await eng.store.add(
            story_id="story-fail-001",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await eng.store.list_items()
        item = items[0]
        await eng.store.advance(item.id, PipelineStatus.READY)
        await eng.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-err1")

        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id="job-err1",
            data={"error": "Agent crashed"},
        )
        await eng._handle_pool_event(event)

        if eng._intelligence:
            # Should have submitted a new enriched job
            pool_queue.submit.assert_called()

    @pytest.mark.asyncio
    async def test_failed_exhausted_stays_failed(self, engine):
        eng, pool_queue, pool_events = engine

        from core.daemon.models import PipelineStatus
        await eng.store.add(
            story_id="story-fail-001",
            project_id="proj-test",
            priority=1,
            assignee="code",
        )
        items = await eng.store.list_items()
        item = items[0]
        await eng.store.advance(item.id, PipelineStatus.READY)
        await eng.store.advance(
            item.id, PipelineStatus.DISPATCHED, job_id="job-err2",
            daemon_retries=1,  # already at limit
        )

        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id="job-err2",
            data={"error": "Agent crashed again"},
        )
        await eng._handle_pool_event(event)

        updated = await eng.store.get_by_job("job-err2")
        assert updated.status == PipelineStatus.FAILED


# ── Event Types ───────────────────────────────────────────────


class TestNewEventTypes:

    def test_daemon_escalation_exists(self):
        from core.pool.events import PoolEventType
        assert PoolEventType.DAEMON_ESCALATION == "daemon_escalation"

    def test_daemon_auto_resolved_exists(self):
        from core.pool.events import PoolEventType
        assert PoolEventType.DAEMON_AUTO_RESOLVED == "daemon_auto_resolved"

    def test_escalation_event_serializes(self):
        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.DAEMON_ESCALATION,
            job_id="job-123",
            data={"question": "What to do?", "story_id": "story-001"},
        )
        d = event.to_dict()
        assert d["event_type"] == "daemon_escalation"
        assert d["question"] == "What to do?"

    def test_auto_resolved_event_serializes(self):
        from core.pool.events import PoolEvent, PoolEventType
        event = PoolEvent(
            type=PoolEventType.DAEMON_AUTO_RESOLVED,
            job_id="job-456",
            data={"question": "Q?", "answer": "A.", "source": "mechanical"},
        )
        d = event.to_dict()
        assert d["event_type"] == "daemon_auto_resolved"
        assert d["answer"] == "A."


# ── Story 2: Robustness Tests ─────────────────────────────────


class TestSafeResponseText:
    """Tests for _safe_response_text helper."""

    def test_valid_response(self):
        response = MagicMock()
        response.content = [MagicMock(text="  Hello world  ")]
        assert _safe_response_text(response) == "Hello world"

    def test_empty_content(self):
        response = MagicMock()
        response.content = []
        assert _safe_response_text(response) is None

    def test_none_content(self):
        response = MagicMock()
        response.content = None
        assert _safe_response_text(response) is None

    def test_non_text_block(self):
        block = MagicMock(spec=[])  # no attributes
        response = MagicMock()
        response.content = [block]
        assert _safe_response_text(response) is None


class TestParseVerdictRobustness:
    """Additional _parse_verdict edge cases."""

    def test_missing_verdict_line_defaults_fail(self):
        text = "Some narrative about the work being great."
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"

    def test_empty_verdict_value_defaults_fail(self):
        text = "VERDICT:\nREASONING: Something\nMISSING: none"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"

    def test_invalid_verdict_value_defaults_fail(self):
        text = "VERDICT: unknown\nREASONING: Something"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"

    def test_multi_line_reasoning(self):
        text = (
            "VERDICT: pass\n"
            "REASONING: The implementation looks good.\n"
            "All tests are passing and coverage is adequate.\n"
            "Edge cases are handled properly.\n"
            "MISSING: none"
        )
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "pass"
        assert "implementation looks good" in v.reasoning
        assert "Edge cases" in v.reasoning

    def test_missing_with_trailing_comma(self):
        text = "VERDICT: fail\nREASONING: Missing stuff\nMISSING: Tests, Docs,"
        v = OutputValidator._parse_verdict(text, [])
        assert v.missing_criteria == ["Tests", "Docs"]

    def test_multiple_verdict_lines_last_wins(self):
        text = "VERDICT: pass\nVERDICT: fail\nREASONING: Changed mind"
        v = OutputValidator._parse_verdict(text, [])
        assert v.outcome == "fail"


class TestLLMResolveRobustness:
    """Tests for _llm_resolve response handling."""

    @pytest.mark.asyncio
    async def test_empty_content_escalates(self):
        """Empty response content should cause graceful fallback."""
        mock_response = MagicMock()
        mock_response.content = []

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            resolver = ClarificationResolver()
            result = await resolver.resolve("Some question?", "", "Some context.")
            assert result.answered is False
            assert result.source == "escalated"

    @pytest.mark.asyncio
    async def test_cannot_answer_case_variations(self):
        """Various case forms of CANNOT_ANSWER should all be detected."""
        for text in ["CANNOT_ANSWER", "Cannot Answer", "cannot_answer", "Cannot answer from context"]:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text=text)]

            with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
                mock_client = AsyncMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_anthropic.AsyncAnthropic.return_value = mock_client

                resolver = ClarificationResolver()
                result = await resolver.resolve("Q?", "", "context")
                assert result.answered is False, f"Failed for text: {text!r}"
                assert result.source == "llm"


class TestLLMValidateRobustness:
    """Tests for _llm_validate response handling."""

    @pytest.mark.asyncio
    async def test_empty_content_passes_by_default(self):
        """Empty API response should fall through to graceful pass."""
        mock_response = MagicMock()
        mock_response.content = []

        with patch("core.daemon.intelligence._anthropic") as mock_anthropic:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            validator = OutputValidator()
            verdict = await validator.validate(["Tests pass"], "Some result.")
            assert verdict.outcome == "pass"
            assert "unavailable" in verdict.reasoning.lower()


# ── Story 4: Handler Edge Case Tests ──────────────────────────


class TestHandlerEdgeCases:
    """Tests for engine handler edge cases."""

    @pytest.fixture
    async def engine_with_intelligence(self, tmp_path):
        """Engine with mocked intelligence components."""
        from core.daemon.engine import ManagementEngine
        from core.daemon.models import PipelineStatus

        pool_queue = AsyncMock()
        pool_queue.provide_clarification = AsyncMock()
        pool_queue.get = AsyncMock(return_value=MagicMock(result="Done."))
        pool_queue.submit = AsyncMock()

        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        cfg = MagicMock()
        cfg.daemon_db_path = tmp_path / "edge.db"
        cfg.daemon_poll_interval = 999
        cfg.daemon_max_concurrent_jobs = 1
        cfg.daemon_auto_dispatch = True
        cfg.daemon_project_filter = []
        cfg.vault_path = tmp_path / "vault"
        cfg.workspace_root = tmp_path
        cfg.daemon_auto_resolve = True
        cfg.daemon_validate_output = True
        cfg.daemon_resolve_model = "claude-sonnet-4-6"
        cfg.daemon_validate_model = "claude-opus-4-6"
        cfg.daemon_max_daemon_retries = 2
        cfg.daemon_resolve_confidence_threshold = 0.7

        eng = ManagementEngine(
            config=cfg, pool_queue=pool_queue, pool_events=pool_events,
            vault_path=tmp_path / "vault",
        )
        await eng.store.initialize()

        # Mock task engine for story details
        mock_te = MagicMock()
        mock_te.get_items_batch = MagicMock(return_value={
            "story-edge-001": {
                "id": "story-edge-001", "title": "Edge case story",
                "description": "Test edge", "acceptance_criteria": ["Tests pass"],
                "assignee": "code",
            },
            "story-edge-002": {
                "id": "story-edge-002", "title": "No AC story",
                "description": "No criteria", "assignee": "code",
            },
        })
        mock_te.update_item = MagicMock()
        eng._task_engine = mock_te

        return eng, pool_queue, pool_events

    async def _make_dispatched(self, eng, story_id, job_id, **kw):
        """Helper to create a DISPATCHED pipeline item."""
        from core.daemon.models import PipelineStatus
        await eng.store.add(story_id=story_id, project_id="proj-test", priority=1, assignee="code")
        items = await eng.store.list_items()
        item = [i for i in items if i.story_id == story_id][0]
        await eng.store.advance(item.id, PipelineStatus.READY)
        await eng.store.advance(item.id, PipelineStatus.DISPATCHED, job_id=job_id, **kw)
        return item

    @pytest.mark.asyncio
    async def test_blocked_empty_question_escalates(self, engine_with_intelligence):
        """JOB_BLOCKED with empty question should escalate without calling resolver."""
        eng, pool_queue, pool_events = engine_with_intelligence
        item = await self._make_dispatched(eng, "story-edge-001", "job-empty-q")

        from core.pool.events import PoolEvent, PoolEventType
        await eng._handle_pool_event(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id="job-empty-q",
            data={"question": ""},
        ))

        pool_queue.provide_clarification.assert_not_called()
        updated = await eng.store.get_by_job("job-empty-q")
        assert updated.status.value == "blocked"

    @pytest.mark.asyncio
    async def test_blocked_auto_resolve_disabled(self, engine_with_intelligence):
        """With auto_resolve=False, should skip resolver and escalate."""
        eng, pool_queue, pool_events = engine_with_intelligence
        eng._auto_resolve = False
        item = await self._make_dispatched(eng, "story-edge-001", "job-no-ar")

        from core.pool.events import PoolEvent, PoolEventType
        await eng._handle_pool_event(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id="job-no-ar",
            data={"question": "Should I use pattern X?"},
        ))

        pool_queue.provide_clarification.assert_not_called()
        # Should have emitted DAEMON_ESCALATION
        escalation_calls = [
            c for c in pool_events.emit.call_args_list
            if c[0][0].type == PoolEventType.DAEMON_ESCALATION
        ]
        assert len(escalation_calls) >= 1

    @pytest.mark.asyncio
    async def test_complete_no_acceptance_criteria(self, engine_with_intelligence):
        """Story with no AC should skip validation and go directly to REVIEW."""
        eng, pool_queue, pool_events = engine_with_intelligence
        # Override task engine to return story with no AC
        eng._task_engine.get_items_batch = MagicMock(return_value={
            "story-edge-002": {
                "id": "story-edge-002", "title": "No AC",
                "description": "No criteria", "assignee": "code",
            },
        })
        item = await self._make_dispatched(eng, "story-edge-002", "job-no-ac")

        from core.pool.events import PoolEvent, PoolEventType
        await eng._handle_pool_event(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-no-ac",
            data={"workspace_id": "ws-no-ac"},
        ))

        updated = await eng.store.get_by_job("job-no-ac")
        # No AC → skip validation → _handle_review auto-merges (mock job_type != "code")
        assert updated.status.value == "merged"

    @pytest.mark.asyncio
    async def test_complete_partial_verdict_escalates(self, engine_with_intelligence):
        """Partial verdict should advance to REVIEW AND emit escalation."""
        eng, pool_queue, pool_events = engine_with_intelligence

        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value=Verdict(
            outcome="partial", reasoning="Tests pass but docs missing",
            missing_criteria=["Docs updated"],
        ))
        if eng._intelligence:
            eng._intelligence["validator"] = mock_validator

        item = await self._make_dispatched(eng, "story-edge-001", "job-partial")

        from core.pool.events import PoolEvent, PoolEventType
        await eng._handle_pool_event(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-partial",
            data={"workspace_id": "ws-partial"},
        ))

        updated = await eng.store.get_by_job("job-partial")
        # Partial → advances to review → _handle_review auto-merges (mock job_type != "code")
        assert updated.status.value == "merged"

        # Should have emitted escalation
        if eng._intelligence:
            escalation_calls = [
                c for c in pool_events.emit.call_args_list
                if c[0][0].type == PoolEventType.DAEMON_ESCALATION
            ]
            assert len(escalation_calls) >= 1

    @pytest.mark.asyncio
    async def test_retry_state_transitions(self, engine_with_intelligence):
        """Retry should transition DISPATCHED → FAILED → READY → DISPATCHED with new job_id."""
        eng, pool_queue, pool_events = engine_with_intelligence
        item = await self._make_dispatched(eng, "story-edge-001", "job-retry-orig")

        from core.pool.events import PoolEvent, PoolEventType
        await eng._handle_pool_event(PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id="job-retry-orig",
            data={"error": "First attempt failed"},
        ))

        if eng._intelligence:
            # Should have submitted a new job
            pool_queue.submit.assert_called()
            # Item should be DISPATCHED with a new job_id
            updated = await eng.store.get(item.id)
            assert updated.status.value == "dispatched"
            assert updated.job_id != "job-retry-orig"
            assert updated.daemon_retries == 1


# ── Notifier embed tests ──────────────────────────────────────


class TestNotifierEmbeds:
    """Tests for Discord embed formatting of daemon events."""

    def test_escalation_embed(self):
        from core.pool.events import PoolEvent, PoolEventType
        from core.pool.notifiers import DiscordWebhookNotifier

        event = PoolEvent(
            type=PoolEventType.DAEMON_ESCALATION,
            job_id="job-esc-001",
            data={
                "question": "What pattern should I use?",
                "story_id": "story-test-001",
                "reason": "Could not resolve (source=escalated, confidence=0.42)",
            },
        )
        embed = DiscordWebhookNotifier._build_embed(event)
        assert "Escalation" in embed["title"]
        assert "story-test-001" in embed["description"]
        assert "What pattern" in embed["description"]
        assert embed["color"] == 0xE67E22

    def test_auto_resolved_embed(self):
        from core.pool.events import PoolEvent, PoolEventType
        from core.pool.notifiers import DiscordWebhookNotifier

        event = PoolEvent(
            type=PoolEventType.DAEMON_AUTO_RESOLVED,
            job_id="job-res-001",
            data={
                "question": "Which framework?",
                "answer": "Use the existing pattern.",
                "source": "mechanical",
                "confidence": 0.85,
                "story_id": "story-test-002",
            },
        )
        embed = DiscordWebhookNotifier._build_embed(event)
        assert "Resolved" in embed["title"]
        assert "mechanical" in embed["description"]
        assert "85%" in embed["description"]
        assert embed["color"] == 0x27AE60


# ── Config tests ──────────────────────────────────────────────


class TestConfigPhase3Fields:
    """Tests for Phase 3 config fields in GrimConfig."""

    def test_defaults(self):
        from core.config import GrimConfig
        cfg = GrimConfig()
        assert cfg.daemon_auto_resolve is True
        assert cfg.daemon_validate_output is True
        assert cfg.daemon_max_daemon_retries == 1
        assert cfg.daemon_resolve_model == "claude-sonnet-4-6"
        assert cfg.daemon_validate_model == "claude-opus-4-6"
        assert cfg.daemon_resolve_confidence_threshold == 0.7

    def test_yaml_override(self, tmp_path):
        import yaml
        from core.config import load_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "grim.yaml"
        config_file.write_text(yaml.dump({
            "daemon": {
                "auto_resolve": False,
                "validate_output": False,
                "max_daemon_retries": 3,
                "resolve_model": "claude-haiku-4-5-20251001",
                "validate_model": "claude-sonnet-4-6",
                "resolve_confidence_threshold": 0.5,
            },
        }), encoding="utf-8")

        cfg = load_config(config_path=config_file, grim_root=tmp_path)
        assert cfg.daemon_auto_resolve is False
        assert cfg.daemon_validate_output is False
        assert cfg.daemon_max_daemon_retries == 3
        assert cfg.daemon_resolve_model == "claude-haiku-4-5-20251001"
        assert cfg.daemon_validate_model == "claude-sonnet-4-6"
        assert cfg.daemon_resolve_confidence_threshold == 0.5


# ── target_repo inference ────────────────────────────────────────


class TestInferTargetRepo:
    """Tests for ManagementEngine._PROJECT_REPO_MAP and _infer_target_repo."""

    def test_proj_grim(self):
        from core.daemon.engine import ManagementEngine
        assert ManagementEngine._PROJECT_REPO_MAP["proj-grim"] == "GRIM"

    def test_proj_charizard(self):
        from core.daemon.engine import ManagementEngine
        assert ManagementEngine._PROJECT_REPO_MAP["proj-charizard"] == "GRIM"

    def test_proj_mewtwo(self):
        from core.daemon.engine import ManagementEngine
        assert ManagementEngine._PROJECT_REPO_MAP["proj-mewtwo"] == "GRIM"

    def test_proj_dft(self):
        from core.daemon.engine import ManagementEngine
        assert ManagementEngine._PROJECT_REPO_MAP["proj-dft"] == "dawn-field-theory"

    def test_unknown_project(self):
        from core.daemon.engine import ManagementEngine
        assert ManagementEngine._PROJECT_REPO_MAP.get("proj-unknown") is None


class TestHandlePoolEventJobReview:
    """Tests for JOB_REVIEW event handling in daemon."""

    @pytest.mark.asyncio
    async def test_job_review_updates_workspace_id(self):
        """JOB_REVIEW event should update workspace_id on pipeline item."""
        from core.daemon.engine import ManagementEngine
        from core.daemon.models import PipelineStatus
        from core.pool.events import PoolEvent, PoolEventType

        engine = ManagementEngine.__new__(ManagementEngine)

        # Mock store
        mock_item = MagicMock()
        mock_item.id = "item-1"
        mock_item.story_id = "story-001"
        mock_item.status = PipelineStatus.REVIEW
        mock_item.workspace_id = None

        engine._store = AsyncMock()
        engine._store.get_by_job = AsyncMock(return_value=mock_item)
        engine._store.get = AsyncMock(return_value=mock_item)
        engine._store.update_fields = AsyncMock()
        engine._health = MagicMock()
        engine._health.record_error = MagicMock()

        event = PoolEvent(
            type=PoolEventType.JOB_REVIEW,
            job_id="job-abc",
            data={"workspace_id": "ws-review-123"},
        )

        await engine._handle_pool_event(event)

        engine._store.update_fields.assert_called_once_with(
            "item-1", workspace_id="ws-review-123",
        )

    @pytest.mark.asyncio
    async def test_job_review_skips_if_workspace_already_set(self):
        """JOB_REVIEW should not update if item already has workspace_id."""
        from core.daemon.engine import ManagementEngine
        from core.daemon.models import PipelineStatus
        from core.pool.events import PoolEvent, PoolEventType

        engine = ManagementEngine.__new__(ManagementEngine)

        mock_item = MagicMock()
        mock_item.id = "item-1"
        mock_item.story_id = "story-001"
        mock_item.status = PipelineStatus.REVIEW
        mock_item.workspace_id = "ws-already-set"

        engine._store = AsyncMock()
        engine._store.get_by_job = AsyncMock(return_value=mock_item)
        engine._store.get = AsyncMock(return_value=mock_item)
        engine._store.update_fields = AsyncMock()
        engine._health = MagicMock()
        engine._health.record_error = MagicMock()

        event = PoolEvent(
            type=PoolEventType.JOB_REVIEW,
            job_id="job-abc",
            data={"workspace_id": "ws-new"},
        )

        await engine._handle_pool_event(event)

        # Should NOT call update_fields since workspace_id already set
        engine._store.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_complete_passes_workspace_id(self):
        """JOB_COMPLETE data should include workspace_id for _handle_complete."""
        from core.daemon.engine import ManagementEngine
        from core.daemon.models import PipelineStatus
        from core.pool.events import PoolEvent, PoolEventType

        engine = ManagementEngine.__new__(ManagementEngine)

        mock_item = MagicMock()
        mock_item.id = "item-1"
        mock_item.story_id = "story-001"
        mock_item.status = PipelineStatus.DISPATCHED
        mock_item.daemon_retries = 0

        engine._store = AsyncMock()
        engine._store.get_by_job = AsyncMock(return_value=mock_item)
        engine._store.advance = AsyncMock()
        engine._intelligence = None
        engine._validate_output = False
        engine._health = MagicMock()
        engine._health.record_error = MagicMock()

        # Mock _handle_review to capture what workspace_id is passed
        captured_ws_id = []
        async def mock_review(item, ws_id):
            captured_ws_id.append(ws_id)
        engine._handle_review = mock_review

        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-abc",
            data={
                "workspace_id": "ws-from-complete",
                "result_preview": "done",
                "cost_usd": 0.05,
            },
        )

        await engine._handle_pool_event(event)

        assert captured_ws_id == ["ws-from-complete"]
