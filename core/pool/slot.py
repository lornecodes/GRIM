"""AgentSlot — Claude Agent SDK wrapper for job execution.

Each slot wraps a ClaudeSDKClient session, configured per JobType with
appropriate tools, system prompt, and Kronos MCP access.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

# Callback type for streaming agent output per-message
OnMessage = Callable[[str, dict], Coroutine[Any, Any, None]]

from core.pool.audit import ToolVerdict, can_use_tool
from core.pool.models import (
    ClarificationNeeded,
    Job,
    JobResult,
    JobStatus,
    JobType,
)

logger = logging.getLogger(__name__)

# ── Agent type configurations ────────────────────────────────────
# Maps JobType to tools + system prompts (patterns from spikes 01-04)

_CODE_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    # Kronos MCP tools (prefixed by SDK)
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_find_implementation",
    "mcp__kronos__kronos_update",
]

_RESEARCH_TOOLS = [
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_tags",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_deep_dive",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_search_source",
    "mcp__kronos__kronos_find_implementation",
    "mcp__kronos__kronos_git_recent",
]

_AUDIT_TOOLS = [
    "Read", "Grep", "Glob",
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_graph",
]

_PLAN_TOOLS = [
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_navigate",
]

_INDEX_TOOLS = [
    "Read", "Glob",
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_update",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_find_implementation",
    "mcp__kronos__kronos_validate_sources",
]

_SYSTEM_PROMPTS: dict[JobType, str] = {
    JobType.CODE: (
        "You are a coding agent for the Dawn Field Institute. "
        "Write clean, tested code following project standards. "
        "Use Kronos to check project conventions before coding. "
        "Always write tests for new functions. "
        "After creating or modifying files, call kronos_update on the relevant FDO "
        "to add new files to source_paths (repo, path, type fields)."
    ),
    JobType.RESEARCH: (
        "You are a research agent for the Dawn Field Institute. "
        "Use Kronos tools to search, retrieve, and synthesize knowledge. "
        "Cite FDO IDs when referencing vault content. "
        "Provide thorough, well-sourced answers."
    ),
    JobType.AUDIT: (
        "You are an audit agent for the Dawn Field Institute. "
        "Review code changes for quality, security, and correctness. "
        "Check against project standards (type hints, docstrings, tests). "
        "Flag security vulnerabilities (SQL injection, hardcoded secrets, etc.)."
    ),
    JobType.PLAN: (
        "You are a planning agent for the Dawn Field Institute. "
        "Use Kronos to understand project state and propose implementation plans. "
        "Break complex tasks into concrete, actionable steps."
    ),
    JobType.INDEX: (
        "You are an indexing agent for the Dawn Field Institute. "
        "Scan the given repository to discover source files and map them to Kronos FDOs. "
        "Use kronos_find_implementation to discover symbols, then kronos_update to add "
        "source_paths entries (repo, path, type) to the matching FDOs. "
        "Match files to FDOs by tag/title similarity."
    ),
}

# Model selection: Opus for high-stakes work (code, planning, audit),
# Sonnet for fast/cheap research. Interactive GrimClient uses auto-detection.
AGENT_CONFIGS: dict[JobType, dict[str, Any]] = {
    JobType.CODE: {"allowed_tools": _CODE_TOOLS, "system_prompt": _SYSTEM_PROMPTS[JobType.CODE], "model": "claude-opus-4-6", "allow_writes": True, "allow_bash": True},
    JobType.RESEARCH: {"allowed_tools": _RESEARCH_TOOLS, "system_prompt": _SYSTEM_PROMPTS[JobType.RESEARCH], "model": "claude-sonnet-4-6", "allow_writes": False, "allow_bash": False},
    JobType.AUDIT: {"allowed_tools": _AUDIT_TOOLS, "system_prompt": _SYSTEM_PROMPTS[JobType.AUDIT], "model": "claude-opus-4-6", "allow_writes": False, "allow_bash": False},
    JobType.PLAN: {"allowed_tools": _PLAN_TOOLS, "system_prompt": _SYSTEM_PROMPTS[JobType.PLAN], "model": "claude-opus-4-6", "allow_writes": False, "allow_bash": False},
    JobType.INDEX: {"allowed_tools": _INDEX_TOOLS, "system_prompt": _SYSTEM_PROMPTS[JobType.INDEX], "model": "claude-sonnet-4-6", "allow_writes": True, "allow_bash": False},
}


# ── AgentSlot ────────────────────────────────────────────────────

@dataclass
class AgentSlot:
    """Wraps a ClaudeSDKClient to execute a single job.

    Each slot is a logical execution unit. The ExecutionPool manages
    multiple slots for concurrent job execution.
    """

    slot_id: str
    busy: bool = False
    current_job_id: Optional[str] = None

    # Config — set by ExecutionPool at boot
    kronos_mcp_command: str = ""
    kronos_mcp_env: dict[str, str] = field(default_factory=dict)
    max_turns: int = 20
    cwd: Optional[str] = None

    async def execute(
        self,
        job: Job,
        on_message: OnMessage | None = None,
    ) -> JobResult:
        """Execute a job using Claude Agent SDK.

        Args:
            job: The job to execute.
            on_message: Optional async callback ``(job_id, captured_msg) -> None``
                called for each SDK message as it arrives. Used for live
                streaming to WebSocket clients.

        Returns a JobResult with transcript, cost, and outcome.
        Raises ClarificationNeeded if the agent can't proceed.
        """
        # Lazy import — claude_agent_sdk is an optional dependency
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )

        self.busy = True
        self.current_job_id = job.id
        transcript: list[dict] = []

        try:
            config = AGENT_CONFIGS[job.job_type]
            prompt = _build_prompt(job)
            system = _build_system_prompt(job, config["system_prompt"])

            # Build MCP servers dict (only if Kronos is configured)
            mcp_servers: dict[str, Any] = {}
            if self.kronos_mcp_command:
                mcp_servers["kronos"] = {
                    "command": self.kronos_mcp_command,
                    "env": self.kronos_mcp_env,
                }

            # Build permission callback from audit gate config
            allow_writes = config.get("allow_writes", False)
            allow_bash = config.get("allow_bash", False)
            permission_cb = _make_permission_callback(
                allow_writes=allow_writes, allow_bash=allow_bash,
            )

            options = ClaudeAgentOptions(
                system_prompt=system,
                mcp_servers=mcp_servers if mcp_servers else None,
                allowed_tools=config["allowed_tools"],
                can_use_tool=permission_cb,
                max_turns=self.max_turns,
                model=config.get("model"),
            )

            # Must unset CLAUDECODE to spawn child sessions from Claude Code
            saved_claudecode = os.environ.pop("CLAUDECODE", None)

            try:
                messages: list[Any] = []
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
                    async for msg in client.receive_response():
                        messages.append(msg)
                        captured = _capture_message(msg)
                        transcript.append(captured)
                        if on_message:
                            try:
                                await on_message(job.id, captured)
                            except Exception:
                                logger.debug("on_message callback error", exc_info=True)
            finally:
                # Restore env var
                if saved_claudecode is not None:
                    os.environ["CLAUDECODE"] = saved_claudecode

            # Extract result from messages
            result_msg = next((m for m in messages if isinstance(m, ResultMessage)), None)
            result_text = _extract_final_text(messages)
            cost_usd = result_msg.total_cost_usd if result_msg else None
            num_turns = result_msg.num_turns if result_msg else None

            logger.info(
                "Slot %s completed job %s: turns=%s, cost=$%s",
                self.slot_id, job.id, num_turns, f"{cost_usd:.4f}" if cost_usd else "?",
            )

            return JobResult(
                job_id=job.id,
                success=True,
                result=result_text,
                transcript=transcript,
                cost_usd=cost_usd,
                num_turns=num_turns,
            )

        except ClarificationNeeded:
            raise  # Let pool handle this
        except Exception as e:
            logger.error("Slot %s job %s failed: %s", self.slot_id, job.id, e)
            return JobResult(
                job_id=job.id,
                success=False,
                error=str(e),
                transcript=transcript,
            )
        finally:
            self.busy = False
            self.current_job_id = None


# ── Helpers ──────────────────────────────────────────────────────

def _make_permission_callback(
    *, allow_writes: bool, allow_bash: bool,
):
    """Build an async can_use_tool callback for the Claude Agent SDK.

    Wraps audit.can_use_tool to gate tool calls based on job type permissions.
    Returns PermissionResultAllow/Deny from claude_agent_sdk.
    """
    async def _permission_handler(tool_name, tool_input, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        result = can_use_tool(
            tool_name,
            tool_input if isinstance(tool_input, dict) else {},
            allow_writes=allow_writes,
            allow_bash=allow_bash,
        )
        if result.verdict == ToolVerdict.ALLOW:
            return PermissionResultAllow()
        return PermissionResultDeny(
            behavior="deny",
            message=f"Audit gate denied: {result.reason}",
        )

    return _permission_handler


def _build_system_prompt(job: Job, base_prompt: str) -> str:
    """Build full system prompt with Kronos context."""
    parts = [base_prompt]

    if job.kronos_domains:
        parts.append(f"\nRelevant Kronos domains: {', '.join(job.kronos_domains)}")
    if job.kronos_fdo_ids:
        parts.append(f"Relevant FDOs: {', '.join(job.kronos_fdo_ids)}")
    if job.workspace_id:
        parts.append(f"Workspace: {job.workspace_id}")

    return "\n".join(parts)


def _build_prompt(job: Job) -> str:
    """Build the user-facing prompt from job instructions + context."""
    parts = [job.instructions]

    if job.plan:
        parts.append(f"\n## Implementation Plan\n{job.plan}")

    if job.clarification_answer:
        parts.append(
            f"\n## Clarification\n"
            f"Q: {job.clarification_question}\n"
            f"A: {job.clarification_answer}"
        )

    return "\n".join(parts)


def _capture_message(msg: Any) -> dict:
    """Convert an SDK message to a serializable dict for transcript."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    if isinstance(msg, AssistantMessage):
        blocks = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text[:2000]})
            elif isinstance(block, ToolUseBlock):
                blocks.append({
                    "type": "tool_use",
                    "name": block.name,
                    "input": _safe_json(block.input),
                })
            else:
                blocks.append({"type": type(block).__name__})
        return {"role": "assistant", "content": blocks}
    elif isinstance(msg, ResultMessage):
        return {
            "role": "result",
            "num_turns": msg.num_turns,
            "cost_usd": msg.total_cost_usd,
        }
    else:
        return {"role": type(msg).__name__}


def _extract_final_text(messages: list[Any]) -> str | None:
    """Extract the last text block from assistant messages."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            for block in reversed(msg.content):
                if isinstance(block, TextBlock) and block.text.strip():
                    return block.text
    return None


def _safe_json(obj: Any) -> Any:
    """Safely serialize tool input for transcript."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
