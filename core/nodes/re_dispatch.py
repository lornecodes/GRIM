"""Re-dispatch node — prepare feedback from failed audit for IronClaw retry.

When the audit agent rejects staged output, this node builds structured
feedback from the audit verdict, increments the review counter, and clears
the verdict for the next audit cycle. The graph then loops back through
dispatch → audit_gate → audit.
"""

from __future__ import annotations

import logging

from core.state import AuditVerdict, GrimState

logger = logging.getLogger(__name__)


async def re_dispatch_node(state: GrimState) -> dict:
    """Build feedback from failed audit and prepare for re-dispatch."""
    verdict: AuditVerdict | None = state.get("audit_verdict")
    review_count = state.get("review_count", 0)

    feedback_parts = [
        f"## Audit Review Failed (attempt {review_count + 1})",
        "",
    ]

    if verdict:
        if verdict.issues:
            feedback_parts.append("### Blocking Issues")
            for issue in verdict.issues:
                feedback_parts.append(f"- {issue}")
            feedback_parts.append("")

        if verdict.suggestions:
            feedback_parts.append("### Suggestions")
            for suggestion in verdict.suggestions:
                feedback_parts.append(f"- {suggestion}")
            feedback_parts.append("")

        if verdict.security_flags:
            feedback_parts.append("### Security Flags")
            for flag in verdict.security_flags:
                feedback_parts.append(f"- ⚠ {flag}")
            feedback_parts.append("")

        feedback_parts.append(
            "Fix these issues and re-execute. Write output to the same staging path."
        )

    feedback = "\n".join(feedback_parts)

    logger.info(
        "Re-dispatch: preparing feedback for attempt %d (job %s, %d issues)",
        review_count + 2,
        state.get("staging_job_id"),
        len(verdict.issues) if verdict else 0,
    )

    return {
        "audit_feedback": feedback,
        "review_count": review_count + 1,
        "audit_verdict": None,  # clear for next cycle
    }


def audit_decision(state: GrimState) -> str:
    """Decide: pass, fail (re-dispatch), or escalate to user.

    Returns:
        "pass" — audit passed, proceed to integrate
        "fail" — audit failed, re-dispatch with feedback
        "escalate" — max retries exceeded, show user what happened
    """
    verdict: AuditVerdict | None = state.get("audit_verdict")
    review_count = state.get("review_count", 0)
    max_reviews = state.get("max_reviews", 3)

    if verdict is None or verdict.passed:
        logger.info("Audit decision: PASS")
        return "pass"

    if review_count >= max_reviews:
        logger.info(
            "Audit decision: ESCALATE (max retries %d reached)", max_reviews
        )
        return "escalate"

    logger.info(
        "Audit decision: FAIL (attempt %d/%d)", review_count + 1, max_reviews
    )
    return "fail"
