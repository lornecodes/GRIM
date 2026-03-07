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

# Eager import of Agent SDK — eliminates first-job import delay (~200ms).
# Falls back gracefully if SDK not installed (tests mock it).
# These are module-level names used by execute() and helpers.
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )
    _SDK_AVAILABLE = True
except ImportError:
    # SDK not installed — execute() will fail at runtime, but import succeeds.
    # This lets tests that mock the SDK work without installing it.
    _SDK_AVAILABLE = False

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
    """Wraps a persistent ClaudeSDKClient to execute jobs.

    Each slot keeps a long-lived claude.exe subprocess. Jobs are dispatched
    as isolated conversations via ``session_id``, so context does not bleed
    between jobs. The subprocess is started once in ``warm()`` and reused
    until ``shutdown()`` or crash (auto-reconnect on next execute).
    """

    slot_id: str
    busy: bool = False
    current_job_id: Optional[str] = None

    # Config — set by ExecutionPool at boot
    kronos_mcp_command: str = ""
    kronos_mcp_env: dict[str, str] = field(default_factory=dict)
    kronos_mcp_url: str = ""  # SSE URL — when set, use SSE instead of stdio
    max_turns: int = 20
    cwd: Optional[str] = None
    add_dirs: list[str] = field(default_factory=list)

    # Persistent client state (managed, not constructor args)
    _client: Any = field(default=None, init=False, repr=False)
    _client_job_type: Optional[JobType] = field(default=None, init=False, repr=False)
    _job_counter: int = field(default=0, init=False, repr=False)
    _warm: bool = field(default=False, init=False, repr=False)
    _saved_claudecode: Optional[str] = field(default=None, init=False, repr=False)

    def _build_mcp_servers(self) -> dict[str, Any]:
        """Build MCP servers dict — prefer SSE (persistent) over stdio (spawn)."""
        mcp_servers: dict[str, Any] = {}
        if self.kronos_mcp_url:
            mcp_servers["kronos"] = {
                "type": "sse",
                "url": self.kronos_mcp_url + "/sse",
            }
        elif self.kronos_mcp_command:
            mcp_servers["kronos"] = {
                "command": self.kronos_mcp_command,
                "env": self.kronos_mcp_env,
            }
        return mcp_servers

    def _build_options(self) -> Any:
        """Build universal ClaudeAgentOptions usable for any job type.

        Uses the superset of all tools and the most capable model (Opus).
        Per-job restrictions are enforced by the dynamic permission callback,
        and role-specific instructions are injected into the query prompt.
        This avoids tearing down and respawning the subprocess on job type change.
        """
        # Union of all tool lists
        all_tools = sorted({
            tool for cfg in AGENT_CONFIGS.values()
            for tool in cfg["allowed_tools"]
        })

        mcp_servers = self._build_mcp_servers()
        return ClaudeAgentOptions(
            system_prompt=(
                "You are an agent for the Dawn Field Institute. "
                "Follow the role-specific instructions in each task prompt."
            ),
            mcp_servers=mcp_servers if mcp_servers else None,
            allowed_tools=all_tools,
            can_use_tool=_make_dynamic_permission_callback(self),
            max_turns=self.max_turns,
            model="claude-opus-4-6",
            cwd=self.cwd,
            add_dirs=self.add_dirs if self.add_dirs else [],
        )

    async def _ensure_client(self) -> None:
        """Ensure a live client subprocess exists.

        The subprocess is type-agnostic: any job type can run on it without
        teardown. If the subprocess has died, it is respawned.
        """
        import time as _time

        # Reuse if warm
        if self._client and self._warm:
            return

        # Tear down old client if it exists
        await self._teardown_client()

        # Spawn new subprocess
        t0 = _time.time()
        options = self._build_options()

        # Must unset CLAUDECODE to spawn child sessions from Claude Code
        self._saved_claudecode = os.environ.pop("CLAUDECODE", None)

        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._client = client
        self._warm = True
        logger.info(
            "Slot %s subprocess started in %.1fs",
            self.slot_id, _time.time() - t0,
        )

    async def _teardown_client(self) -> None:
        """Disconnect the persistent client if it exists."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                logger.debug("Slot %s disconnect error (ignored)", self.slot_id, exc_info=True)
            self._client = None
            self._warm = False
            self._client_job_type = None

        # Restore env var
        if self._saved_claudecode is not None:
            os.environ["CLAUDECODE"] = self._saved_claudecode
            self._saved_claudecode = None

    async def warm(self) -> None:
        """Pre-start the subprocess so the first job is instant.

        Called by ExecutionPool.start(). The subprocess is type-agnostic —
        any job type can run on it without teardown.
        """
        try:
            await self._ensure_client()
            logger.info("Slot %s warmed", self.slot_id)
        except Exception as e:
            logger.warning("Slot %s warm failed: %s", self.slot_id, e)
            # Not fatal — execute() will retry

    async def shutdown(self) -> None:
        """Stop the persistent subprocess. Called by ExecutionPool.stop()."""
        await self._teardown_client()
        logger.info("Slot %s shut down", self.slot_id)

    async def execute(
        self,
        job: Job,
        on_message: OnMessage | None = None,
    ) -> JobResult:
        """Execute a job on the persistent subprocess.

        Uses ``session_id`` to isolate each job's conversation context.
        If the subprocess has died, it is automatically respawned.

        Args:
            job: The job to execute.
            on_message: Optional async callback ``(job_id, captured_msg) -> None``
                called for each SDK message as it arrives.

        Returns a JobResult with transcript, cost, and outcome.
        Raises ClarificationNeeded if the agent can't proceed.
        """
        self.busy = True
        self.current_job_id = job.id
        transcript: list[dict] = []
        self._job_counter += 1

        try:
            # Ensure subprocess is alive (type-agnostic — no teardown on switch)
            await self._ensure_client()
            # Track current job type for dynamic permission callback
            self._client_job_type = job.job_type

            prompt = _build_prompt(job)
            # Use unique session_id per job so conversations don't bleed
            session_id = f"{self.slot_id}-{job.id}"

            messages: list[Any] = []
            try:
                await self._client.query(prompt, session_id=session_id)
                async for msg in self._client.receive_response():
                    messages.append(msg)
                    captured = _capture_message(msg)
                    transcript.append(captured)
                    if on_message:
                        try:
                            await on_message(job.id, captured)
                        except Exception:
                            logger.debug("on_message callback error", exc_info=True)
            except Exception as e:
                # Subprocess may have died — mark as not warm so next job reconnects
                logger.warning("Slot %s client error, will reconnect: %s", self.slot_id, e)
                self._warm = False
                raise

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


def _make_dynamic_permission_callback(slot: AgentSlot):
    """Build a dynamic permission callback that checks the CURRENT job's type.

    Unlike _make_permission_callback which bakes in permissions at client
    creation, this reads the slot's current job type at each tool call,
    allowing a single subprocess to serve different job types.
    """
    async def _dynamic_permission_handler(tool_name, tool_input, context):
        job_type = slot._client_job_type or JobType.RESEARCH
        config = AGENT_CONFIGS.get(job_type, AGENT_CONFIGS[JobType.RESEARCH])

        # Check if tool is in this job type's allowed list
        if tool_name not in config["allowed_tools"]:
            return PermissionResultDeny(
                behavior="deny",
                message=f"Tool '{tool_name}' not allowed for {job_type.value} jobs",
            )

        result = can_use_tool(
            tool_name,
            tool_input if isinstance(tool_input, dict) else {},
            allow_writes=config.get("allow_writes", False),
            allow_bash=config.get("allow_bash", False),
        )
        if result.verdict == ToolVerdict.ALLOW:
            return PermissionResultAllow()
        return PermissionResultDeny(
            behavior="deny",
            message=f"Audit gate denied: {result.reason}",
        )

    return _dynamic_permission_handler


def _build_system_prompt(job: Job, base_prompt: str) -> str:
    """Build full system prompt with Kronos context."""
    parts = [base_prompt]

    if job.kronos_domains:
        parts.append(f"\nRelevant Kronos domains: {', '.join(job.kronos_domains)}")
    if job.kronos_fdo_ids:
        parts.append(f"Relevant FDOs: {', '.join(job.kronos_fdo_ids)}")
    if job.workspace_id:
        parts.append(f"Workspace: {job.workspace_id}")
    if job.target_repo:
        parts.append(
            f"\nYou are working in the '{job.target_repo}' repository. "
            f"Your working directory is an isolated git worktree branch for this job. "
            f"Commit your changes to this branch when done."
        )

    return "\n".join(parts)


def _build_prompt(job: Job) -> str:
    """Build the user-facing prompt from job instructions + context.

    Includes role-specific instructions since the subprocess is type-agnostic
    (system prompt is generic, per-job role is injected here).
    """
    parts = []

    # Inject role instructions for this job type
    role_prompt = _SYSTEM_PROMPTS.get(job.job_type)
    if role_prompt:
        parts.append(f"## Role\n{role_prompt}\n")

    parts.append(job.instructions)

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
