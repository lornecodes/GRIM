"""Planning Companion node — GRIM's work planning and scoping mode.

A planning-focused mode activated when the graph router detects planning
intent (scoping, task breakdown, sprint planning, backlog grooming).
Key differences from the research and personal companions:

- Prompt emphasizes work breakdown, scoping, and draft-by-default
- Has full task write tools + vault read tools
- Includes skill protocols and objectives (planning needs context)
- Self-validates created items before presenting to user
- No delegation path — goes straight to integrate after responding

This is a graph-level branch, not a dispatched agent.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from core.config import GrimConfig
from core.nodes.companion import _execute_tool, _safe_invoke
from core.state import GrimState
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.kronos_tasks import TASK_ALL_TOOLS

if TYPE_CHECKING:
    from core.reasoning_cache import ReasoningCache

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 8  # Planning needs more steps: search vault, check board, create items

# Planning tools: full task CRUD + vault read
PLANNING_TOOLS = [*TASK_ALL_TOOLS, *COMPANION_TOOLS]

# UI roster metadata for the planning companion node
NODE_METADATA = {
    "id": "planning_companion",
    "name": "Planning",
    "role": "planner",
    "description": "Task breakdown, sprint planning, board management, scoping",
    "tools": [t.name for t in PLANNING_TOOLS],
    "color": "#a78bfa",
    "tier": "grim",
    "toggleable": False,
}

PLANNING_MODE_PREAMBLE = """\
## Mode: Planning Companion

You are in planning mode. Your focus is scoping, breaking down, and organizing
work into structured items on the task board.

### Core Rules

1. **Vault-First**: Before creating any item, search the vault for existing work:
   - Use kronos_search to check if similar work exists
   - Use kronos_task_list to see existing stories in the target feature
   - Use kronos_board_view to see what's currently in flight

2. **Duplicate Check**: Before creating a story, verify no similar story exists
   in the same feature. Check both title and description overlap.

3. **Draft by Default**: All items you create MUST use status="draft" and
   created_by="agent:planning". The user will promote drafts to "new" when
   approved. Never create items with status="new" unless the user explicitly
   asks you to skip drafting.

4. **Acceptance Criteria Required**: Every story MUST have at least 2 acceptance
   criteria. If the user didn't specify them, derive reasonable ones from the
   request.

5. **Right-Sized Work**:
   - Story: 0.5 to 5 days of work. If larger, break into multiple stories.
   - Task: 0.25 to 1 day. Each story should have 2-6 tasks.
   - If scope exceeds 10 days total, suggest creating a new feature first.

6. **Always Execute**: If you have a tool that can do it, do it. Never say
   you cannot perform an action if a tool exists for it.

### Workflow

1. Understand the request — what is the user trying to accomplish?
2. Search for existing related work in the vault and on the board
3. Identify or confirm the target feature (feat-*)
4. Check for duplicate stories
5. Create story(ies) with status="draft", created_by="agent:planning"
6. Break each story into tasks
7. Self-validate: check titles, estimates, acceptance criteria
8. Present the draft items to the user for approval
9. Only the user promotes items from draft to new

### Self-Validation

After creating draft items, validate your own output:
- Every story has: title (>10 chars), priority, estimate, acceptance criteria (>=2)
- No duplicate titles within the feature
- Total estimate is reasonable (warn if >10 days)
- Individual stories are right-sized (warn if >5 days, suggest splitting)

### Output Format

After creating items, present a clear summary:
- Story ID, title, estimate, key acceptance criteria
- Task breakdown with estimates
- Total estimate for all new work
- Note that items are in DRAFT and need user promotion to activate

### Future: Code-Level Planning

