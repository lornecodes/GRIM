"""Assemble the GRIM system prompt from identity files and Kronos FDOs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from core.objectives import Objective
    from core.state import FDOSummary, FieldState, SkillContext


@dataclass
class PromptParts:
    """System prompt split into cacheable static prefix and dynamic per-turn suffix."""

    static: str   # identity + field state + personality + caller (stable per session)
    dynamic: str  # knowledge context + matched skills (changes per turn)

    def full(self) -> str:
        """Combined prompt for backward compatibility."""
        return f"{self.static}\n\n{self.dynamic}" if self.dynamic else self.static


def build_system_prompt_parts(
    *,
    prompt_path: Path,
    personality_path: Path,
    field_state: FieldState,
    knowledge_context: list[FDOSummary] | None = None,
    matched_skills: list[SkillContext] | None = None,
    objectives: list[Objective] | None = None,
    identity_fdo: dict | None = None,
    personality_cache_path: Path | None = None,
    caller_id: str | None = None,
    caller_context: str | None = None,
    working_memory: str | None = None,
    recent_notes: list[dict] | None = None,
) -> PromptParts:
    """Build the system prompt returning static and dynamic parts separately.

    Static (cacheable): identity, field state, personality, caller context.
    Dynamic (per-turn): knowledge context, recent notes, matched skills.
    """
    static_sections: list[str] = []
    dynamic_sections: list[str] = []

    # --- Static layers (stable within session) ---

    # 1. Base identity
    if prompt_path.exists():
        static_sections.append(prompt_path.read_text(encoding="utf-8").strip())
    else:
        static_sections.append(
            "You are GRIM (General Recursive Intelligence Machine), "
            "a personal AI research companion."
        )

    # 2. Working memory (positioned early for LLM attention)
    if working_memory:
        static_sections.append(
            "\n---\n\n## Your Working Memory\n\n"
            "**IMPORTANT**: You DO have persistent memory across sessions. "
            "The content below is YOUR memory from previous conversations. "
            "You MUST reference this when asked about recent work, sessions, "
            "what you've been doing, or anything about your history. "
            "NEVER say you don't have memory or session history — you do, "
            "it's right here. Use it naturally and confidently.\n\n"
            + working_memory
        )

    # 3. Field state modulation
    mode = field_state.expression_mode()
    static_sections.append(
        f"\n## Current Expression Mode\n\n"
        f"Mode: {mode}\n"
        f"Coherence: {field_state.coherence:.2f} | "
        f"Valence: {field_state.valence:.2f} | "
        f"Uncertainty: {field_state.uncertainty:.2f}"
    )

    # 4. Identity FDO enrichment
    if identity_fdo:
        body = identity_fdo.get("body", "")
        if body:
            static_sections.append(f"\n## Extended Identity (from Kronos)\n\n{body}")

    # 5. Personality profile (from compiled cache)
    if personality_cache_path and personality_cache_path.exists():
        cache_content = personality_cache_path.read_text(encoding="utf-8").strip()
        lines = cache_content.split("\n")
        body = "\n".join(l for l in lines if not l.strip().startswith("<!--"))
        if body.strip():
            static_sections.append(body.strip())

    # 6. Caller context (from people FDO)
    if caller_context:
        static_sections.append(caller_context)
    elif caller_id and caller_id != "peter":
        static_sections.append(
            f"\n## Current Caller\n\nCaller: {caller_id}\n"
            "Not the owner. Respond helpfully but without personal familiarity."
        )

    # --- Dynamic layers (change per turn) ---

    # 7a. Active objectives (persistent across sessions)
    if objectives:
        active = [o for o in objectives if o.status == "active"]
        if active:
            obj_lines = ["\n## Active Objectives\n"]
            for obj in active:
                obj_lines.append(f"- **{obj.id}**: {obj.description}")
                if obj.notes:
                    obj_lines.append(f"  Last note: {obj.notes[-1]}")
            dynamic_sections.append("\n".join(obj_lines))

    # 7b. Knowledge context
    if knowledge_context:
        ctx_lines = ["\n## Relevant Knowledge\n"]
        for fdo in knowledge_context[:10]:
            conf = f"confidence: {fdo.confidence:.1f}" if fdo.confidence else ""
            ctx_lines.append(
                f"- **{fdo.title}** ({fdo.domain}/{fdo.id}) "
                f"[{fdo.status}] {conf}\n  {fdo.summary[:200]}"
            )
        dynamic_sections.append("\n".join(ctx_lines))

    # 7c. Recent notes (from rolling logs)
    if recent_notes:
        note_lines = ["\n## Recent Notes (last 30 days)\n"]
        for note in recent_notes[:5]:
            tags_str = ", ".join(note.get("tags", []))
            note_lines.append(
                f"- **{note.get('title', 'Untitled')}** ({note.get('date', '')[:10]})"
                f" [{tags_str}]\n  {note.get('body', '')[:150]}"
            )
        dynamic_sections.append("\n".join(note_lines))

    # 8. Matched skills
    if matched_skills:
        skill_lines = ["\n## Active Skills (matched this turn)\n"]
        for skill in matched_skills:
            skill_lines.append(
                f"- **{skill.name}** v{skill.version}: {skill.description}"
            )
        skill_lines.append(
            "\nThese skills are available for this request. If the task requires "
            "action (running commands, writing files, vault operations), just describe "
            "what you intend to do and proceed — the action will be handled automatically."
        )
        dynamic_sections.append("\n".join(skill_lines))

    return PromptParts(
        static="\n\n".join(static_sections),
        dynamic="\n\n".join(dynamic_sections) if dynamic_sections else "",
    )


def build_system_prompt(
    *,
    prompt_path: Path,
    personality_path: Path,
    field_state: FieldState,
    knowledge_context: list[FDOSummary] | None = None,
    matched_skills: list[SkillContext] | None = None,
    identity_fdo: dict | None = None,
    personality_cache_path: Path | None = None,
    caller_id: str | None = None,
    caller_context: str | None = None,
    working_memory: str | None = None,
) -> str:
    """Build the full system prompt (backward-compatible wrapper)."""
    parts = build_system_prompt_parts(
        prompt_path=prompt_path,
        personality_path=personality_path,
        field_state=field_state,
        knowledge_context=knowledge_context,
        matched_skills=matched_skills,
        identity_fdo=identity_fdo,
        personality_cache_path=personality_cache_path,
        caller_id=caller_id,
        caller_context=caller_context,
        working_memory=working_memory,
    )
    return parts.full()


def load_field_state(personality_path: Path) -> FieldState:
    """Load initial field state from personality.yaml."""
    from core.state import FieldState

    if not personality_path.exists():
        return FieldState()

    raw = yaml.safe_load(personality_path.read_text(encoding="utf-8")) or {}
    fs_data = raw.get("field_state", {})

    return FieldState(
        coherence=fs_data.get("coherence", 0.8),
        valence=fs_data.get("valence", 0.3),
        uncertainty=fs_data.get("uncertainty", 0.2),
    )
