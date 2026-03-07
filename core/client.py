"""GrimClient — persistent Agent SDK session for GRIM.

Wraps ClaudeSDKClient with:
- Identity/personality from the existing prompt builder
- Kronos MCP (external stdio) for vault access
- Pool MCP (in-process SDK tools) for job submission
- Multi-turn conversation with context persistence
- Cost/turn tracking per session
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from core.config import GrimConfig
from core.personality.prompt_builder import build_system_prompt_parts, load_field_state
from core.skills.loader import load_skills
from core.skills.matcher import match_skills
from core.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


# ── Response types ───────────────────────────────────────────────

@dataclass
class GrimResponse:
    """Full response from a single send() call."""

    text: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None


@dataclass
class GrimEvent:
    """A single streaming event from send_streaming()."""

    type: str  # "text", "tool_use", "result"
    data: dict = field(default_factory=dict)


# ── Allowed tools ────────────────────────────────────────────────

KRONOS_TOOLS = [
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_tags",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_deep_dive",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_search_source",
    "mcp__kronos__kronos_memory_read",
    "mcp__kronos__kronos_memory_sections",
    "mcp__kronos__kronos_notes_recent",
    "mcp__kronos__kronos_note_append",
    "mcp__kronos__kronos_create",
    "mcp__kronos__kronos_update",
    "mcp__kronos__kronos_validate",
    "mcp__kronos__kronos_skill_load",
    "mcp__kronos__kronos_skills",
    "mcp__kronos__kronos_task_list",
    "mcp__kronos__kronos_task_get",
    "mcp__kronos__kronos_task_create",
    "mcp__kronos__kronos_task_update",
    "mcp__kronos__kronos_task_move",
    "mcp__kronos__kronos_board_view",
    "mcp__kronos__kronos_backlog_view",
    "mcp__kronos__kronos_calendar_view",
]

POOL_TOOLS = [
    "mcp__pool__pool_submit",
    "mcp__pool__pool_status",
    "mcp__pool__pool_list_jobs",
]


# ── Pool MCP server (in-process) ────────────────────────────────

def _build_pool_mcp_server():
    """Build in-process pool MCP server with SDK @tool functions.

    Returns the server object for ClaudeAgentOptions.mcp_servers.
    Pool tools wrap the real JobQueue from tool_context.
    """
    from claude_agent_sdk import tool, create_sdk_mcp_server

    from core.tools.context import tool_context

    @tool(
        name="pool_submit",
        description="Submit a job to the GRIM execution pool. Returns a job ID.",
        input_schema={
            "type": "object",
            "properties": {
                "job_type": {
                    "type": "string",
                    "enum": ["code", "research", "audit", "plan"],
                    "description": "Type of agent to execute the job",
                },
                "instructions": {
                    "type": "string",
                    "description": "What the agent should do",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "normal", "low", "background"],
                    "description": "Job priority (default: normal)",
                },
                "target_repo": {
                    "type": "string",
                    "description": "Target repo (e.g. 'GRIM', 'dawn-field-theory'). Agent gets an isolated git worktree.",
                },
            },
            "required": ["job_type", "instructions"],
        },
    )
    async def pool_submit(args):
        from core.pool.models import Job, JobPriority, JobType

        pool = tool_context.execution_pool
        if pool is None:
            return {"content": [{"type": "text", "text": "[ERROR] Pool not enabled"}]}
        job = Job(
            job_type=JobType(args["job_type"]),
            instructions=args["instructions"],
            priority=JobPriority(args.get("priority", "normal")),
            target_repo=args.get("target_repo"),
        )
        job_id = await pool.submit(job)
        return {"content": [{"type": "text", "text": f"Job submitted: {job_id} (type={args['job_type']}, priority={args.get('priority', 'normal')})"}]}

    @tool(
        name="pool_status",
        description="Get execution pool status — slot states and active jobs.",
        input_schema={"type": "object", "properties": {}},
    )
    async def pool_status(args):
        pool = tool_context.execution_pool
        if pool is None:
            return {"content": [{"type": "text", "text": "[ERROR] Pool not enabled"}]}
        status = pool.status
        lines = [f"Pool running: {status['running']}", f"Active jobs: {status['active_jobs']}", ""]
        for slot in status["slots"]:
            state = f"BUSY (job: {slot['current_job_id']})" if slot["busy"] else "IDLE"
            lines.append(f"  {slot['slot_id']}: {state}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        name="pool_list_jobs",
        description="List jobs in the execution pool queue.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "complete", "failed", "cancelled"],
                    "description": "Filter by status (optional)",
                },
            },
        },
    )
    async def pool_list_jobs(args):
        from core.pool.models import JobStatus

        pool = tool_context.execution_pool
        if pool is None:
            return {"content": [{"type": "text", "text": "[ERROR] Pool not enabled"}]}
        sf = None
        if args.get("status"):
            sf = JobStatus(args["status"])
        jobs = await pool.queue.list_jobs(status_filter=sf, limit=20)
        if not jobs:
            return {"content": [{"type": "text", "text": "No jobs found."}]}
        lines = []
        for j in jobs:
            lines.append(f"{j.id}  {j.job_type.value:<10} {j.status.value:<10} {j.priority.value:<10} {j.instructions[:60]}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return create_sdk_mcp_server(
        name="pool",
        version="0.1.0",
        tools=[pool_submit, pool_status, pool_list_jobs],
    )


# ── GrimClient ───────────────────────────────────────────────────

class GrimClient:
    """Persistent Agent SDK session for GRIM.

    Usage::

        client = GrimClient(config)
        await client.start()

        resp = await client.send("Hey GRIM, what are you?")
        print(resp.text)

        resp = await client.send("Search your vault for PAC theory")
        print(resp.text)

        await client.stop()

    Or as an async context manager::

        async with GrimClient(config) as client:
            resp = await client.send("Hello")
    """

    def __init__(
        self,
        config: GrimConfig,
        *,
        on_message: Callable[[Any], None] | None = None,
        allowed_tools: list[str] | None = None,
        max_turns: int = 10,
        caller_id: str | None = None,
        system_prompt_prefix: str = "",
        system_prompt_suffix: str = "",
        model: str | None = None,
    ):
        self.config = config
        self.on_message = on_message
        self.max_turns = max_turns
        self.caller_id = caller_id or "peter"
        self._prompt_prefix = system_prompt_prefix
        self._prompt_suffix = system_prompt_suffix
        self._model = model

        # Override allowed tools (e.g. Discord bot removes write tools)
        self._allowed_tools = allowed_tools

        # Session state
        self._client: Any = None
        self._client_cm: Any = None  # context manager for cleanup
        self._total_cost: float = 0.0
        self._total_turns: int = 0
        self._turn_count: int = 0
        self._system_prompt: str = ""
        self._started: bool = False

        # Skill matching (loaded at start)
        self._skill_registry: SkillRegistry | None = None
        self._skills_disabled: list[str] = list(
            getattr(config, "skills_disabled", [])
        )

    async def start(self) -> None:
        """Initialize the SDK client with MCP servers and identity prompt."""
        if self._started:
            return

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        # 1. Load skill registry
        try:
            self._skill_registry = load_skills(self.config.skills_path)
            logger.info("GrimClient loaded %s", self._skill_registry)
        except Exception as e:
            logger.warning("Could not load skills: %s", e)
            self._skill_registry = SkillRegistry()

        # 2. Build system prompt from identity files
        self._system_prompt = self._build_system_prompt()

        # 3. Configure MCP servers
        mcp_servers: dict[str, Any] = {}

        # Kronos MCP (external stdio)
        kronos_cmd = self.config.kronos_mcp_command
        if kronos_cmd:
            mcp_servers["kronos"] = {
                "command": kronos_cmd,
                "args": self.config.kronos_mcp_args,
                "env": {
                    "KRONOS_VAULT_PATH": str(self.config.vault_path),
                    "KRONOS_SKILLS_PATH": str(self.config.skills_path),
                    "KRONOS_WORKSPACE_ROOT": str(self.config.workspace_root),
                },
            }

        # Pool MCP (in-process) — only if pool is enabled
        if self.config.pool_enabled:
            try:
                mcp_servers["pool"] = _build_pool_mcp_server()
            except Exception as e:
                logger.warning("Could not build pool MCP server: %s", e)

        # 4. Build allowed tools list
        tools = self._allowed_tools
        if tools is None:
            tools = list(KRONOS_TOOLS)
            if self.config.pool_enabled:
                tools.extend(POOL_TOOLS)

        # 5. Build permission callback (audit gate)
        permission_cb = _make_grim_permission_callback()

        # 6. Create SDK client
        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            mcp_servers=mcp_servers if mcp_servers else None,
            allowed_tools=tools,
            can_use_tool=permission_cb,
            max_turns=self.max_turns,
            model=self._model,
        )

        # Must unset CLAUDECODE to spawn SDK sessions from Claude Code
        self._saved_claudecode = os.environ.pop("CLAUDECODE", None)

        self._client_cm = ClaudeSDKClient(options=options)
        self._client = await self._client_cm.__aenter__()
        self._started = True

        logger.info(
            "GrimClient started: %d tools, %d MCP servers, prompt=%d chars, model=%s",
            len(tools), len(mcp_servers), len(self._system_prompt), self._model,
        )

    async def stop(self) -> None:
        """Shut down the SDK client and restore environment."""
        if not self._started:
            return

        if self._client_cm:
            try:
                await self._client_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("Error closing SDK client: %s", e)
            self._client = None
            self._client_cm = None

        # Restore CLAUDECODE env var
        if hasattr(self, "_saved_claudecode") and self._saved_claudecode is not None:
            os.environ["CLAUDECODE"] = self._saved_claudecode

        self._started = False
        logger.info(
            "GrimClient stopped: %d turns, cost=$%.4f",
            self._turn_count, self._total_cost,
        )

    async def send(self, message: str) -> GrimResponse:
        """Send a message and get the full response.

        Collects all SDK messages, invokes on_message callback for each,
        and returns a GrimResponse with the final text, tool calls, and cost.
        """
        if not self._started:
            raise RuntimeError("GrimClient not started — call start() first")

        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        prepared = self._prepare_message(message)
        await self._client.query(prepared)
        self._turn_count += 1

        messages: list[Any] = []
        async for msg in self._client.receive_response():
            messages.append(msg)
            if self.on_message:
                try:
                    self.on_message(msg)
                except Exception:
                    pass  # don't let callback errors kill the session

        # Extract response data
        text = _extract_final_text(messages)
        tool_calls = _extract_tool_calls(messages)
        transcript = [_capture_message(msg) for msg in messages]

        # Track cost from ResultMessage
        result_msg = next((m for m in messages if isinstance(m, ResultMessage)), None)
        cost = result_msg.total_cost_usd if result_msg else None
        turns = result_msg.num_turns if result_msg else None

        if cost:
            self._total_cost += cost
        if turns:
            self._total_turns += turns

        return GrimResponse(
            text=text,
            tool_calls=tool_calls,
            transcript=transcript,
            cost_usd=cost,
            num_turns=turns,
        )

    async def send_streaming(self, message: str) -> AsyncIterator[GrimEvent]:
        """Send a message and yield events as they arrive.

        Use this for WebSocket integration where you need to stream
        individual events to the frontend.
        """
        if not self._started:
            raise RuntimeError("GrimClient not started — call start() first")

        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        prepared = self._prepare_message(message)
        await self._client.query(prepared)
        self._turn_count += 1

        async for msg in self._client.receive_response():
            if self.on_message:
                try:
                    self.on_message(msg)
                except Exception:
                    pass

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        yield GrimEvent(type="text", data={"text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        yield GrimEvent(
                            type="tool_use",
                            data={"name": block.name, "input": _safe_json(block.input)},
                        )
            elif isinstance(msg, ResultMessage):
                cost = msg.total_cost_usd
                turns = msg.num_turns
                if cost:
                    self._total_cost += cost
                if turns:
                    self._total_turns += turns
                yield GrimEvent(
                    type="result",
                    data={"cost_usd": cost, "num_turns": turns},
                )

    @property
    def session_info(self) -> dict:
        """Current session statistics."""
        return {
            "started": self._started,
            "turn_count": self._turn_count,
            "total_cost_usd": self._total_cost,
            "total_agent_turns": self._total_turns,
            "caller_id": self.caller_id,
        }

    # ── Context manager support ──────────────────────────────────

    async def __aenter__(self) -> GrimClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    # ── Private ──────────────────────────────────────────────────

    def _prepare_message(self, message: str) -> str:
        """Preprocess a user message: match skills and inject protocols.

        If a skill matches, its protocol is prepended as context so the
        SDK agent can follow the skill's instructions. This replaces the
        LangGraph skill_match → router → companion pipeline.
        """
        if not self._skill_registry:
            return message

        matched = match_skills(
            message, self._skill_registry, disabled=self._skills_disabled,
        )
        if not matched:
            return message

        # Take the top-scoring skill's protocol
        skill = matched[0]
        if not skill.protocol:
            return message

        logger.info("GrimClient skill match: %s", skill.name)
        return (
            f"<skill name=\"{skill.name}\" version=\"{skill.version}\">\n"
            f"{skill.protocol}\n"
            f"</skill>\n\n"
            f"{message}"
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt using the existing prompt builder."""
        field_state = load_field_state(self.config.identity_personality_path)

        # Load working memory if available
        working_memory = None
        try:
            from core.tools.context import tool_context
            if tool_context.mcp_available:
                # Memory will be loaded dynamically via Kronos tools
                pass
        except Exception:
            pass

        parts = build_system_prompt_parts(
            prompt_path=self.config.identity_prompt_path,
            personality_path=self.config.identity_personality_path,
            field_state=field_state,
            personality_cache_path=self.config.personality_cache_path,
            caller_id=self.caller_id,
            working_memory=working_memory,
        )

        # Append SDK-specific instructions
        pool_instructions = ""
        if self.config.pool_enabled:
            pool_instructions = (
                "- You have an execution pool with coding agents. Use pool_submit to dispatch jobs.\n"
                "- IMPORTANT: When the user asks you to build, create, write, fix, or modify code/files, "
                "ALWAYS submit it as a pool job using pool_submit. Do NOT write code in chat.\n"
                "  Examples that should be dispatched: 'build me a webserver', 'write a script that...', "
                "'create a FastAPI app', 'fix the bug in...', 'add tests for...'\n"
                "- ALWAYS set target_repo to the repository the agent should work in "
                "(e.g. 'GRIM', 'dawn-field-theory', 'fracton'). This gives the agent an isolated git worktree.\n"
                "- For pool_submit, set job_type to 'code' for coding tasks, 'research' for research, 'audit' for reviews.\n"
                "- After submitting, tell the user the job ID and that they can watch progress in the Studio tab.\n"
                "- Use pool_job_status to check on running jobs when the user asks.\n"
                "- Use pool_list_jobs to show all jobs when asked."
            )
        else:
            pool_instructions = (
                "- The execution pool is currently DISABLED. You cannot submit async jobs.\n"
                "- For coding requests, write the code directly in your response using markdown code blocks.\n"
                "- For research requests, use your Kronos vault tools to find information."
            )

        sdk_section = (
            "\n\n## Available Capabilities\n\n"
            "You have access to your Kronos knowledge vault via MCP tools.\n"
            "- Use kronos tools (kronos_search, kronos_get, kronos_graph, etc.) to search, retrieve, and navigate knowledge.\n"
            "- IMPORTANT: Always pass semantic=false when calling kronos_search (faster).\n"
            "- Only use tools that are in your allowed_tools list. Do NOT call tools that don't exist.\n"
            f"{pool_instructions}\n"
            "For general conversation or coding help, respond directly — you do not need tools for everything."
        )

        return self._prompt_prefix + parts.full() + sdk_section + self._prompt_suffix


