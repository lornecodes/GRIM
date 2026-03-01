"""Audit Agent — reviews IronClaw staging output before acceptance.

The Audit Agent is the zero-trust review layer. When IronClaw executes a task
and writes output to the shared staging volume, this agent reviews every file
for security, correctness, and style before it's accepted into the workspace.

It has READ-ONLY access to the staging area — it can list and read files,
but cannot modify, accept, or reject them. That decision is made by the
integrate node based on the audit verdict.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.state import AgentResult, AuditVerdict, GrimState
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.staging_tools import STAGING_READ_TOOLS

logger = logging.getLogger(__name__)

AUDIT_SYSTEM_PREAMBLE = """\
You are the audit agent. Your job is to review files staged by the execution \
engine before they are accepted into the workspace.

## Review Criteria

1. **Security** — No hardcoded secrets, API keys, credentials, or tokens. \
No destructive commands (rm -rf, format, DROP TABLE). No suspicious downloads \
or outbound connections.

2. **Correctness** — File content matches the original task intent. Code has \
valid syntax. Output is complete (not truncated or partial).

3. **Style** — Follows project conventions where visible. No obvious code \
smell or anti-patterns. Reasonable file naming.

## Process

1. Call `staging_list` to see all files in the staging job
2. Call `staging_read` for each file to inspect its content
3. Evaluate each file against the review criteria
4. Return your verdict as a JSON block at the END of your response

## Verdict Format

Your final response MUST end with a JSON block (no other text after it):

```json
{
    "passed": true/false,
    "issues": ["blocking problem 1", "blocking problem 2"],
    "suggestions": ["non-blocking improvement 1"],
    "security_flags": ["any security concern"],
    "summary": "One-line verdict"
}
```

If there are no blocking issues, set passed=true. Only set passed=false for \
genuine problems — not stylistic preferences.
"""


class AuditAgent(BaseAgent):
    """Agent that reviews staged IronClaw output for security and correctness."""

    agent_name = "audit"

    def __init__(self, config: GrimConfig) -> None:
        # Audit gets: read-only staging access + read-only Kronos for context
        tools = list(STAGING_READ_TOOLS) + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)


def _parse_verdict(text: str) -> AuditVerdict:
    """Extract AuditVerdict from the agent's response text.

    Looks for a JSON block at the end of the response (inside ```json fences
    or as raw JSON).
    """
    # Try to find JSON block in fences
    import re

    json_match = re.search(r"```json\s*\n(.+?)\n\s*```", text, re.DOTALL)
    if json_match:
        raw = json_match.group(1)
    else:
        # Try raw JSON at end of text
        brace_idx = text.rfind("{")
        if brace_idx >= 0:
            raw = text[brace_idx:]
        else:
            return AuditVerdict(
                passed=False,
                issues=["Audit agent did not return a valid JSON verdict"],
                summary="Verdict parsing failed",
            )

    try:
        data = json.loads(raw)
        return AuditVerdict(
            passed=bool(data.get("passed", False)),
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            security_flags=data.get("security_flags", []),
            summary=data.get("summary", ""),
        )
    except (json.JSONDecodeError, TypeError):
        return AuditVerdict(
            passed=False,
            issues=["Audit agent returned invalid JSON verdict"],
            summary="Verdict parsing failed",
        )


def make_audit_agent(config: GrimConfig):
    """Create an Audit Agent callable for the graph.

    Returns an async function that takes GrimState and returns AgentResult
    with the AuditVerdict in details["verdict"].
    """
    agent = AuditAgent(config)

    async def audit_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Review staged IronClaw output."""
        job_id = state.get("staging_job_id")
        if not job_id:
            return AgentResult(
                agent="audit",
                success=False,
                summary="No staging job to audit",
            )

        # Build the task from original request + staging context
        messages = state.get("messages", [])
        original_task = ""
        if messages:
            last_msg = messages[-1]
            original_task = (
                last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            )

        task = (
            f"Review the staged output for job '{job_id}'.\n\n"
            f"Original task that produced this output:\n{original_task}\n\n"
            f"Use staging_list and staging_read to inspect all files."
        )

        # Context
        context: dict[str, Any] = {"staging_job_id": job_id}
        review_count = state.get("review_count", 0)
        if review_count > 0:
            context["review_attempt"] = (
                f"This is review attempt {review_count + 1}. "
                "Previous review failed — check if issues were addressed."
            )

        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            context["relevant_knowledge"] = ", ".join(
                f"{fdo.id} ({fdo.domain})" for fdo in knowledge_context[:5]
            )

        # The audit preamble is injected as the skill protocol
        result = await agent.execute(
            task=task,
            skill_protocol=AUDIT_SYSTEM_PREAMBLE,
            context=context,
            event_queue=event_queue,
        )

        # Parse the verdict from the agent's response
        verdict = _parse_verdict(result.summary)
        result.details["verdict"] = verdict

        logger.info(
            "Audit agent: %s — %s (job %s, attempt %d)",
            "PASSED" if verdict.passed else "FAILED",
            verdict.summary,
            job_id,
            review_count + 1,
        )

        return result

    return audit_agent_fn
