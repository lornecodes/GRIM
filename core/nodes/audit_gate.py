"""Audit gate node — decide whether dispatched output needs audit review.

Only IronClaw dispatches with staged artifacts go through the audit path.
All other agents (memory, coder, researcher, operator) skip straight to
integrate, preserving backward compatibility.
"""

from __future__ import annotations

import logging

from core.state import GrimState

logger = logging.getLogger(__name__)


async def audit_gate_node(state: GrimState) -> dict:
    """Pass-through node — routing logic is in the decision function."""
    return {}


def audit_gate_decision(state: GrimState) -> str:
    """Decide: route to audit or skip to integrate.

    Returns:
        "audit" if IronClaw dispatched with staging artifacts.
        "skip" for all other agents.
    """
    delegation = state.get("delegation_type")
    artifacts = state.get("staging_artifacts", [])

    if delegation == "ironclaw" and artifacts:
        logger.info(
            "Audit gate: routing to audit (%d artifacts, job %s)",
            len(artifacts),
            state.get("staging_job_id"),
        )
        return "audit"

    logger.info("Audit gate: skipping audit (delegation=%s, artifacts=%d)", delegation, len(artifacts))
    return "skip"