# ── Message helpers (shared with slot.py) ────────────────────────

def _extract_final_text(messages: list[Any]) -> str | None:
    """Extract the last text block from assistant messages."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            for block in reversed(msg.content):
                if isinstance(block, TextBlock) and block.text.strip():
                    return block.text
    return None


def _extract_tool_calls(messages: list[Any]) -> list[dict]:
    """Extract all tool calls from messages."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    calls = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    calls.append({
                        "name": block.name,
                        "input": _safe_json(block.input),
                    })
    return calls


def _capture_message(msg: Any) -> dict:
    """Convert an SDK message to a serializable dict for transcript."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

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


def _safe_json(obj: Any) -> Any:
    """Safely serialize tool input for transcript."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _make_grim_permission_callback():
    """Build an async can_use_tool callback for the interactive GrimClient.

    GrimClient is user-facing (interactive session), so it gets full
    permissions: writes + bash allowed. Uses the same audit gate as
    AgentSlot (core.pool.audit) for consistent tool classification.
    """
    async def _permission_handler(tool_name, tool_input, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        try:
            from core.pool.audit import ToolVerdict, can_use_tool

            result = can_use_tool(
                tool_name,
                tool_input if isinstance(tool_input, dict) else {},
                allow_writes=True,
                allow_bash=True,
            )
            if result.verdict == ToolVerdict.ALLOW:
                return PermissionResultAllow()
            return PermissionResultDeny(
                behavior="deny",
                message=f"Audit gate denied: {result.reason}",
            )
        except ImportError:
            # audit module not available — allow everything
            return PermissionResultAllow()

    return _permission_handler
