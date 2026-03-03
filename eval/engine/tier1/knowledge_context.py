"""Tier 1 knowledge_context evaluator — test session knowledge accumulation.

Tests KnowledgeEntry, reducer, merge, prompt builder, and compression
integration. No LLM calls, pure structural assertion.
"""

from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path
from typing import Any

from eval.schema import CheckResult, CaseResult, Tier1Case

logger = logging.getLogger(__name__)


def _fdo(id: str, domain: str = "physics", confidence: float = 0.8, related: list | None = None):
    from core.state import FDOSummary
    return FDOSummary(
        id=id,
        title=id.replace("-", " ").title(),
        domain=domain,
        status="stable",
        confidence=confidence,
        summary="test",
        related=related or [],
    )


def _entry(id: str, turn: int = 1, hit_count: int = 1, by: str = "memory", related: list | None = None):
    from core.state import KnowledgeEntry
    return KnowledgeEntry(
        fdo=_fdo(id, related=related),
        fetched_turn=turn,
        fetched_by=by,
        query="test",
        last_referenced_turn=turn,
        hit_count=hit_count,
    )


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

_CHECKS: dict[str, Any] = {}


def _check(name: str):
    def decorator(fn):
        _CHECKS[name] = fn
        return fn
    return decorator


@_check("entry_defaults")
def _entry_defaults():
    e = _entry("test")
    return e.hit_count == 1


@_check("entry_to_dict")
def _entry_to_dict():
    e = _entry("pac")
    d = e.to_dict()
    required = {"fdo_id", "fdo_title", "fdo_domain", "fetched_turn", "fetched_by", "hit_count", "query"}
    return required.issubset(d.keys())


@_check("entry_related")
def _entry_related():
    e = _entry("pac", related=["sec", "rbf"])
    d = e.to_dict()
    return d.get("related") == ["sec", "rbf"]


@_check("both_none")
def _both_none():
    from core.state import _merge_session_knowledge
    return _merge_session_knowledge(None, None) == []


@_check("dedup_bumps_hit_count")
def _dedup_bumps_hit_count():
    from core.state import _merge_session_knowledge
    existing = [_entry("a", hit_count=3)]
    new = [_entry("a", hit_count=1)]
    result = _merge_session_knowledge(existing, new)
    return len(result) == 1 and result[0].hit_count == 4


@_check("dedup_updates_turn")
def _dedup_updates_turn():
    from core.state import _merge_session_knowledge
    existing = [_entry("a", turn=1)]
    new = [_entry("a", turn=5)]
    result = _merge_session_knowledge(existing, new)
    return result[0].last_referenced_turn == 5


@_check("cap_enforcement")
def _cap_enforcement():
    from core.state import _merge_session_knowledge, _SESSION_KNOWLEDGE_CAP
    entries = [_entry(f"fdo-{i}") for i in range(_SESSION_KNOWLEDGE_CAP + 10)]
    result = _merge_session_knowledge(None, entries)
    return len(result) == _SESSION_KNOWLEDGE_CAP


@_check("cap_keeps_important")
def _cap_keeps_important():
    from core.state import _merge_session_knowledge, _SESSION_KNOWLEDGE_CAP
    existing = [_entry(f"fdo-{i}", hit_count=1) for i in range(_SESSION_KNOWLEDGE_CAP)]
    new = [_entry("important", hit_count=100)]
    result = _merge_session_knowledge(existing, new)
    return any(e.fdo.id == "important" for e in result)


@_check("merge_empty")
def _merge_empty():
    from core.agents.base import _merge_knowledge_sources
    state = {"knowledge_context": [], "session_knowledge": []}
    return _merge_knowledge_sources(state) == []


@_check("merge_dedup")
def _merge_dedup():
    from core.agents.base import _merge_knowledge_sources
    fdos = [_fdo("pac")]
    entries = [_entry("pac")]
    state = {"knowledge_context": fdos, "session_knowledge": entries}
    return len(_merge_knowledge_sources(state)) == 1


@_check("merge_priority")
def _merge_priority():
    from core.agents.base import _merge_knowledge_sources
    fdos = [_fdo("fresh")]
    entries = [_entry("cached")]
    state = {"knowledge_context": fdos, "session_knowledge": entries}
    result = _merge_knowledge_sources(state)
    return result[0].id == "fresh"


@_check("merge_cap_10")
def _merge_cap_10():
    from core.agents.base import _merge_knowledge_sources, BaseAgent
    agent = BaseAgent.__new__(BaseAgent)
    fdos = [_fdo(f"fdo-{i}") for i in range(15)]
    state = {"knowledge_context": fdos, "session_knowledge": []}
    ctx = agent.build_context(state)
    # build_context caps at 10
    return "fdo-9" in ctx.get("relevant_fdos", "") and "fdo-10" not in ctx.get("relevant_fdos", "")


@_check("prompt_accepts_param")
def _prompt_accepts_param():
    from core.personality.prompt_builder import build_system_prompt_parts
    sig = inspect.signature(build_system_prompt_parts)
    return "session_knowledge" in sig.parameters


@_check("compression_placeholder")
def _compression_placeholder():
    from core.context import COMPRESSION_PROMPT
    return "{knowledge_references}" in COMPRESSION_PROMPT


# ---------------------------------------------------------------------------
# Evaluator entry point
# ---------------------------------------------------------------------------

async def evaluate(case: Tier1Case) -> CaseResult:
    """Evaluate a knowledge_context case."""
    start = time.monotonic()
    checks: list[CheckResult] = []

    check_name = case.expected.reducer_check
    if not check_name:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="knowledge_context",
            tags=case.tags,
            passed=False,
            score=0.0,
            error="No reducer_check specified",
            duration_ms=0,
        )

    check_fn = _CHECKS.get(check_name)
    if not check_fn:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="knowledge_context",
            tags=case.tags,
            passed=False,
            score=0.0,
            error=f"Unknown check: {check_name}",
            duration_ms=0,
        )

    try:
        result = check_fn()
        checks.append(CheckResult(
            name=check_name,
            expected="pass",
            actual="pass" if result else "fail",
            passed=bool(result),
        ))
    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="knowledge_context",
            tags=case.tags,
            passed=False,
            score=0.0,
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    all_passed = all(c.passed for c in checks)
    return CaseResult(
        case_id=case.id,
        tier=1,
        category="knowledge_context",
        tags=case.tags,
        passed=all_passed,
        score=1.0 if all_passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def evaluate_knowledge_suite(
    cases: list[Tier1Case],
    config: Any = None,
) -> list[CaseResult]:
    """Evaluate all knowledge_context cases."""
    results = []
    for case in cases:
        result = await evaluate(case)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s", status, case.id)
    return results
