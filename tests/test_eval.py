"""Tests for the GRIM evaluation framework.

Tests the eval engine, schema, harness, grading, and dataset loading.
Does NOT test Tier 1 evaluators against real GRIM code (those are the
eval cases themselves). Tests the framework's own correctness.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml


# ── Path setup ──
import sys

_grim_root = Path(__file__).parent.parent
if str(_grim_root) not in sys.path:
    sys.path.insert(0, str(_grim_root))


# =========================================================================
# Schema tests
# =========================================================================


class TestSchemaModels:
    """Test Pydantic schema models for dataset and result types."""

    def test_tier1_case_creation(self):
        from eval.schema import ExpectedOutcome, Tier1Case

        case = Tier1Case(
            id="test-case-1",
            tags=["routing"],
            message="hello world",
            expected=ExpectedOutcome(graph_target="research"),
        )
        assert case.id == "test-case-1"
        assert case.expected.graph_target == "research"
        assert case.expected.mode is None

    def test_tier1_dataset_creation(self):
        from eval.schema import ExpectedOutcome, Tier1Case, Tier1Dataset

        dataset = Tier1Dataset(
            category="routing",
            cases=[
                Tier1Case(
                    id="tc-1",
                    message="hello",
                    expected=ExpectedOutcome(graph_target="research"),
                ),
            ],
        )
        assert dataset.tier == 1
        assert len(dataset.cases) == 1

    def test_tier2_case_single_turn(self):
        from eval.schema import Tier2Case, Tier2Expected

        case = Tier2Case(
            id="t2-1",
            turn_type="single",
            message="create an FDO",
            mock_tools={"kronos_create": {"success": True}},
            expected=Tier2Expected(tools_called=["kronos_create"]),
            grading={"understanding": "Did it work?"},
        )
        assert case.turn_type == "single"
        assert "kronos_create" in case.mock_tools

    def test_tier2_case_multi_turn(self):
        from eval.schema import ConversationTurn, Tier2Case, TurnCheckpoint

        case = Tier2Case(
            id="t2-multi",
            turn_type="multi",
            turns=[
                ConversationTurn(
                    message="first turn",
                    checkpoint=TurnCheckpoint(response_contains=["hello"]),
                ),
                ConversationTurn(message="second turn"),
            ],
        )
        assert len(case.turns) == 2
        assert case.turns[0].checkpoint is not None

    def test_case_result_creation(self):
        from eval.schema import CaseResult, CheckResult

        result = CaseResult(
            case_id="tc-1",
            tier=1,
            category="routing",
            passed=True,
            score=1.0,
            checks=[
                CheckResult(name="graph_target", expected="research", actual="research", passed=True),
            ],
        )
        assert result.passed
        assert len(result.checks) == 1

    def test_suite_result_compute_stats(self):
        from eval.schema import CaseResult, SuiteResult

        suite = SuiteResult(
            tier=1,
            category="routing",
            cases=[
                CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0, duration_ms=10),
                CaseResult(case_id="tc-2", tier=1, category="routing", passed=False, score=0.0, duration_ms=5),
                CaseResult(case_id="tc-3", tier=1, category="routing", passed=True, score=1.0, duration_ms=8),
            ],
        )
        suite.compute_stats()
        assert suite.total == 3
        assert suite.passed == 2
        assert suite.failed == 1
        assert abs(suite.score - 2 / 3) < 0.01
        assert suite.duration_ms == 23

    def test_eval_run_compute_stats(self):
        from eval.schema import CaseResult, EvalRun, SuiteResult

        run = EvalRun(
            run_id="test-run",
            timestamp="2026-03-02T00:00:00Z",
            suites=[
                SuiteResult(
                    tier=1,
                    category="routing",
                    cases=[
                        CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0),
                    ],
                ),
                SuiteResult(
                    tier=2,
                    category="memory",
                    cases=[
                        CaseResult(case_id="tc-2", tier=2, category="memory", passed=True, score=0.8),
                    ],
                ),
            ],
        )
        run.compute_stats()
        assert run.total_cases == 2
        assert run.total_passed == 2
        assert run.pass_rate == 1.0
        assert abs(run.overall_score - 0.9) < 0.01

    def test_eval_run_status_enum(self):
        from eval.schema import EvalRunStatus

        assert EvalRunStatus.RUNNING.value == "running"
        assert EvalRunStatus.COMPLETED.value == "completed"

    def test_dimension_score(self):
        from eval.schema import DimensionScore

        dim = DimensionScore(name="understanding", score=0.85, weight=1.0, rationale="Good")
        assert dim.score == 0.85
        assert dim.weight == 1.0


# =========================================================================
# Harness tests
# =========================================================================


class TestEvalToolHarness:
    """Test the EvalToolHarness mock tool system."""

    def test_harness_creation(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"tool_a": {"result": "ok"}})
        assert len(harness.calls) == 0

    def test_make_tools_creates_langchain_tools(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({
            "kronos_search": {"results": []},
            "kronos_create": {"success": True},
        })
        tools = harness.make_tools()
        assert len(tools) == 2
        assert tools[0].name == "kronos_search"
        assert tools[1].name == "kronos_create"

    def test_tool_invocation_records_call(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"my_tool": {"value": 42}})
        tools = harness.make_tools()
        result = tools[0].invoke({"query": "test"})
        assert harness.was_called("my_tool")
        assert harness.call_count("my_tool") == 1
        assert json.loads(result) == {"value": 42}

    def test_tool_string_response(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"echo": "hello world"})
        tools = harness.make_tools()
        result = tools[0].invoke({})
        assert result == "hello world"

    def test_call_sequence(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({
            "tool_a": "a",
            "tool_b": "b",
            "tool_c": "c",
        })
        tools = harness.make_tools()
        tools[0].invoke({})
        tools[2].invoke({})
        tools[1].invoke({})
        assert harness.call_sequence() == ["tool_a", "tool_c", "tool_b"]

    def test_was_not_called(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"tool_a": "a"})
        assert not harness.was_called("tool_a")

    def test_calls_for(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"t": "ok"})
        tools = harness.make_tools()
        tools[0].invoke({"x": 1})
        tools[0].invoke({"x": 2})
        calls = harness.calls_for("t")
        assert len(calls) == 2
        assert calls[0].args == {"x": 1}
        assert calls[1].args == {"x": 2}

    def test_last_call(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"t": "ok"})
        tools = harness.make_tools()
        tools[0].invoke({"v": "first"})
        tools[0].invoke({"v": "second"})
        last = harness.last_call("t")
        assert last is not None
        assert last.args == {"v": "second"}

    def test_last_call_none_when_not_called(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"t": "ok"})
        assert harness.last_call("t") is None

    def test_reset(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"t": "ok"})
        tools = harness.make_tools()
        tools[0].invoke({})
        assert harness.call_count("t") == 1
        harness.reset()
        assert harness.call_count("t") == 0

    def test_assert_tools_called_pass(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"a": "1", "b": "2"})
        tools = harness.make_tools()
        tools[0].invoke({})
        tools[1].invoke({})
        failures = harness.assert_tools_called(["a", "b"])
        assert failures == []

    def test_assert_tools_called_fail(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"a": "1", "b": "2"})
        tools = harness.make_tools()
        tools[0].invoke({})
        failures = harness.assert_tools_called(["a", "b"])
        assert len(failures) == 1
        assert "b" in failures[0]

    def test_assert_call_order_pass(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"a": "1", "b": "2", "c": "3"})
        tools = harness.make_tools()
        tools[0].invoke({})
        tools[1].invoke({})
        tools[2].invoke({})
        failures = harness.assert_call_order(["a", "c"])
        assert failures == []

    def test_assert_call_order_fail(self):
        from eval.engine.harness import EvalToolHarness

        harness = EvalToolHarness({"a": "1", "b": "2"})
        tools = harness.make_tools()
        tools[1].invoke({})
        tools[0].invoke({})
        failures = harness.assert_call_order(["a", "b"])
        assert len(failures) == 1  # b comes before a


# =========================================================================
# Config tests
# =========================================================================


class TestEvalConfig:
    """Test EvalConfig defaults and construction."""

    def test_default_config(self):
        from eval.config import EvalConfig

        config = EvalConfig()
        assert config.tier1_pass_threshold == 1.0
        assert config.tier2_overall_threshold == 0.70
        assert config.judge_model == "claude-sonnet-4-6"
        assert config.regression_tolerance == 0.05

    def test_vault_path_defaults_to_fixture(self):
        from eval.config import EvalConfig

        config = EvalConfig()
        assert "fixtures" in str(config.vault_path)
        assert "vault" in str(config.vault_path)

    def test_custom_config(self):
        from eval.config import EvalConfig

        config = EvalConfig(
            judge_model="claude-haiku-4-5-20251001",
            tier2_overall_threshold=0.80,
        )
        assert config.judge_model == "claude-haiku-4-5-20251001"
        assert config.tier2_overall_threshold == 0.80


# =========================================================================
# Grading tests
# =========================================================================


class TestGrading:
    """Test score aggregation and threshold checks."""

    def test_grade_tier1_suite(self):
        from eval.engine.grading import grade_tier1_suite
        from eval.schema import CaseResult

        results = [
            CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0),
            CaseResult(case_id="tc-2", tier=1, category="routing", passed=True, score=1.0),
        ]
        suite = grade_tier1_suite(results, "routing")
        assert suite.total == 2
        assert suite.passed == 2
        assert suite.score == 1.0

    def test_grade_tier1_with_failure(self):
        from eval.engine.grading import grade_tier1_suite
        from eval.schema import CaseResult

        results = [
            CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0),
            CaseResult(case_id="tc-2", tier=1, category="routing", passed=False, score=0.0),
        ]
        suite = grade_tier1_suite(results, "routing")
        assert suite.passed == 1
        assert suite.failed == 1
        assert suite.score == 0.5

    def test_check_thresholds_pass(self):
        from eval.engine.grading import check_thresholds
        from eval.schema import CaseResult, EvalRun, SuiteResult

        run = EvalRun(
            run_id="test",
            timestamp="2026-01-01T00:00:00Z",
            suites=[
                SuiteResult(
                    tier=1,
                    category="routing",
                    cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0)],
                ),
            ],
        )
        run.compute_stats()
        failures = check_thresholds(run)
        assert failures == []

    def test_check_thresholds_fail_tier1(self):
        from eval.engine.grading import check_thresholds
        from eval.schema import CaseResult, EvalRun, SuiteResult

        run = EvalRun(
            run_id="test",
            timestamp="2026-01-01T00:00:00Z",
            suites=[
                SuiteResult(
                    tier=1,
                    category="routing",
                    cases=[
                        CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0),
                        CaseResult(case_id="tc-2", tier=1, category="routing", passed=False, score=0.0),
                    ],
                ),
            ],
        )
        run.compute_stats()
        failures = check_thresholds(run)
        assert len(failures) == 1
        assert "Tier 1" in failures[0]


# =========================================================================
# Comparator tests
# =========================================================================


class TestComparator:
    """Test baseline comparison and regression detection."""

    def test_compare_no_regressions(self):
        from eval.engine.comparator import compare_runs
        from eval.schema import CaseResult, EvalRun, SuiteResult

        base = EvalRun(
            run_id="base",
            timestamp="2026-01-01",
            suites=[SuiteResult(
                tier=1,
                category="routing",
                cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=0.8)],
            )],
        )
        target = EvalRun(
            run_id="target",
            timestamp="2026-01-02",
            suites=[SuiteResult(
                tier=1,
                category="routing",
                cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=0.9)],
            )],
        )
        base.compute_stats()
        target.compute_stats()

        result = compare_runs(base, target)
        assert not result.has_regressions
        assert len(result.improvements) == 1

    def test_compare_with_regression(self):
        from eval.engine.comparator import compare_runs
        from eval.schema import CaseResult, EvalRun, SuiteResult

        base = EvalRun(
            run_id="base",
            timestamp="2026-01-01",
            suites=[SuiteResult(
                tier=1,
                category="routing",
                cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=0.9)],
            )],
        )
        target = EvalRun(
            run_id="target",
            timestamp="2026-01-02",
            suites=[SuiteResult(
                tier=1,
                category="routing",
                cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=False, score=0.3)],
            )],
        )
        base.compute_stats()
        target.compute_stats()

        result = compare_runs(base, target)
        assert result.has_regressions
        assert len(result.regressions) == 1
        assert result.regressions[0].severity in ("major", "critical")

    def test_save_and_load_run(self):
        from eval.engine.comparator import load_run, save_run
        from eval.schema import CaseResult, EvalRun, SuiteResult

        with tempfile.TemporaryDirectory() as tmpdir:
            run = EvalRun(
                run_id="save-test",
                timestamp="2026-01-01T00:00:00Z",
                suites=[SuiteResult(
                    tier=1,
                    category="routing",
                    cases=[CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0)],
                )],
            )
            run.compute_stats()

            path = save_run(run, Path(tmpdir))
            assert path.exists()

            loaded = load_run(path)
            assert loaded.run_id == "save-test"
            assert loaded.total_cases == 1


# =========================================================================
# Dataset loading tests
# =========================================================================


class TestDatasetLoading:
    """Test YAML dataset loading and validation."""

    def test_load_tier1_routing_dataset(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.load_tier1_datasets()
        assert "routing" in datasets
        assert len(datasets["routing"].cases) > 0

    def test_load_tier1_keyword_dataset(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.load_tier1_datasets(["keyword_routing"])
        assert "keyword_routing" in datasets

    def test_load_tier1_skill_dataset(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.load_tier1_datasets(["skill_matching"])
        assert "skill_matching" in datasets

    def test_load_tier1_tool_dataset(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.load_tier1_datasets(["tool_groups"])
        assert "tool_groups" in datasets

    def test_load_tier2_datasets(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.load_tier2_datasets()
        assert len(datasets) >= 5  # companion, memory, ironclaw, planning, personal, etc.

    def test_list_datasets(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.list_datasets()
        assert len(datasets) > 0

        # Check structure
        for ds in datasets:
            assert "tier" in ds
            assert "category" in ds
            assert "case_count" in ds
            assert ds["case_count"] > 0

    def test_list_datasets_has_both_tiers(self):
        from eval.config import EvalConfig
        from eval.engine.runner import EvalRunner

        runner = EvalRunner(EvalConfig())
        datasets = runner.list_datasets()
        tiers = {ds["tier"] for ds in datasets}
        assert 1 in tiers
        assert 2 in tiers

    def test_tier1_dataset_yaml_valid(self):
        """Validate that all Tier 1 YAML files parse correctly."""
        from eval.config import DATASETS_DIR
        from eval.schema import Tier1Dataset

        tier1_dir = DATASETS_DIR / "tier1"
        for path in tier1_dir.glob("*_cases.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            dataset = Tier1Dataset(**data)
            assert dataset.tier == 1
            assert len(dataset.cases) > 0, f"{path.name} has no cases"

    def test_tier2_dataset_yaml_valid(self):
        """Validate that all Tier 2 YAML files parse correctly."""
        from eval.config import DATASETS_DIR
        from eval.schema import Tier2Dataset

        tier2_dir = DATASETS_DIR / "tier2"
        for path in tier2_dir.glob("*_cases.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            dataset = Tier2Dataset(**data)
            assert dataset.tier == 2
            assert len(dataset.cases) > 0, f"{path.name} has no cases"

    def test_all_case_ids_unique(self):
        """All case IDs across all datasets must be unique."""
        from eval.config import DATASETS_DIR

        all_ids = []
        for tier_dir in ["tier1", "tier2"]:
            dir_path = DATASETS_DIR / tier_dir
            if not dir_path.exists():
                continue
            for path in dir_path.glob("*_cases.yaml"):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                for case in data.get("cases", []):
                    all_ids.append(case["id"])

        assert len(all_ids) == len(set(all_ids)), (
            f"Duplicate case IDs found: "
            f"{[x for x in all_ids if all_ids.count(x) > 1]}"
        )


# =========================================================================
# Judge tests
# =========================================================================


class TestJudge:
    """Test judge prompt building and response parsing."""

    def test_build_judge_prompt(self):
        from eval.engine.tier2.judge import build_judge_prompt
        from eval.schema import Tier2Case

        case = Tier2Case(
            id="test",
            turn_type="single",
            message="create an FDO",
            grading={"understanding": "Did it understand?", "execution": "Did it execute?"},
        )
        prompt = build_judge_prompt(case, "I created the FDO.", ["kronos_create"])
        assert "create an FDO" in prompt
        assert "kronos_create" in prompt
        assert "understanding" in prompt

    def test_parse_judge_response_valid(self):
        from eval.engine.tier2.judge import parse_judge_response
        from eval.schema import Tier2Case

        case = Tier2Case(id="test", grading={"understanding": "?"})
        response = '[{"dimension": "understanding", "score": 0.85, "rationale": "Good"}]'
        dims = parse_judge_response(response, case)
        assert len(dims) == 1
        assert dims[0].score == 0.85

    def test_parse_judge_response_fenced(self):
        from eval.engine.tier2.judge import parse_judge_response
        from eval.schema import Tier2Case

        case = Tier2Case(id="test", grading={"understanding": "?"})
        response = '```json\n[{"dimension": "understanding", "score": 0.9, "rationale": "Great"}]\n```'
        dims = parse_judge_response(response, case)
        assert len(dims) == 1
        assert dims[0].score == 0.9

    def test_parse_judge_response_invalid(self):
        from eval.engine.tier2.judge import parse_judge_response
        from eval.schema import Tier2Case

        case = Tier2Case(id="test", grading={"understanding": "?", "execution": "?"})
        response = "not valid json at all"
        dims = parse_judge_response(response, case)
        assert len(dims) == 2
        assert all(d.score == 0.0 for d in dims)

    def test_parse_judge_clamps_scores(self):
        from eval.engine.tier2.judge import parse_judge_response
        from eval.schema import Tier2Case

        case = Tier2Case(id="test", grading={"understanding": "?"})
        response = '[{"dimension": "understanding", "score": 1.5, "rationale": "Over"}]'
        dims = parse_judge_response(response, case)
        assert dims[0].score == 1.0  # clamped


# =========================================================================
# Tier 2 structural tests
# =========================================================================


class TestTier2SingleTurn:
    """Test Tier 2 single-turn evaluator (dry-run mode)."""

    @pytest.mark.asyncio
    async def test_single_turn_dry_run(self):
        from eval.engine.tier2.single_turn import evaluate_single_turn
        from eval.schema import Tier2Case, Tier2Expected

        case = Tier2Case(
            id="test-dry",
            turn_type="single",
            message="do something",
            mock_tools={"tool_a": "result_a"},
            expected=Tier2Expected(tools_called=["tool_a"]),
        )
        result = await evaluate_single_turn(case)
        assert result.case_id == "test-dry"
        assert result.tier == 2
        assert "tool_a" in result.tool_trace

    @pytest.mark.asyncio
    async def test_single_turn_no_message(self):
        from eval.engine.tier2.single_turn import evaluate_single_turn
        from eval.schema import Tier2Case

        case = Tier2Case(id="no-msg", turn_type="single")
        result = await evaluate_single_turn(case)
        assert not result.passed
        assert "No message" in result.error


class TestTier2MultiTurn:
    """Test Tier 2 multi-turn evaluator (dry-run mode)."""

    @pytest.mark.asyncio
    async def test_multi_turn_dry_run(self):
        from eval.engine.tier2.multi_turn import evaluate_multi_turn
        from eval.schema import ConversationTurn, Tier2Case, TurnCheckpoint

        case = Tier2Case(
            id="test-multi",
            turn_type="multi",
            turns=[
                ConversationTurn(
                    message="first",
                    checkpoint=TurnCheckpoint(response_contains=["first"]),
                ),
                ConversationTurn(message="second"),
            ],
            mock_tools={"tool_a": "ok"},
        )
        result = await evaluate_multi_turn(case)
        assert result.case_id == "test-multi"
        assert result.tier == 2

    @pytest.mark.asyncio
    async def test_multi_turn_no_turns(self):
        from eval.engine.tier2.multi_turn import evaluate_multi_turn
        from eval.schema import Tier2Case

        case = Tier2Case(id="no-turns", turn_type="multi")
        result = await evaluate_multi_turn(case)
        assert not result.passed
        assert "No turns" in result.error


# =========================================================================
# Reports tests
# =========================================================================


class TestReports:
    """Test report generation."""

    def test_markdown_report(self):
        from eval.reports import generate_markdown_report
        from eval.schema import CaseResult, EvalRun, SuiteResult

        run = EvalRun(
            run_id="report-test",
            timestamp="2026-01-01T00:00:00Z",
            git_sha="abc1234",
            suites=[SuiteResult(
                tier=1,
                category="routing",
                cases=[
                    CaseResult(case_id="tc-1", tier=1, category="routing", passed=True, score=1.0),
                    CaseResult(case_id="tc-2", tier=1, category="routing", passed=False, score=0.0),
                ],
            )],
        )
        run.compute_stats()

        report = generate_markdown_report(run)
        assert "report-test" in report
        assert "abc1234" in report
        assert "routing" in report
        assert "PASS" in report
        assert "FAIL" in report


# =========================================================================
# Fixture tests
# =========================================================================


class TestFixtures:
    """Test that eval fixtures exist and are valid."""

    def test_fixture_vault_exists(self):
        from eval.config import FIXTURES_DIR

        vault = FIXTURES_DIR / "vault"
        assert vault.exists()
        assert (vault / "projects").exists()
        assert (vault / "ai-systems").exists()
        assert (vault / "physics").exists()

    def test_fixture_vault_has_fdos(self):
        from eval.config import FIXTURES_DIR

        vault = FIXTURES_DIR / "vault"
        md_files = list(vault.rglob("*.md"))
        assert len(md_files) >= 2  # at least proj-test and grim-identity

    def test_fixture_board_exists(self):
        from eval.config import FIXTURES_DIR

        board = FIXTURES_DIR / "vault" / "projects" / "board.yaml"
        assert board.exists()
        data = yaml.safe_load(board.read_text())
        assert "columns" in data
