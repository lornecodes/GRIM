"""Assemble the GRIM system prompt from identity files and Kronos FDOs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from core.state import FDOSummary, FieldState, SkillContext


def build_system_prompt(
    *,
    prompt_path: Path,
    personality_path: Path,
    field_state: FieldState,
    knowledge_context: list[FDOSummary] | None = None,
    matched_skills: list[SkillContext] | None = None,
    identity_fdo: dict | None = None,
) -> str:
    """Build the full system prompt for the GRIM companion.

    Layers (in order):
    1. Base identity from system_prompt.md
    2. Field state modulation (expression mode)
    3. Identity FDO enrichment from Kronos (if available)
    4. Knowledge context summary (if available)
    5. Matched skill context (if available)
    """
    sections: list[str] = []

    # 1. Base identity
    if prompt_path.exists():
        sections.append(prompt_path.read_text(encoding="utf-8").strip())
    else:
        sections.append(
            "You are GRIM (General Recursive Intelligence Machine), "
            "a personal AI research companion."
        )

    # 2. Field state modulation
    mode = field_state.expression_mode()
    sections.append(
        f"\n## Current Expression Mode\n\n"
        f"Mode: {mode}\n"
        f"Coherence: {field_state.coherence:.2f} | "
        f"Valence: {field_state.valence:.2f} | "
        f"Uncertainty: {field_state.uncertainty:.2f}"
    )

    # 3. Identity FDO enrichment
    if identity_fdo:
        body = identity_fdo.get("body", "")
        if body:
            sections.append(f"\n## Extended Identity (from Kronos)\n\n{body}")

    # 4. Knowledge context
    if knowledge_context:
        ctx_lines = ["\n## Relevant Knowledge\n"]
        for fdo in knowledge_context[:10]:  # cap at 10 for context window
            conf = f"confidence: {fdo.confidence:.1f}" if fdo.confidence else ""
            ctx_lines.append(
                f"- **{fdo.title}** ({fdo.domain}/{fdo.id}) "
                f"[{fdo.status}] {conf}\n  {fdo.summary[:200]}"
            )
        sections.append("\n".join(ctx_lines))

    # 5. Matched skills (read-only awareness — companion sees but doesn't execute)
    if matched_skills:
        skill_lines = ["\n## Active Skills (matched this turn)\n"]
        for skill in matched_skills:
            skill_lines.append(
                f"- **{skill.name}** v{skill.version}: {skill.description}"
            )
        skill_lines.append(
            "\nNote: You are the THINKER. If these skills require action "
            "(vault writes, code execution), formulate the request and the "
            "Router will delegate to the appropriate agent."
        )
        sections.append("\n".join(skill_lines))

    return "\n\n".join(sections)


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
