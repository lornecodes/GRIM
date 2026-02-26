"""Skill match node — match user message against skill triggers every turn.

This is the skill-centric routing heart. Skills are the priority for
orchestration — they define HOW things get done.
"""

from __future__ import annotations

import logging

from core.skills.matcher import match_skills
from core.skills.registry import SkillRegistry
from core.state import GrimState, SkillContext

logger = logging.getLogger(__name__)


def make_skill_match_node(registry: SkillRegistry):
    """Create a skill match node closure with the skill registry."""

    async def skill_match_node(state: GrimState) -> dict:
        """Match the latest message against all loaded skill triggers."""
        messages = state.get("messages", [])
        if not messages:
            return {"matched_skills": [], "skill_protocols": {}}

        last_msg = messages[-1]
        message = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        matched = match_skills(message, registry)

        # Convert to state-friendly format
        skill_contexts = [
            SkillContext(
                name=s.name,
                version=s.version,
                description=s.description,
                permissions=s.permissions,
                triggers=s.triggers,
            )
            for s in matched
        ]

        skill_protocols = {s.name: s.protocol for s in matched}

        if matched:
            logger.info(
                "Skill match: %s",
                ", ".join(f"{s.name} (write={s.requires_write})" for s in matched),
            )

        return {
            "matched_skills": skill_contexts,
            "skill_protocols": skill_protocols,
        }

    return skill_match_node
