"""Tests for Tier 3 live integration eval system.

Covers:
- Sandbox MCP session wrapper
- Sandbox contextvar activation
- Trace parser
- Judge system (routing, quality, domain, code, efficiency)
- Ground truth loader
- Schema models
- Executor dataset loading
- QA MCP server tool definitions
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Sandbox tests
# ---------------------------------------------------------------------------


class TestSandboxMCPSession:
    """Test the SandboxMCPSession wrapper."""

    def test_write_tools_list(self):
        from core.tools.sandbox import WRITE_TOOLS

        assert "kronos_create" in WRITE_TOOLS
        assert "kronos_update" in WRITE_TOOLS
        assert "kronos_memory_update" in WRITE_TOOLS
        assert "kronos_note_append" in WRITE_TOOLS
        assert "kronos_task_create" in WRITE_TOOLS
        assert "kronos_calendar_add" in WRITE_TOOLS
        # Reads should NOT be in write tools
        assert "kronos_search" not in WRITE_TOOLS
        assert "kronos_get" not in WRITE_TOOLS
        assert "kronos_list" not in WRITE_TOOLS
        assert "kronos_graph" not in WRITE_TOOLS

    @pytest.mark.asyncio
    async def test_blocks_write_tools(self):
        from core.tools.sandbox import SandboxMCPSession

        mock_session = AsyncMock()
        sandbox = SandboxMCPSession(mock_session)

        result = await sandbox.call_tool("kronos_create", {"id": "test-fdo"})
        mock_session.call_tool.assert_not_called()
        assert hasattr(result, "content")
        text = json.loads(result.content[0].text)
        assert text["sandbox"] is True
        assert text["id"] == "test-fdo"

    @pytest.mark.asyncio
    async def test_passes_through_read_tools(self):
        from core.tools.sandbox import SandboxMCPSession

        mock_session = AsyncMock()
        mock_session.call_tool.return_value = "search results"
        sandbox = SandboxMCPSession(mock_session)

        result = await sandbox.call_tool("kronos_search", {"query": "test"})
        mock_session.call_tool.assert_called_once_with("kronos_search", {"query": "test"})
        assert result == "search results"

    @pytest.mark.asyncio
    async def test_blocked_calls_audit_trail(self):
        from core.tools.sandbox import SandboxMCPSession

        mock_session = AsyncMock()
        sandbox = SandboxMCPSession(mock_session)

        await sandbox.call_tool("kronos_create", {"id": "a"})
        await sandbox.call_tool("kronos_update", {"id": "b"})
        await sandbox.call_tool("kronos_search", {"query": "c"})  # read — not blocked

        assert len(sandbox.blocked_calls) == 2
        assert sandbox.blocked_calls[0]["method"] == "kronos_create"
        assert sandbox.blocked_calls[1]["method"] == "kronos_update"

    @pytest.mark.asyncio
    async def test_all_write_tools_return_synthetic(self):
        from core.tools.sandbox import WRITE_TOOLS, SandboxMCPSession

        mock_session = AsyncMock()
        sandbox = SandboxMCPSession(mock_session)

        for tool in WRITE_TOOLS:
            result = await sandbox.call_tool(tool, {})
            text = json.loads(result.content[0].text)
            assert text["sandbox"] is True, f"{tool} should return sandbox=True"

    def test_proxies_other_attributes(self):
        from core.tools.sandbox import SandboxMCPSession

        mock_session = MagicMock()
        mock_session.some_attr = "value"
        sandbox = SandboxMCPSession(mock_session)
        assert sandbox.some_attr == "value"


class TestSandboxContextVar:
    """Test the contextvar-based sandbox activation."""

    def test_default_inactive(self):
        from core.tools.sandbox import is_sandbox_active
        assert is_sandbox_active() is False

    def test_activate_deactivate(self):
        from core.tools.sandbox import activate_sandbox, deactivate_sandbox, is_sandbox_active

        activate_sandbox()
        assert is_sandbox_active() is True
        deactivate_sandbox()
        assert is_sandbox_active() is False

    def test_get_blocked_calls_default_empty(self):
        from core.tools.sandbox import get_blocked_calls
        assert get_blocked_calls() == []

    def test_activate_resets_blocked_calls(self):
        from core.tools.sandbox import activate_sandbox, get_blocked_calls
        activate_sandbox()
        assert get_blocked_calls() == []


# ---------------------------------------------------------------------------
# Trace parser tests
# ---------------------------------------------------------------------------


class TestTraceParser:
    """Test the trace parser."""

    def test_empty_events(self):
        from eval.tier3.trace import TraceParser

        result = TraceParser.parse([])
        assert result.routing_path == []
        assert result.subgraph is None
        assert result.loop_count == 0
        assert result.metrics.total_tokens == 0

    def test_node_lifecycle(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "node", "node": "identity", "text": "→ identity", "action": "start", "ms": 0},
            {"type": "trace", "cat": "node", "node": "identity", "text": "✓ identity (100ms)", "action": "end", "ms": 100, "duration_ms": 100},
            {"type": "trace", "cat": "node", "node": "memory", "text": "→ memory", "action": "start", "ms": 100},
            {"type": "trace", "cat": "node", "node": "memory", "text": "✓ memory (150ms)", "action": "end", "ms": 250, "duration_ms": 150},
        ]
        result = TraceParser.parse(events)
        assert "identity" in result.routing_path
        assert "memory" in result.routing_path
        assert result.metrics.node_times.get("identity") == 100
        assert result.metrics.node_times.get("memory") == 150

    def test_subgraph_detection(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "node", "text": "Node started: conversation", "action": "start", "ms": 0},
            {"type": "trace", "cat": "node", "text": "Node ended: conversation", "action": "end", "ms": 500},
        ]
        result = TraceParser.parse(events)
        assert result.subgraph == "conversation"

    def test_loop_counting(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "node", "text": "Node started: response_generator", "action": "start", "ms": 0},
            {"type": "trace", "cat": "node", "text": "Node ended: response_generator", "action": "end", "ms": 100},
            {"type": "trace", "cat": "node", "text": "Node started: response_generator", "action": "start", "ms": 200},
            {"type": "trace", "cat": "node", "text": "Node ended: response_generator", "action": "end", "ms": 300},
        ]
        result = TraceParser.parse(events)
        assert result.loop_count == 2

    def test_llm_token_tracking(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "node", "node": "companion", "text": "→ companion", "action": "start", "ms": 0},
            {"type": "trace", "cat": "llm", "text": "LLM call started", "action": "start", "ms": 10},
            {"type": "trace", "cat": "llm", "text": "LLM call ended", "action": "end", "ms": 500,
             "detail": {"tokens": {"input_tokens": 1000, "output_tokens": 200}}},
            {"type": "trace", "cat": "node", "node": "companion", "text": "✓ companion (510ms)", "action": "end", "ms": 510, "duration_ms": 510},
        ]
        result = TraceParser.parse(events)
        assert result.metrics.input_tokens == 1000
        assert result.metrics.output_tokens == 200
        assert result.metrics.total_tokens == 1200
        assert result.metrics.llm_call_count == 1

    def test_tool_call_tracking(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "llm", "text": "LLM call ended", "action": "end", "ms": 100,
             "tool_calls": ["kronos_search", "kronos_get"]},
        ]
        result = TraceParser.parse(events)
        assert "kronos_search" in result.tools_called
        assert "kronos_get" in result.tools_called
        assert result.metrics.tool_call_count == 2

    def test_agent_traversal(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "node", "text": "identity", "action": "start", "ms": 0},
            {"type": "trace", "cat": "node", "text": "memory", "action": "start", "ms": 100},
            {"type": "trace", "cat": "node", "text": "companion_router", "action": "start", "ms": 200},
            {"type": "trace", "cat": "node", "text": "conversation", "action": "start", "ms": 300},
        ]
        result = TraceParser.parse(events)
        assert result.metrics.agent_traversal == ["identity", "memory", "companion_router", "conversation"]

    def test_cost_estimation(self):
        from eval.tier3.trace import TraceParser

        events = [
            {"type": "trace", "cat": "llm", "text": "LLM call started", "action": "start", "ms": 0},
            {"type": "trace", "cat": "llm", "text": "LLM call ended", "action": "end", "ms": 100,
             "detail": {"tokens": {"input_tokens": 1_000_000, "output_tokens": 100_000}}},
        ]
        result = TraceParser.parse(events)
        # 1M input * $3/1M + 100K output * $15/1M = $3 + $1.50 = $4.50
        assert result.metrics.cost_estimate_usd == pytest.approx(4.5, abs=0.01)


# ---------------------------------------------------------------------------
# Judge tests
# ---------------------------------------------------------------------------


class TestRoutingJudge:
    """Test the routing judge."""

    @pytest.mark.asyncio
    async def test_correct_routing(self):
        from eval.schema import RoutingExpectation, Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import RoutingJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
            expected_routing=RoutingExpectation(subgraph="conversation"),
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            subgraph_history=["conversation"],
            turn_results=[Tier3TurnResult(turn_index=0)],
        )

        judge = RoutingJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True
        assert judgment.score == 1.0

    @pytest.mark.asyncio
    async def test_incorrect_routing(self):
        from eval.schema import RoutingExpectation, Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import RoutingJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
            expected_routing=RoutingExpectation(subgraph="conversation"),
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            subgraph_history=["research"],
            turn_results=[Tier3TurnResult(turn_index=0)],
        )

        judge = RoutingJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False

    @pytest.mark.asyncio
    async def test_expected_tools_check(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import RoutingJudge

        case = Tier3Case(
            id="test", category="research",
            turns=[Tier3Turn(message="search")],
            expected_tools=["kronos_search"],
        )
        result = Tier3CaseResult(
            case_id="test", category="research",
            tools_called=["kronos_search", "kronos_get"],
            turn_results=[Tier3TurnResult(turn_index=0)],
        )

        judge = RoutingJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True

    @pytest.mark.asyncio
    async def test_unexpected_tools_check(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import RoutingJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
            unexpected_tools=["kronos_create"],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            tools_called=["kronos_create"],
            turn_results=[Tier3TurnResult(turn_index=0)],
        )

        judge = RoutingJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False


class TestQualityJudge:
    """Test the quality judge."""

    @pytest.mark.asyncio
    async def test_response_contains(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import QualityJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello", response_contains=["hey"])],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0, response_text="Hey there!")],
        )

        judge = QualityJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True

    @pytest.mark.asyncio
    async def test_response_excludes(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import QualityJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello", response_excludes=["error"])],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0, response_text="Something went error!")],
        )

        judge = QualityJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False

    @pytest.mark.asyncio
    async def test_no_response(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import QualityJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0, response_text="")],
        )

        judge = QualityJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False


class TestDomainJudge:
    """Test the domain accuracy judge."""

    @pytest.mark.asyncio
    async def test_no_facts_passes(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import DomainJudge

        case = Tier3Case(
            id="test", category="domain_accuracy",
            turns=[Tier3Turn(message="hello")],
        )
        result = Tier3CaseResult(
            case_id="test", category="domain_accuracy",
            turn_results=[Tier3TurnResult(turn_index=0, response_text="Hi")],
        )

        judge = DomainJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True

    @pytest.mark.asyncio
    async def test_fact_checking(self):
        from eval.schema import DomainFact, Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import DomainJudge

        case = Tier3Case(
            id="test", category="domain_accuracy",
            turns=[Tier3Turn(message="what is PAC?")],
            domain_facts=[DomainFact(claim="coherence")],
        )
        result = Tier3CaseResult(
            case_id="test", category="domain_accuracy",
            turn_results=[Tier3TurnResult(
                turn_index=0,
                response_text="PAC deals with coherence in information fields",
            )],
        )

        judge = DomainJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True


class TestCodeJudge:
    """Test the code generation judge."""

    @pytest.mark.asyncio
    async def test_no_expectations_passes(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import CodeJudge

        case = Tier3Case(
            id="test", category="code",
            turns=[Tier3Turn(message="write code")],
        )
        result = Tier3CaseResult(
            case_id="test", category="code",
            turn_results=[Tier3TurnResult(turn_index=0, response_text="def foo(): pass")],
        )

        judge = CodeJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True

    @pytest.mark.asyncio
    async def test_must_contain_check(self):
        from eval.schema import CodeExpectation, Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import CodeJudge

        case = Tier3Case(
            id="test", category="code",
            turns=[Tier3Turn(message="write code")],
            code_expectations=CodeExpectation(must_contain=["def ", "return"]),
        )
        result = Tier3CaseResult(
            case_id="test", category="code",
            turn_results=[Tier3TurnResult(
                turn_index=0,
                response_text="def factorial(n): return 1 if n <= 1 else n * factorial(n-1)",
            )],
        )

        judge = CodeJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True

    @pytest.mark.asyncio
    async def test_must_not_contain_check(self):
        from eval.schema import CodeExpectation, Tier3Case, Tier3CaseResult, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import CodeJudge

        case = Tier3Case(
            id="test", category="code",
            turns=[Tier3Turn(message="write code")],
            code_expectations=CodeExpectation(must_not_contain=["eval(", "exec("]),
        )
        result = Tier3CaseResult(
            case_id="test", category="code",
            turn_results=[Tier3TurnResult(
                turn_index=0,
                response_text="result = eval(user_input)",
            )],
        )

        judge = CodeJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False


class TestEfficiencyJudge:
    """Test the efficiency judge."""

    @pytest.mark.asyncio
    async def test_within_thresholds(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Metrics, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import EfficiencyJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0)],
            metrics=Tier3Metrics(
                total_tokens=5000,
                wall_time_ms=10000,
                turns=1,
                llm_call_count=2,
                tool_call_count=3,
            ),
        )

        judge = EfficiencyJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is True
        assert judgment.score == 1.0

    @pytest.mark.asyncio
    async def test_exceeds_token_threshold(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Metrics, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import EfficiencyJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0)],
            metrics=Tier3Metrics(total_tokens=100_000),
        )

        judge = EfficiencyJudge()
        judgment = await judge.judge(case, result)
        assert judgment.passed is False
        assert "tokens=100000 > 50000" in judgment.rationale

    @pytest.mark.asyncio
    async def test_per_case_threshold_override(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Metrics, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import EfficiencyJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
            max_tokens=200_000,  # override — more lenient
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0)],
            metrics=Tier3Metrics(total_tokens=100_000),
        )

        judge = EfficiencyJudge()
        judgment = await judge.judge(case, result)
        # 100K tokens within 200K threshold
        assert "tokens=100000 <= 200000" in str(judgment.details)

    @pytest.mark.asyncio
    async def test_metrics_in_details(self):
        from eval.schema import Tier3Case, Tier3CaseResult, Tier3Metrics, Tier3Turn, Tier3TurnResult
        from eval.tier3.judges import EfficiencyJudge

        case = Tier3Case(
            id="test", category="conversation",
            turns=[Tier3Turn(message="hello")],
        )
        result = Tier3CaseResult(
            case_id="test", category="conversation",
            turn_results=[Tier3TurnResult(turn_index=0)],
            metrics=Tier3Metrics(total_tokens=1000, cost_estimate_usd=0.005),
        )

        judge = EfficiencyJudge()
        judgment = await judge.judge(case, result)
        assert "metrics_summary" in judgment.details
        assert judgment.details["metrics_summary"]["cost_usd"] == 0.005


# ---------------------------------------------------------------------------
# Ground truth loader tests
# ---------------------------------------------------------------------------


class TestGroundTruthLoader:
    """Test the ground truth loader."""

    def test_load_nonexistent_fdo(self, tmp_path):
        from eval.tier3.ground_truth import GroundTruthLoader

        loader = GroundTruthLoader(tmp_path)
        result = loader.load_fdo("nonexistent")
        assert result is None

    def test_load_fdo_with_frontmatter(self, tmp_path):
        from eval.tier3.ground_truth import GroundTruthLoader

        # Create a mock FDO
        domain_dir = tmp_path / "physics"
        domain_dir.mkdir()
        fdo = domain_dir / "test-fdo.md"
        fdo.write_text("""---
