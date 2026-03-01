"""Integrate node — absorb agent results back into conversation state.

After a doer agent completes its work, this node formats the results
for the user and adds them to the conversation as an AI message.

For the staging pipeline (Phase 4), integrate also handles:
- Audit passed: report accepted staged artifacts to user
- Audit escalated: format failures for user after max retries
- Clearing staging state after integration
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from core.state import AuditVerdict, GrimState, StagingArtifact

logger = logging.getLogger(__name__)


async def integrate_node(state: GrimState) -> dict:
    """Integrate agent results into the conversation."""
    agent_result = state.get("agent_result")

    if agent_result is None:
        return {}

    # Format result as a conversation message
    if agent_result.success:
        msg = f"**{agent_result.agent.title()} Agent**: {agent_result.summary}"
        if agent_result.artifacts:
            msg += "\n\nArtifacts: " + ", ".join(agent_result.artifacts)
    else:
        msg = f"**{agent_result.agent.title()} Agent** (failed): {agent_result.summary}"

    # Handle staging pipeline outcomes
    verdict: AuditVerdict | None = state.get("audit_verdict")
    job_id = state.get("staging_job_id")
    staging_artifacts = state.get("staging_artifacts", [])

    if verdict and job_id:
        if verdict.passed:
            msg += _format_audit_pass(verdict, staging_artifacts, job_id)
        else:
            # Escalation — max retries exceeded
            msg += _format_audit_escalation(verdict, state.get("review_count", 0))

    logger.info(
        "Integrate: %s agent %s — %s",
        agent_result.agent,
        "succeeded" if agent_result.success else "failed",
        agent_result.summary[:100],
    )

    result: dict = {
        "messages": [AIMessage(content=msg)],
        "agent_result": None,  # clear for next turn
        "last_delegation_type": agent_result.agent,  # persist for continuity
    }

    # Clear staging state after integration
    if job_id:
        result.update({
            "staging_job_id": None,
            "staging_artifacts": [],
            "audit_verdict": None,
            "review_count": 0,
            "audit_feedback": None,
        })

    return result


def _format_audit_pass(
    verdict: AuditVerdict,
    artifacts: list[StagingArtifact],
    job_id: str,
) -> str:
    """Format message for a passed audit."""
    parts = [f"\n\n**Audit Passed** — {verdict.summary}"]

    if artifacts:
        parts.append(f"\nStaged files ({len(artifacts)}):")
        for artifact in artifacts:
            parts.append(f"  - `{artifact.path}` ({artifact.size_bytes} bytes)")

    if verdict.suggestions:
        parts.append("\nSuggestions for improvement:")
        for suggestion in verdict.suggestions:
            parts.append(f"  - {suggestion}")

    return "\n".join(parts)


def _format_audit_escalation(verdict: AuditVerdict, review_count: int) -> str:
    """Format message when audit fails after max retries."""
    parts = [
        f"\n\n**Audit Failed** — escalating after {review_count} attempt(s)",
        f"\nVerdict: {verdict.summary}",
    ]

    if verdict.issues:
        parts.append("\nUnresolved issues:")
        for issue in verdict.issues:
            parts.append(f"  - {issue}")

    if verdict.security_flags:
        parts.append("\nSecurity flags:")
        for flag in verdict.security_flags:
            parts.append(f"  - {flag}")

    parts.append(
        "\nThe staged files remain in the staging area for manual review."
    )

    return "\n".join(parts)
