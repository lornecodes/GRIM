"""GRIM state types — the data flowing through the LangGraph state graph."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Schema version — bump on breaking state changes
# ---------------------------------------------------------------------------

STATE_SCHEMA_VERSION = "0.10.0"


# ---------------------------------------------------------------------------
# Objective system — drives the continuation loop
# ---------------------------------------------------------------------------

class ObjectiveStatus(str, Enum):
    """Lifecycle states for an Objective."""
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"       # needs user input — triggers interrupt
    COMPLETE = "complete"
    FAILED = "failed"


class Objective(BaseModel):
    """Core unit of the state system.

    Objectives drive the continuation loop, persist across sessions,
    and map naturally to the task board hierarchy (Feature → Story → Task).
    """
    id: str = Field(default_factory=lambda: f"obj-{uuid.uuid4().hex[:8]}")
    title: str
    status: ObjectiveStatus = ObjectiveStatus.PENDING
    priority: Literal["high", "medium", "low"] = "medium"
    parent_id: Optional[str] = None
    children: list[str] = Field(default_factory=list)
    origin_subgraph: Optional[str] = None   # who created this
    target_subgraph: Optional[str] = None   # who should execute this
    context: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None    # why it's blocked (for interrupt)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


_OBJECTIVES_CAP = 100  # max objectives in state


def _merge_objectives(
    existing: list[Objective] | None,
    new: list[Objective] | None,
) -> list[Objective]:
    """LangGraph reducer: merge objective updates into existing list.

    Deduplicates by ID — if an objective already exists, the new version
    replaces it (for status updates). Otherwise appends.
    Caps at _OBJECTIVES_CAP entries.
    """
    if not existing and not new:
        return []
    if not existing:
        return (new or [])[:_OBJECTIVES_CAP]
    if not new:
        return existing

    by_id: dict[str, Objective] = {o.id: o for o in existing}
    for obj in new:
        by_id[obj.id] = obj  # replace or add

    merged = list(by_id.values())
    return merged[:_OBJECTIVES_CAP]


# ---------------------------------------------------------------------------
# Objective lifecycle methods
# ---------------------------------------------------------------------------

def create_objective(
    title: str,
    target_subgraph: str,
    parent_id: str | None = None,
    priority: str = "medium",
    context: dict[str, Any] | None = None,
    auto_continue: bool = False,
    origin_subgraph: str | None = None,
) -> Objective:
    """Create a new Objective with full metadata."""
    ctx = dict(context or {})
    if auto_continue:
        ctx["auto_continue"] = True
    return Objective(
        title=title,
        priority=priority,
        parent_id=parent_id,
        target_subgraph=target_subgraph,
        origin_subgraph=origin_subgraph,
        context=ctx,
    )


def create_objective_tree(plan: dict[str, Any]) -> list[Objective]:
    """Convert a structured plan into a hierarchy of Objectives.

    Expects plan format:
        {"title": "...", "stories": [{"title": "...", "target": "code", "tasks": [...]}]}

    Returns flat list with parent_id/children linking.
    """
    objectives: list[Objective] = []

    feature = create_objective(
        title=plan["title"],
        target_subgraph="planning",
        priority="high",
        origin_subgraph="planning",
    )
    objectives.append(feature)

    for story in plan.get("stories", []):
        story_obj = create_objective(
            title=story["title"],
            target_subgraph=story.get("target", "code"),
            parent_id=feature.id,
            priority=story.get("priority", "medium"),
            origin_subgraph="planning",
        )
        feature.children.append(story_obj.id)
        objectives.append(story_obj)

        for task in story.get("tasks", []):
            task_obj = create_objective(
                title=task["title"],
                target_subgraph=task.get("target", "code"),
                parent_id=story_obj.id,
                priority=task.get("priority", "medium"),
                context=task.get("context", {}),
                auto_continue=True,
                origin_subgraph="planning",
            )
            story_obj.children.append(task_obj.id)
            objectives.append(task_obj)

    return objectives


def update_objective(
    objectives: list[Objective],
    objective_id: str,
    status: ObjectiveStatus | None = None,
    artifacts: list[str] | None = None,
    blocked_reason: str | None = None,
) -> list[Objective]:
    """Return updated objectives list with the target objective modified.

    Non-destructive — returns a new list.
    """
    now = datetime.now(timezone.utc).isoformat()
    result: list[Objective] = []
    for obj in objectives:
        if obj.id == objective_id:
            updates: dict[str, Any] = {"updated_at": now}
            if status is not None:
                updates["status"] = status
                if status == ObjectiveStatus.COMPLETE:
                    updates["completed_at"] = now
            if artifacts is not None:
                updates["artifacts"] = obj.artifacts + artifacts
            if blocked_reason is not None:
                updates["blocked_reason"] = blocked_reason
                updates["status"] = ObjectiveStatus.BLOCKED
            result.append(obj.model_copy(update=updates))
        else:
            result.append(obj)
    return result


def get_pending_objectives(objectives: list[Objective]) -> list[Objective]:
    """Return pending objectives sorted by priority."""
    priority_order = {"high": 0, "medium": 1, "low": 2}
    pending = [o for o in objectives if o.status == ObjectiveStatus.PENDING]
    return sorted(pending, key=lambda o: priority_order.get(o.priority, 1))


def get_active_objectives(objectives: list[Objective]) -> list[Objective]:
    """Return all non-terminal objectives (pending, active, blocked)."""
    terminal = {ObjectiveStatus.COMPLETE, ObjectiveStatus.FAILED}
    return [o for o in objectives if o.status not in terminal]


def get_next_objective(objectives: list[Objective]) -> Objective | None:
    """Return the highest-priority pending objective with a target subgraph."""
    pending = get_pending_objectives(objectives)
    for obj in pending:
        if obj.target_subgraph:
            return obj
    return None


def handle_blocked_objective(
    objectives: list[Objective],
    objective_id: str,
    reason: str,
) -> tuple[list[Objective], Any]:
    """Block an objective and trigger LangGraph interrupt.

    Freezes graph state at this point and waits for user input.
    The user can respond minutes, hours, or days later — LangGraph
    resumes from this exact checkpoint.

    Returns:
        Tuple of (updated objectives, user_response from interrupt).
    """
    updated = update_objective(objectives, objective_id, blocked_reason=reason)

    # This freezes execution — the graph saves state and waits
    user_response = interrupt(reason)

    return updated, user_response


def build_resume_message(objectives: list[Objective]) -> str | None:
    """Build a companion-style resume message for active objectives.

    Returns None if there are no active objectives to resume.
    """
    active = get_active_objectives(objectives)
    if not active:
        return None

    titles = [o.title for o in active[:3]]
    count = len(active)

    if count == 1:
        return f'Hey — we were working on "{titles[0]}" last time. Want to pick that back up?'
    else:
        listed = ", ".join(f'"{t}"' for t in titles[:2])
        remaining = f" and {count - 2} more" if count > 2 else ""
        return (
            f"Hey — we had {count} things in progress: {listed}{remaining}. "
            "Want to continue or start something new?"
        )


# ---------------------------------------------------------------------------
# Routing decision — structured output from Companion Router
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    """Structured output from the Companion Router's intent classifier.

    The LLM produces this typed object instead of free-form text,
    making routing deterministic and auditable.
    """
    target_subgraph: Literal[
        "conversation", "research", "code", "operations", "planning"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    is_continuation: bool = False
    continuation_context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Subgraph output — common interface for all subgraphs
# ---------------------------------------------------------------------------

class SubgraphOutput(BaseModel):
    """Every subgraph returns this structure to the Response Generator."""
    response: str
    artifacts: list[str] = Field(default_factory=list)
    memory_updates: dict[str, Any] = Field(default_factory=dict)
    objective_updates: list[Objective] = Field(default_factory=list)
    continuation: Optional[dict[str, Any]] = None  # {next_intent, context}
    source_subgraph: str = ""


# ---------------------------------------------------------------------------
# UX Mode — detected at session entry, influences response formatting
# ---------------------------------------------------------------------------

class UXMode(str, Enum):
    """UX surface modes for response formatting."""
    FULLSCREEN = "fullscreen"           # rich, verbose, full markdown
    SIDEPANEL = "sidepanel"             # concise, key points
    MISSION_CONTROL = "mission_control" # status-oriented, progress
    DISCORD = "discord"                 # brief updates, background work


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
# Knowledge entry — session-level FDO accumulator with provenance
# ---------------------------------------------------------------------------

_SESSION_KNOWLEDGE_CAP = 50  # max accumulated FDOs per session


@dataclass
class KnowledgeEntry:
    """An FDO fetched during this session, with provenance metadata.

    Tracks when, by whom, and how often an FDO was referenced.
    Used by the session knowledge accumulator to avoid re-fetching
    and to provide agents with the full conversation context.
    """

    fdo: FDOSummary
    fetched_turn: int          # which turn first retrieved this FDO
    fetched_by: str            # "memory", "companion", "code", etc.
    query: str                 # the search query that found it
    last_referenced_turn: int  # updated when re-encountered
    hit_count: int = 1         # how many times this FDO was returned/referenced

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "fdo_id": self.fdo.id,
            "fdo_title": self.fdo.title,
            "fdo_domain": self.fdo.domain,
            "fdo_confidence": self.fdo.confidence,
            "fetched_turn": self.fetched_turn,
            "fetched_by": self.fetched_by,
            "query": self.query,
            "last_referenced_turn": self.last_referenced_turn,
            "hit_count": self.hit_count,
            "related": self.fdo.related,
        }


def _merge_session_knowledge(
    existing: list[KnowledgeEntry] | None,
    new: list[KnowledgeEntry] | None,
) -> list[KnowledgeEntry]:
    """LangGraph reducer: merge new knowledge entries into existing.

    Deduplicates by FDO ID — if an FDO already exists, bump hit_count
    and update last_referenced_turn. Otherwise append.
    Caps at _SESSION_KNOWLEDGE_CAP entries (drops least-referenced).
    """
    if not existing and not new:
        return []
    if not existing:
        return (new or [])[:_SESSION_KNOWLEDGE_CAP]
    if not new:
        return existing

    # Index existing by FDO ID for O(1) lookup
    by_id: dict[str, KnowledgeEntry] = {e.fdo.id: e for e in existing}

    for entry in new:
        fdo_id = entry.fdo.id
        if fdo_id in by_id:
            # Bump existing entry
            existing_entry = by_id[fdo_id]
            existing_entry.hit_count += entry.hit_count
            existing_entry.last_referenced_turn = max(
                existing_entry.last_referenced_turn,
                entry.last_referenced_turn,
            )
        else:
            by_id[fdo_id] = entry

    merged = list(by_id.values())

    # If over cap, drop least-referenced entries
    if len(merged) > _SESSION_KNOWLEDGE_CAP:
        merged.sort(key=lambda e: (e.hit_count, e.last_referenced_turn), reverse=True)
        merged = merged[:_SESSION_KNOWLEDGE_CAP]

    return merged


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
    created_by: str  # "code"


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

    # --- Schema version (v0.10.0) ---
    schema_version: str

    # Conversation history — add_messages enables proper accumulation
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Identity (loaded at session start)
    system_prompt: str
    field_state: FieldState
    user_identity: dict[str, Any]   # v0.10: full identity context for router
    memory_context: dict[str, Any]  # v0.10: loaded memory for router

    # Knowledge context (enriched per turn by memory node)
    knowledge_context: list[FDOSummary]

    # Session-level knowledge accumulator (survives compression, deduped)
    session_knowledge: Annotated[list[KnowledgeEntry], _merge_session_knowledge]
    turn_count: int  # incremented by memory node each turn

    # Skill context (matched per turn by skill_match node)
    matched_skills: list[SkillContext]
    skill_protocols: dict[str, str]  # skill_name → protocol.md content

    skill_delegation_hint: Optional[str]  # delegation target from skill match

    # --- v0.10: Structured routing ---
    routing_decision: Optional[dict[str, Any]]  # serialized RoutingDecision
    ux_mode: str  # UXMode value — detected at session entry

    # Graph-level routing (v0.0.6 compat — used by current graph_router)
    graph_target: Literal["research", "personal", "planning"]

    # Routing decision (within research graph — v0.0.6 compat)
    mode: Literal["companion", "delegate"]
    delegation_type: Optional[
        Literal["memory", "code", "research", "operate", "audit", "codebase"]
    ]
    selected_model: Optional[str]  # model ID chosen by model router

    # Agent results (set by doer agents, consumed by integrate node)
    agent_result: Optional[AgentResult]
    last_delegation_type: Optional[str]  # persists after integrate for continuity

    # Staging pipeline — Phase 4 zero-trust audit
    staging_job_id: Optional[str]  # UUID for current staging session
    staging_artifacts: list[StagingArtifact]  # files pending review
    audit_verdict: Optional[AuditVerdict]  # result from audit agent
    review_count: int  # audit cycles so far (0-based)
    max_reviews: int  # cap (default 3)
    audit_feedback: Optional[str]  # structured feedback for re-dispatch

    # Caller identity (resolved at session start)
    caller_id: str  # "peter", "pool", etc. — defaults to "peter"
    caller_context: Optional[str]  # compiled caller profile for prompt injection

    # Context management
    context_summary: Optional[str]  # compressed summary of older messages
    token_estimate: int  # estimated total tokens in messages

    # --- v0.10: Objective system (drives continuation loop) ---
    objectives: Annotated[list[Objective], _merge_objectives]

    # --- v0.10: Response Generator loop control ---
    subgraph_output: Optional[dict[str, Any]]  # serialized SubgraphOutput
    subgraph_history: list[str]    # trail of subgraphs visited this session
    context_stack: list[dict[str, Any]]  # outputs from each subgraph pass
    should_continue: bool          # Response Generator sets this
    continuation_intent: Optional[str]  # target subgraph for next loop
    loop_count: int                # incremented each loop iteration
    max_loops: int                 # safety valve (default 10)

    # Recent notes from rolling logs (populated by memory node)
    recent_notes: list[dict[str, Any]]

    # Evolution tracking
    session_topics: list[str]
    session_start: datetime

    # Tracing
    trace_id: str  # unique per invocation for log correlation

    # Sandbox mode — blocks vault/memory writes for eval testing
    sandbox: bool

    # NOTE: agent_event_queue is NOT in state — it's passed via
    # RunnableConfig["configurable"]["agent_event_queue"] to avoid
    # serialization by LangGraph's checkpointer (Queue is not serializable).
