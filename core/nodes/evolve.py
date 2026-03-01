"""Evolve node — update field state, extract objectives, capture session learning.

Runs after every turn. Computes field state drift based on conversation
topics and outcomes, saves snapshots. Also extracts/updates persistent
objectives via a lightweight LLM call (every 5 turns to limit cost).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.config import GrimConfig
from core.context import format_messages_for_summary
from core.objectives import (
    OBJECTIVE_EXTRACTION_PROMPT,
    Objective,
    load_objectives,
    save_objectives,
)
from core.state import FieldState, GrimState

logger = logging.getLogger(__name__)

# Extract objectives every N turns to limit LLM calls
_OBJECTIVE_EXTRACT_INTERVAL = 5


def make_evolve_node(config: GrimConfig, mcp_session: Any = None):
    """Create an evolve node closure with config and optional MCP session."""

    # Track turn count for objective extraction interval
    _turn_counter = {"count": 0}

    async def evolve_node(state: GrimState) -> dict:
        """Evolve field state based on session activity."""
        field_state: FieldState | None = state.get("field_state")
        if field_state is None:
            return {}

        topics = state.get("session_topics", [])
        knowledge_context = state.get("knowledge_context", [])

        # Capture start state before modulation
        start_snapshot = field_state.snapshot()

        # Modulate based on knowledge confidence
        if knowledge_context:
            avg_confidence = sum(f.confidence for f in knowledge_context) / len(knowledge_context)
            field_state.modulate(confidence=avg_confidence)

        # Modulate toward more coherent state after productive sessions
        if len(topics) > 3:
            field_state.coherence = min(1.0, field_state.coherence + 0.02)
            field_state.valence = min(1.0, field_state.valence + 0.05)

        end_snapshot = field_state.snapshot()

        # Save evolution snapshot
        if config.evolution_dir:
            _save_snapshot(
                config.evolution_dir,
                start=start_snapshot,
                end=end_snapshot,
                topics=topics,
                session_start=state.get("session_start"),
            )

        logger.info(
            "Evolve: coherence %.2f→%.2f, valence %.2f→%.2f, uncertainty %.2f→%.2f",
            start_snapshot["coherence"], end_snapshot["coherence"],
            start_snapshot["valence"], end_snapshot["valence"],
            start_snapshot["uncertainty"], end_snapshot["uncertainty"],
        )

        # Objective extraction + memory update (periodic, not every turn)
        _turn_counter["count"] += 1
        messages = list(state.get("messages", []))

        if (
            len(messages) >= 4
            and _turn_counter["count"] % _OBJECTIVE_EXTRACT_INTERVAL == 0
        ):
            await _extract_and_save_objectives(messages, state, config)
            await _update_working_memory(messages, state, config, mcp_session)

        return {"field_state": field_state}

    return evolve_node


def _save_snapshot(
    evolution_dir: Path,
    start: dict,
    end: dict,
    topics: list[str],
    session_start: datetime | None,
) -> None:
    """Save a field state evolution snapshot to disk."""
    try:
        evolution_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        filename = now.strftime("%Y-%m-%d_session_%H%M.yaml")

        snapshot = {
            "session_start": session_start.isoformat() if session_start else None,
            "session_end": now.isoformat(),
            "field_state_start": start,
            "field_state_end": end,
            "topics": topics,
        }

        (evolution_dir / filename).write_text(
            yaml.dump(snapshot, default_flow_style=False),
            encoding="utf-8",
        )
        logger.debug("Saved evolution snapshot: %s", filename)

    except Exception:
        logger.exception("Failed to save evolution snapshot")


async def _extract_and_save_objectives(
    messages: list, state: GrimState, config: GrimConfig
) -> None:
    """Extract objectives from conversation via LLM and save to disk."""
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage

        llm = ChatAnthropic(
            model=config.model,
            temperature=0.0,
            max_tokens=1024,
            default_headers={"X-Caller-ID": "grim"},
        )

        current_objectives = state.get("objectives", [])
        current_obj_text = "None" if not current_objectives else json.dumps(
            [o.to_dict() if hasattr(o, "to_dict") else o for o in current_objectives],
            indent=2,
        )

        # Use last 20 messages for extraction (not full history)
        recent = messages[-20:]
        conversation_text = format_messages_for_summary(recent)

        prompt = OBJECTIVE_EXTRACTION_PROMPT.format(
            current_objectives=current_obj_text,
            conversation=conversation_text,
            max_objectives=config.objectives_max_active,
        )

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()

        # Parse JSON from response (handle markdown code blocks)
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(raw_text)
        obj_list = parsed.get("objectives", [])

        # Merge with existing objectives (preserve timestamps)
        existing_map = {}
        for o in current_objectives:
            obj = o if isinstance(o, Objective) else Objective.from_dict(o)
            existing_map[obj.id] = obj

        now = datetime.now().isoformat()
        updated: list[Objective] = []
        for raw_obj in obj_list:
            obj_id = raw_obj.get("id", "")
            if not obj_id:
                continue

            if obj_id in existing_map:
                existing = existing_map[obj_id]
                existing.status = raw_obj.get("status", existing.status)
                existing.updated = now
                new_notes = raw_obj.get("notes", [])
                if new_notes:
                    existing.notes.extend(new_notes)
                updated.append(existing)
            else:
                updated.append(Objective(
                    id=obj_id,
                    description=raw_obj.get("description", ""),
                    status=raw_obj.get("status", "active"),
                    created=now,
                    updated=now,
                    source_session=str(state.get("session_start", "")),
                    notes=raw_obj.get("notes", []),
                ))

        save_objectives(updated, config.objectives_path)
        logger.info("Evolve: extracted %d objectives", len(updated))

    except json.JSONDecodeError:
        logger.warning("Evolve: failed to parse objective extraction JSON")
    except Exception:
        logger.exception("Evolve: objective extraction failed")


MEMORY_UPDATE_PROMPT = """You are updating GRIM's persistent working memory file.
Given the current memory content and recent conversation, produce an updated version.

