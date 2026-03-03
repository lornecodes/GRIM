"""Tier 1 tool resolution evaluator — verify agent tool groups.

Instantiates agent classes and checks that they have the expected
tools (and don't have forbidden ones).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from eval.schema import CheckResult, CaseResult, Tier1Case

logger = logging.getLogger(__name__)

# Agent class lookup
_AGENT_CLASSES: dict[str, str] = {
    "memory": "core.agents.memory_agent.MemoryAgent",
    "research": "core.agents.research_agent.ResearchAgent",
    "codebase": "core.agents.codebase_agent.CodebaseAgent",
    "operator": "core.agents.operator_agent.OperatorAgent",
    "coder": "core.agents.coder_agent.CoderAgent",
    "ironclaw": "core.agents.ironclaw_agent.IronClawAgent",
    "audit": "core.agents.audit_agent.AuditAgent",
}


def _get_agent_class(agent_name: str):
    """Import and return an agent class by name."""
    import importlib

    path = _AGENT_CLASSES.get(agent_name)
    if not path:
        raise ValueError(f"Unknown agent: {agent_name}")

    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _get_agent_tools(agent_name: str) -> list[str]:
    """Instantiate an agent and return its tool names."""
    from core.config import GrimConfig

    cls = _get_agent_class(agent_name)
    config = GrimConfig()
    config.model = "claude-sonnet-4-6"

    agent = cls(config)
    return [t.name for t in agent.tools]


async def evaluate_tool_case(case: Tier1Case) -> CaseResult:
    """Evaluate a single tool group test case.

    The 'message' field is used as the agent name to test.
    """
    start = time.monotonic()
    checks: list[CheckResult] = []
    agent_name = case.message  # repurposed: agent name to test

    try:
        tool_names = _get_agent_tools(agent_name)

        # Check tools_present
        if case.expected.tools_present:
            for tool in case.expected.tools_present:
                found = tool in tool_names
                checks.append(CheckResult(
                    name=f"has_{tool}",
                    expected=True,
                    actual=found,
                    passed=found,
                ))

        # Check tools_absent
        if case.expected.tools_absent:
            for tool in case.expected.tools_absent:
                found = tool in tool_names
                checks.append(CheckResult(
                    name=f"missing_{tool}",
                    expected=False,
                    actual=found,
                    passed=not found,
                ))

    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="tool_groups",
            tags=case.tags,
            passed=False,
            score=0.0,
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    passed = all(c.passed for c in checks)
    return CaseResult(
        case_id=case.id,
        tier=1,
        category="tool_groups",
        tags=case.tags,
        passed=passed,
        score=1.0 if passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def evaluate_tool_suite(cases: list[Tier1Case]) -> list[CaseResult]:
    """Evaluate all tool group cases."""
    results = []
    for case in cases:
        result = await evaluate_tool_case(case)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s", status, case.id)
    return results
