"""Pydantic models for eval datasets, results, and scores."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Dataset schemas
# ---------------------------------------------------------------------------


class ExpectedOutcome(BaseModel):
    """Expected structural outcome for a Tier 1 case."""

    graph_target: Optional[str] = None
    mode: Optional[str] = None
    delegation_type: Optional[str] = None
    matched_skills: Optional[list[str]] = None
    top_skill: Optional[str] = None
    delegation_hint: Optional[str] = None
    keyword_match: Optional[str] = None
    action_intent: Optional[str] = None
    tools_present: Optional[list[str]] = None
    tools_absent: Optional[list[str]] = None
    reducer_check: Optional[str] = None  # knowledge_context evaluator checks


class Tier1Case(BaseModel):
    """A single Tier 1 deterministic test case."""

    id: str
    tags: list[str] = Field(default_factory=list)
    message: str
    state_overrides: dict[str, Any] = Field(default_factory=dict)
    expected: ExpectedOutcome


class Tier1Dataset(BaseModel):
    """A Tier 1 dataset file."""

    version: str = "1.0"
    tier: int = 1
    category: str
    description: str = ""
    cases: list[Tier1Case]


class TurnCheckpoint(BaseModel):
    """Expected state at a multi-turn checkpoint."""

    tools_called: Optional[list[str]] = None
    response_contains: Optional[list[str]] = None
    response_excludes: Optional[list[str]] = None


class ConversationTurn(BaseModel):
    """A single turn in a multi-turn eval."""

    role: str = "user"
    message: str
    checkpoint: Optional[TurnCheckpoint] = None


class Tier2Expected(BaseModel):
    """Expected outcomes for Tier 2 cases (structural checks)."""

    tools_called: Optional[list[str]] = None
    response_contains: Optional[list[str]] = None
    response_excludes: Optional[list[str]] = None


class Tier2Case(BaseModel):
    """A single Tier 2 LLM-graded test case."""

    id: str
    tags: list[str] = Field(default_factory=list)
    turn_type: str = "single"  # "single" or "multi"
    message: Optional[str] = None  # for single-turn
    turns: Optional[list[ConversationTurn]] = None  # for multi-turn
    context: dict[str, Any] = Field(default_factory=dict)
    mock_tools: dict[str, Any] = Field(default_factory=dict)
    expected: Optional[Tier2Expected] = None
    grading: dict[str, str] = Field(default_factory=dict)
    golden_response: Optional[str] = None


class Tier2Dataset(BaseModel):
    """A Tier 2 dataset file."""

    version: str = "1.0"
    tier: int = 2
    category: str
    description: str = ""
    cases: list[Tier2Case]


# ---------------------------------------------------------------------------
# Result schemas
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result of a single assertion check."""

    name: str
    expected: Any = None
    actual: Any = None
    passed: bool


class DimensionScore(BaseModel):
    """Score for a single grading dimension."""

    name: str
    score: float  # 0.0 - 1.0
    weight: float = 1.0
    rationale: str = ""


class CaseResult(BaseModel):
    """Result of evaluating a single test case."""

    case_id: str
    tier: int
    category: str
    tags: list[str] = Field(default_factory=list)
    passed: bool
    score: float = 0.0  # 0.0 - 1.0
    checks: list[CheckResult] = Field(default_factory=list)
    dimensions: list[DimensionScore] = Field(default_factory=list)
    tool_trace: list[str] = Field(default_factory=list)
    response_text: str = ""
    judge_output: Optional[str] = None
    duration_ms: int = 0
    error: Optional[str] = None


class SuiteResult(BaseModel):
    """Result of evaluating a test suite (one category)."""

    tier: int
    category: str
    cases: list[CaseResult]
    total: int = 0
    passed: int = 0
    failed: int = 0
    score: float = 0.0
    duration_ms: int = 0

    def compute_stats(self) -> None:
        """Recompute aggregate stats from case results."""
        self.total = len(self.cases)
        self.passed = sum(1 for c in self.cases if c.passed)
        self.failed = self.total - self.passed
        if self.total > 0:
            self.score = sum(c.score for c in self.cases) / self.total
        self.duration_ms = sum(c.duration_ms for c in self.cases)


class EvalRunStatus(str, Enum):
    """Status of an eval run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvalRun(BaseModel):
    """Complete result of an evaluation run."""

    run_id: str
    timestamp: str  # ISO 8601
    git_sha: str = ""
    tier: int | str = "all"  # 1, 2, or "all"
    status: EvalRunStatus = EvalRunStatus.PENDING
    suites: list[SuiteResult] = Field(default_factory=list)
    overall_score: float = 0.0
    pass_rate: float = 0.0
    total_cases: int = 0
    total_passed: int = 0
    duration_ms: int = 0
    config_snapshot: dict[str, Any] = Field(default_factory=dict)

    def compute_stats(self) -> None:
        """Recompute all aggregate stats from suites."""
        for s in self.suites:
            s.compute_stats()
        self.total_cases = sum(s.total for s in self.suites)
        self.total_passed = sum(s.passed for s in self.suites)
        if self.total_cases > 0:
            self.pass_rate = self.total_passed / self.total_cases
            self.overall_score = sum(
                s.score * s.total for s in self.suites
            ) / self.total_cases
        self.duration_ms = sum(s.duration_ms for s in self.suites)


class RegressionItem(BaseModel):
    """A single regression between two runs."""

    case_id: str
    category: str
    base_score: float
    target_score: float
    delta: float
    severity: str = "minor"  # "minor", "major", "critical"


class ComparisonResult(BaseModel):
    """Comparison between two eval runs."""

    base_run_id: str
    target_run_id: str
    regressions: list[RegressionItem] = Field(default_factory=list)
    improvements: list[RegressionItem] = Field(default_factory=list)
    unchanged: int = 0
    has_regressions: bool = False
    overall_delta: float = 0.0