Rules:
- Keep the same markdown structure with ## section headers
- Update "Active Objectives" to reflect current objectives (sync from provided list)
- Add new entries to "Recent Topics" (keep last 10, each with ISO timestamp)
- Extract any user preferences mentioned and add to "User Preferences" (no duplicates)
- Add confirmed insights to "Key Learnings" (no duplicates)
- Add a brief session note to "Session Notes" (keep last 10)
- Never remove existing entries unless they're clearly outdated
- Keep entries concise — memory should be scannable

Current memory.md:
```
{current_memory}
```

Current objectives:
{objectives_text}

Recent conversation (last messages):
{conversation}

Return ONLY the updated markdown content for memory.md, nothing else."""


async def _update_working_memory(
    messages: list, state: GrimState, config: GrimConfig,
    mcp_session: Any = None,
) -> None:
    """Update GRIM's persistent working memory via LLM analysis.

    Uses MCP tools when available (writes persist across container rebuilds).
    Falls back to direct file I/O when MCP is unavailable.
    """
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage

        # Read current memory via MCP or fallback
        current_memory = ""
        if mcp_session is not None:
            try:
                result = await mcp_session.call_tool("kronos_memory_read", {})
                if hasattr(result, "content") and result.content:
                    data = json.loads(result.content[0].text)
                    current_memory = data.get("content", "")
            except Exception:
                logger.debug("MCP memory read failed, falling back to file I/O")

        if not current_memory:
            from core.memory_store import read_memory
            current_memory = read_memory(config.vault_path)

        if not current_memory:
            current_memory = "# GRIM Working Memory\n\n(empty)"

        llm = ChatAnthropic(
            model=config.model,
            temperature=0.0,
            max_tokens=2048,
            default_headers={"X-Caller-ID": "grim"},
        )

        # Build objectives text
        objectives = state.get("objectives", [])
        if objectives:
            obj_lines = []
            for o in objectives:
                obj_dict = o.to_dict() if hasattr(o, "to_dict") else o
                status = obj_dict.get("status", "active") if isinstance(obj_dict, dict) else "active"
                desc = obj_dict.get("description", str(o)) if isinstance(obj_dict, dict) else str(o)
                obj_lines.append(f"- [{status}] {desc}")
            objectives_text = "\n".join(obj_lines)
        else:
            objectives_text = "(none)"

        recent = messages[-10:]
        conversation_text = format_messages_for_summary(recent)

        prompt = MEMORY_UPDATE_PROMPT.format(
            current_memory=current_memory,
            objectives_text=objectives_text,
            conversation=conversation_text,
        )

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        updated_content = response.content.strip()

        # Strip markdown code fences if present
        if updated_content.startswith("```"):
            updated_content = updated_content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        if updated_content and "## " in updated_content:
            # Write via MCP (persists on host) or fallback to file I/O
            written = False
            if mcp_session is not None:
                try:
                    await mcp_session.call_tool(
                        "kronos_memory_update",
                        {"full_content": updated_content},
                    )
                    written = True
                    logger.info("Evolve: updated working memory via MCP (%d chars)", len(updated_content))
                except Exception:
                    logger.debug("MCP memory write failed, falling back to file I/O")

            if not written:
                from core.memory_store import write_memory
                write_memory(config.vault_path, updated_content)
                logger.info("Evolve: updated working memory via file I/O (%d chars)", len(updated_content))
        else:
            logger.warning("Evolve: memory update produced invalid content, skipping")

    except Exception:
        logger.exception("Evolve: working memory update failed")
