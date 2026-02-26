"""Skill loader — discover and load skills from GRIM/skills/ at boot."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from core.skills.registry import Skill, SkillConsumer, SkillRegistry

logger = logging.getLogger(__name__)


def load_skills(skills_dir: Path) -> SkillRegistry:
    """Walk the skills directory, parse each manifest.yaml + protocol.md.

    Returns a populated SkillRegistry ready for per-turn matching.

    Expected structure per skill:
        skills/skill-name/
            manifest.yaml
            protocol.md
    """
    registry = SkillRegistry()

    if not skills_dir.exists():
        logger.warning("Skills directory not found: %s", skills_dir)
        return registry

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        manifest_path = skill_dir / "manifest.yaml"
        if not manifest_path.exists():
            logger.debug("Skipping %s — no manifest.yaml", skill_dir.name)
            continue

        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.exception("Failed to parse manifest: %s", manifest_path)
            continue

        # Load protocol
        protocol_file = manifest.get("entry_point", "protocol.md")
        protocol_path = skill_dir / protocol_file
        protocol = ""
        if protocol_path.exists():
            protocol = protocol_path.read_text(encoding="utf-8")
        else:
            logger.warning("Protocol file not found: %s", protocol_path)

        # Parse triggers — normalize to {keywords: [...], intents: [...]} format
        raw_triggers = manifest.get("triggers", [])
        triggers = _normalize_triggers(raw_triggers, manifest.get("name", skill_dir.name), manifest.get("description", ""))

        # Parse consumers
        consumers = _parse_consumers(manifest.get("consumers", {}))

        # Parse quality gates
        quality_gates = manifest.get("quality_gates", [])
        if not isinstance(quality_gates, list):
            quality_gates = []

        # Build skill
        skill = Skill(
            name=manifest.get("name", skill_dir.name),
            version=manifest.get("version", "0.0"),
            description=manifest.get("description", "").strip(),
            protocol=protocol,
            entry_point=protocol_file,
            skill_type=manifest.get("type", "instruction-protocol"),
            permissions=manifest.get("permissions", []),
            triggers=triggers,
            inputs=manifest.get("inputs", {}),
            outputs=manifest.get("outputs", {}),
            consumers=consumers,
            quality_gates=quality_gates,
            raw_manifest=manifest,
        )

        registry.register(skill)
        logger.info(
            "Loaded skill: %s v%s (%d consumers: %s)",
            skill.name,
            skill.version,
            len(consumers),
            ", ".join(c.name for c in consumers) or "unspecified",
        )

    logger.info("Skill registry: %s", registry)
    return registry


def _parse_consumers(raw: dict | None) -> list[SkillConsumer]:
    """Parse the consumers block from a manifest.

    Format in YAML:
        consumers:
          grim:
            role: recognition
            description: ...
            reads: [triggers, quality_gates]
          memory-agent:
            role: execution
            description: ...
            reads: [protocol.md, inputs, outputs]
    """
    if not raw or not isinstance(raw, dict):
        return []

    consumers = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        consumers.append(SkillConsumer(
            name=name,
            role=spec.get("role", "execution"),
            description=spec.get("description", ""),
            reads=spec.get("reads", []),
        ))
    return consumers

    logger.info("Skill registry: %s", registry)
    return registry


def _normalize_triggers(
    raw_triggers: list | dict | None,
    skill_name: str,
    description: str,
) -> dict[str, list[str]]:
    """Normalize trigger formats to {keywords: [...], intents: [...]}.

    Handles:
    - dict with keywords/intents keys (already normalized)
    - list of dicts like [{proactive: "desc"}, {explicit: "desc"}]
    - list of strings
    - None / empty
    """
    if isinstance(raw_triggers, dict) and ("keywords" in raw_triggers or "intents" in raw_triggers):
        # Already in expected format
        return raw_triggers

    keywords: list[str] = []
    intents: list[str] = []

    if isinstance(raw_triggers, list):
        for item in raw_triggers:
            if isinstance(item, dict):
                # Format: {trigger_type: "description text"}
                for key, desc in item.items():
                    # Extract meaningful keywords from the description
                    if isinstance(desc, str):
                        _extract_keywords_from_desc(desc, keywords)
                    intents.append(str(key))
            elif isinstance(item, str):
                keywords.append(item)

    # Always include the full skill name as a phrase keyword
    # (NOT individual words — "kronos" and "relate" alone cause false positives)
    if skill_name and skill_name not in keywords:
        keywords.append(skill_name)

    # Extract from description if we have very few keywords
    if len(keywords) < 3 and description:
        _extract_keywords_from_desc(description, keywords)

    return {"keywords": keywords, "intents": intents}


def _extract_keywords_from_desc(desc: str, keywords: list[str]) -> None:
    """Extract trigger-worthy keywords from a description string."""
    # Look for quoted phrases like 'remember this', 'save this'
    import re
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", desc)
    for phrase in quoted:
        phrase_lower = phrase.lower().strip()
        if phrase_lower and phrase_lower not in keywords:
            keywords.append(phrase_lower)