title: Test FDO
domain: physics
status: stable
confidence: 0.9
tags: [pac, test]
related: [other-fdo]
---
# Test FDO

## Summary
- PAC regulation governs coherence
- SEC provides entropy bounds

## Details
- More details about the physics
- Another important detail here
""")

        loader = GroundTruthLoader(tmp_path)
        facts = loader.load_fdo("test-fdo")
        assert facts is not None
        assert facts.title == "Test FDO"
        assert facts.domain == "physics"
        assert facts.confidence == 0.9
        assert "pac" in facts.tags
        assert "other-fdo" in facts.related
        assert len(facts.key_facts) >= 2

    def test_load_multiple_fdos(self, tmp_path):
        from eval.tier3.ground_truth import GroundTruthLoader

        domain_dir = tmp_path / "physics"
        domain_dir.mkdir()
        for name in ["fdo-a", "fdo-b"]:
            (domain_dir / f"{name}.md").write_text(f"---\ntitle: {name}\ndomain: physics\n---\n# {name}\n## Summary\n- fact\n")

        loader = GroundTruthLoader(tmp_path)
        result = loader.load_fdos(["fdo-a", "fdo-b", "nonexistent"])
        assert "fdo-a" in result
        assert "fdo-b" in result
        assert "nonexistent" not in result


# ---------------------------------------------------------------------------
# Schema model tests
# ---------------------------------------------------------------------------


class TestTier3Schemas:
    """Test Tier 3 Pydantic models."""

    def test_tier3_case_creation(self):
        from eval.schema import Tier3Case, Tier3Turn

        case = Tier3Case(
            id="test-1",
            category="conversation",
            turns=[Tier3Turn(message="hello")],
        )
        assert case.id == "test-1"
        assert case.category.value == "conversation"
        assert len(case.turns) == 1

    def test_tier3_metrics_defaults(self):
        from eval.schema import Tier3Metrics

        m = Tier3Metrics()
        assert m.total_tokens == 0
        assert m.wall_time_ms == 0
        assert m.turns == 0
        assert m.cost_estimate_usd == 0.0

    def test_tier3_case_result(self):
        from eval.schema import Tier3CaseResult, Tier3Judgment

        result = Tier3CaseResult(
            case_id="test",
            category="conversation",
            judgments=[
                Tier3Judgment(judge="routing", score=1.0, passed=True),
                Tier3Judgment(judge="quality", score=0.8, passed=True),
            ],
        )
        assert len(result.judgments) == 2
        assert result.judgments[0].judge == "routing"

    def test_tier3_dataset_validation(self):
        from eval.schema import Tier3Dataset, Tier3Case, Tier3Turn

        ds = Tier3Dataset(
            category="test",
            cases=[Tier3Case(id="c1", category="conversation", turns=[Tier3Turn(message="hi")])],
        )
        assert ds.tier == 3
        assert len(ds.cases) == 1

    def test_routing_expectation(self):
        from eval.schema import RoutingExpectation

        exp = RoutingExpectation(subgraph="research", delegation_type="code")
        assert exp.subgraph == "research"
        assert exp.delegation_type == "code"

    def test_domain_fact(self):
        from eval.schema import DomainFact

        fact = DomainFact(claim="PAC governs coherence", fdo_source="pac-comprehensive")
        assert fact.claim == "PAC governs coherence"

    def test_code_expectation(self):
        from eval.schema import CodeExpectation

        exp = CodeExpectation(must_contain=["def ", "return"], runnable=True)
        assert len(exp.must_contain) == 2


# ---------------------------------------------------------------------------
# Executor tests (dataset loading only — no live server)
# ---------------------------------------------------------------------------


class TestTier3Executor:
    """Test executor dataset loading."""

    def test_load_datasets(self):
        from eval.config import EvalConfig
        from eval.tier3.executor import Tier3Executor

        config = EvalConfig()
        executor = Tier3Executor(config)
        datasets = executor.load_datasets()
        # Should find our 6 dataset files
        assert len(datasets) >= 1  # at least conversation should be there

    def test_load_datasets_by_category(self):
        from eval.config import EvalConfig
        from eval.tier3.executor import Tier3Executor

        config = EvalConfig()
        executor = Tier3Executor(config)
        datasets = executor.load_datasets(categories=["conversation"])
        if datasets:
            assert "conversation" in datasets
            assert len(datasets["conversation"].cases) == 5

    def test_load_nonexistent_category(self):
        from eval.config import EvalConfig
        from eval.tier3.executor import Tier3Executor

        config = EvalConfig()
        executor = Tier3Executor(config)
        datasets = executor.load_datasets(categories=["nonexistent"])
        assert len(datasets) == 0


# ---------------------------------------------------------------------------
# Judge factory test
# ---------------------------------------------------------------------------


class TestJudgeFactory:
    """Test the judge factory."""

    def test_creates_5_judges(self):
        from eval.tier3.judges import create_default_judges

        judges = create_default_judges()
        assert len(judges) == 5
        names = [j.name for j in judges]
        assert "routing" in names
        assert "quality" in names
        assert "domain" in names
        assert "code" in names
        assert "efficiency" in names


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestTier3Config:
    """Test Tier 3 eval config."""

    def test_default_config(self):
        from eval.config import EvalConfig

        config = EvalConfig()
        assert config.tier3_docker_url == "http://localhost:8080"
        assert config.tier3_ws_url == "ws://localhost:8080"
        assert config.tier3_sandbox is True
        assert config.tier3_routing_threshold == 0.90
        assert config.tier3_datasets_dir is not None

    def test_from_env_tier3_url(self):
        import os
        from eval.config import EvalConfig

        os.environ["GRIM_TIER3_URL"] = "http://remote:9000"
        try:
            config = EvalConfig.from_env()
            assert config.tier3_docker_url == "http://remote:9000"
            assert config.tier3_ws_url == "ws://remote:9000"
        finally:
            del os.environ["GRIM_TIER3_URL"]


# ---------------------------------------------------------------------------
# QA MCP server tests
# ---------------------------------------------------------------------------


class TestQAMCPServer:
    """Test QA MCP server tool definitions."""

    def test_tool_definitions_exist(self):
        from eval.qa_mcp.server import TOOLS

        names = [t.name for t in TOOLS]
        assert "qa_run_tier3" in names
        assert "qa_list_cases" in names
        assert "qa_load_ground_truth" in names
        assert "qa_inspect_trace" in names
        assert "qa_results_summary" in names

    def test_handlers_exist(self):
        from eval.qa_mcp.server import HANDLERS, TOOLS

        for tool in TOOLS:
            assert tool.name in HANDLERS, f"Missing handler for {tool.name}"

    @pytest.mark.asyncio
    async def test_list_cases_handler(self):
        from eval.qa_mcp.server import handle_qa_list_cases

        result = await handle_qa_list_cases({})
        data = json.loads(result)
        assert "total" in data
        assert "cases" in data

    @pytest.mark.asyncio
    async def test_inspect_trace_handler(self):
        from eval.qa_mcp.server import handle_qa_inspect_trace

        events = [
            {"type": "trace", "cat": "node", "text": "Node started: identity", "action": "start", "ms": 0},
            {"type": "trace", "cat": "node", "text": "Node ended: identity", "action": "end", "ms": 100},
        ]
        result = await handle_qa_inspect_trace({"events": events})
        data = json.loads(result)
        assert "routing_path" in data
        assert "metrics" in data

    @pytest.mark.asyncio
    async def test_results_summary_no_results(self):
        from eval.qa_mcp.server import handle_qa_results_summary

        result = await handle_qa_results_summary({})
        data = json.loads(result)
        assert "message" in data  # "No results yet"


# ---------------------------------------------------------------------------
# State integration test
# ---------------------------------------------------------------------------


class TestStateIntegration:
    """Test sandbox field in GrimState."""

    def test_sandbox_field_exists(self):
        from core.state import GrimState

        # TypedDict — check annotations
        assert "sandbox" in GrimState.__annotations__