Once work items are approved and promoted, the next step is code-level planning
— exploring the actual codebase to design implementation strategy before
execution. This handoff to the Codebase Agent is not yet built but is the
bridge to IronClaw execution (Phase 3 architecture).
"""


def make_planning_companion_node(
    config: GrimConfig,
    reasoning_cache: "ReasoningCache | None" = None,
):
    """Create a planning companion node closure with config."""

    tool_map = {t.name: t for t in PLANNING_TOOLS}

    async def planning_companion_node(
        state: GrimState, run_config: RunnableConfig = None
    ) -> dict:
        """Generate GRIM's response in planning companion mode.

        Uses the same LLM as the research companion but with a
        planning-focused system prompt and task management tools.
        """
        selected_model = state.get("selected_model") or config.model

        llm = ChatAnthropic(
            model=selected_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            default_headers={"X-Caller-ID": "grim"},
        )
        llm_with_tools = llm.bind_tools(PLANNING_TOOLS) if PLANNING_TOOLS else llm

        logger.info("Planning companion: using model %s", selected_model)

        messages = list(state.get("messages", []))
        knowledge_context = state.get("knowledge_context", [])
        recent_notes = state.get("recent_notes", [])

        # Build system prompt — includes skills and objectives (planning needs context)
        from core.personality.prompt_builder import build_system_prompt_parts

        matched_skills = state.get("matched_skills", [])
        skill_protocols = state.get("skill_protocols", {})
        objectives = state.get("objectives", [])

        parts = build_system_prompt_parts(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=state.get("field_state"),
            knowledge_context=knowledge_context,
            matched_skills=matched_skills,
            objectives=objectives,
            personality_cache_path=config.personality_cache_path,
            caller_id=state.get("caller_id"),
            caller_context=state.get("caller_context"),
            recent_notes=recent_notes,
        )

        # Prepend planning mode preamble
        static_with_preamble = f"{PLANNING_MODE_PREAMBLE}\n{parts.static}"

        # Inject skill protocols if available
        protocol_text = ""
        for skill_name, protocol in skill_protocols.items():
            if skill_name in ("task-manage", "sprint-plan"):
                protocol_text += f"\n### Skill Protocol: {skill_name}\n{protocol}\n"

        if protocol_text:
            static_with_preamble += f"\n{protocol_text}"

        system_content = [
            {
                "type": "text",
                "text": f"[SYSTEM INSTRUCTIONS — follow exactly]\n{static_with_preamble}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if parts.dynamic:
            system_content.append({
                "type": "text",
                "text": f"{parts.dynamic}\n[END SYSTEM INSTRUCTIONS]",
            })
        else:
            system_content[0]["text"] += "\n[END SYSTEM INSTRUCTIONS]"

        llm_messages = [
            HumanMessage(content=system_content),
            AIMessage(content="Understood. I am GRIM in planning mode. I will scope work, "
                      "create draft items, and validate before presenting."),
        ] + messages

        logger.info(
            "Planning companion: generating response (%d messages in history)",
            len(messages),
        )

        # Tool-calling loop (more steps than personal, planning needs multiple tool calls)
        new_messages: list[Any] = []
        got_text_response = False

        # Emit trace event if queue available
        event_queue = None
        if run_config and hasattr(run_config, "get"):
            configurable = run_config.get("configurable", {})
            if isinstance(configurable, dict):
                event_queue = configurable.get("agent_event_queue")

        if event_queue:
            try:
                event_queue.put_nowait({
                    "type": "node_start",
                    "node": "planning_companion",
                    "cat": "planning",
                })
            except Exception:
                pass

        for step in range(MAX_TOOL_STEPS):
            response = await _safe_invoke(llm_with_tools, llm_messages, run_config)
            new_messages.append(response)
            llm_messages.append(response)

            if hasattr(response, "tool_calls") and response.tool_calls:
                logger.info(
                    "Planning companion: tool calls at step %d: %s",
                    step,
                    [tc["name"] for tc in response.tool_calls],
                )
                for tc in response.tool_calls:
                    if event_queue:
                        try:
                            event_queue.put_nowait({
                                "type": "tool_call",
                                "node": "planning_companion",
                                "cat": "planning",
                                "tool": tc["name"],
                            })
                        except Exception:
                            pass
                    tool_result = await _execute_tool(tool_map, tc)
                    new_messages.append(tool_result)
                    llm_messages.append(tool_result)
            else:
                got_text_response = True
                break

        if not got_text_response:
            logger.info("Planning companion: tool loop exhausted, forcing final response")
            response = await _safe_invoke(llm, llm_messages, run_config)
            new_messages.append(response)

        if event_queue:
            try:
                event_queue.put_nowait({
                    "type": "node_end",
                    "node": "planning_companion",
                    "cat": "planning",
                })
            except Exception:
                pass

        # Track topics for evolution
        topics = list(state.get("session_topics", []))
        if knowledge_context:
            new_topics = [fdo.id for fdo in knowledge_context if fdo.id not in topics]
            topics = topics + new_topics[:5]

        return {
            "messages": new_messages,
            "session_topics": topics,
        }

    return planning_companion_node
