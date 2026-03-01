"""GRIM state types — the data flowing through the LangGraph state graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Field State — personality dynamics
# ---------------------------------------------------------------------------

@dataclass
class FieldState:
    """Personality dynamics derived from GRIM v0.2 FieldState model.

    These values modulate GRIM's response expression:
      - coherence: how focused/structured responses are (0-1)
      - valence: emotional tone (-1 to 1)
      - uncertainty: epistemic caution (0-1)
    """

    coherence: float = 0.8
    valence: float = 0.3
    uncertainty: float = 0.2

    def modulate(self, confidence: float | None = None, topic_type: str | None = None) -> None:
        """Apply modulation rules based on context."""
        if confidence is not None:
            if confidence > 0.8:
                self.uncertainty = max(0.0, self.uncertainty - 0.1)
            elif confidence < 0.3:
                self.uncertainty = min(1.0, self.uncertainty + 0.2)

        if topic_type == "established":
            self.coherence = min(1.0, self.coherence + 0.1)
            self.uncertainty = max(0.0, self.uncertainty - 0.1)
        elif topic_type == "speculative":
            self.uncertainty = min(1.0, self.uncertainty + 0.2)
            self.valence = min(1.0, self.valence + 0.1)

    def expression_mode(self) -> str:
        """Return the current expression mode description."""
        if self.coherence > 0.6 and self.uncertainty < 0.4:
            return "direct, assertive"
        elif self.coherence > 0.6 and self.uncertainty >= 0.4:
            return "careful, hedging but structured"
        elif self.coherence <= 0.6 and self.uncertainty < 0.4:
            return "conversational, flowing"
        else:
            return "exploratory, open"

    def snapshot(self) -> dict[str, float]:
        """Return a dict snapshot for serialization."""
        return {
            "coherence": round(self.coherence, 3),
            "valence": round(self.valence, 3),
            "uncertainty": round(self.uncertainty, 3),
        }


# ---------------------------------------------------------------------------
# Skill context — matched skills per turn
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """Metadata about a matched skill."""

    name: str
    version: str
    description: str
    permissions: list[str] = field(default_factory=list)
    triggers: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FDO summary — knowledge context from Kronos
# ---------------------------------------------------------------------------

@dataclass
class FDOSummary:
    """Lightweight summary of a Kronos FDO for graph state."""

    id: str
    title: str
    domain: str
    status: str
    confidence: float
    summary: str
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent result — output from doer agents
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Result returned by a doer agent."""

    agent: str  # e.g. "memory", "coder", "researcher", "operator", "audit"
    success: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)  # created/modified file paths


# ---------------------------------------------------------------------------
# Staging pipeline — Phase 4 zero-trust audit
# ---------------------------------------------------------------------------

@dataclass
class StagingArtifact:
    """A file in the staging area pending review."""

    path: str  # relative to /workspace/staging/{job_id}/output/
    size_bytes: int
    artifact_type: str  # "file", "script_output", "log"
    created_by: str  # "ironclaw"


@dataclass
class AuditVerdict:
    """Result from the audit agent's review of staged artifacts."""

    passed: bool
    issues: list[str] = field(default_factory=list)  # blocking problems
    suggestions: list[str] = field(default_factory=list)  # non-blocking improvements
    security_flags: list[str] = field(default_factory=list)  # security concerns
    summary: str = ""  # one-line verdict


# ---------------------------------------------------------------------------
# Graph State — the TypedDict flowing through LangGraph
# ---------------------------------------------------------------------------

class GrimState(TypedDict, total=False):
    """Full state for the GRIM LangGraph state graph.

    Nodes read from and write to this state. LangGraph manages
    persistence and checkpointing via SQLite.
    """

    # Conversation history — add_messages enables proper accumulation
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Identity (loaded at session start)
    system_prompt: str
    field_state: FieldState

    # Knowledge context (enriched per turn by memory node)
    knowledge_context: list[FDOSummary]

    # Skill context (matched per turn by skill_match node)
    matched_skills: list[SkillContext]
    skill_protocols: dict[str, str]  # skill_name → protocol.md content

    # Routing decision
    mode: Literal["companion", "delegate"]
    delegation_type: Optional[
        Literal["memory", "code", "research", "operate", "ironclaw", "audit"]
    ]
    selected_model: Optional[str]  # model ID chosen by model router

    # IronClaw engine state
    ironclaw_available: bool

    # Agent results (set by doer agents, consumed by integrate node)
    agent_result: Optional[AgentResult]

    # Staging pipeline — Phase 4 zero-trust audit
    staging_job_id: Optional[str]  # UUID for current staging session
    staging_artifacts: list[StagingArtifact]  # files pending review
    audit_verdict: Optional[AuditVerdict]  # result from audit agent
    review_count: int  # audit cycles so far (0-based)
    max_reviews: int  # cap (default 3)
    audit_feedback: Optional[str]  # structured feedback for re-dispatch

    # Caller identity (resolved at session start)
    caller_id: str  # "peter", "ironclaw", etc. — defaults to "peter"
    caller_context: Optional[str]  # compiled caller profile for prompt injection

    # Context management
    context_summary: Optional[str]  # compressed summary of older messages
    token_estimate: int  # estimated total tokens in messages

    # Persistent objectives
    objectives: list  # list[Objective] — active objectives loaded at session start

    # Evolution tracking
    session_topics: list[str]
    session_start: datetime
