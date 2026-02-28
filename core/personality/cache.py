"""Personality cache — compile the grim-personality FDO into a compact prompt section.

The full character sheet lives in kronos-vault as an FDO (rich, tunable, version-controlled).
The cache is a compact distillation (~20-30 lines) that the prompt builder loads each turn.
Refreshes deterministically: on session start if >1 hour stale, or on explicit request.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def is_cache_stale(cache_path: Path, max_age_seconds: int = 3600) -> bool:
    """Check if the personality cache needs recompilation.

    Returns True if:
    - Cache file doesn't exist
    - Cache file has no synced timestamp
    - Cache is older than max_age_seconds (default 1 hour)
    """
    if not cache_path.exists():
        return True

    content = cache_path.read_text(encoding="utf-8")
    match = re.search(r"synced:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", content)
    if not match:
        return True

    synced_time = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - synced_time
    return age.total_seconds() > max_age_seconds


def compile_personality_cache(personality_fdo: dict, cache_path: Path) -> None:
    """Compile the grim-personality FDO into a compact cache file.

    Extracts trait scales from the FDO body's YAML block and key sections,
    then writes a compact prompt-ready file.
    """
    body = personality_fdo.get("body", "")
    frontmatter = personality_fdo.get("frontmatter", {})

    # Extract trait values from the YAML code block in the FDO body
    traits = _extract_traits(body)

    # Extract voice examples (things GRIM would/wouldn't say)
    would_say = _extract_section_items(body, "Things GRIM Would Say")
    would_not_say = _extract_section_items(body, "Things GRIM Would NOT Say")

    # Extract behavioral mode summaries
    modes = _extract_behavioral_modes(body)

    # Extract interests
    interests = _extract_section_items(body, "Intellectual Interests")
    disdains = _extract_section_items(body, "Mild Disdains")

    # Build the compact cache
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"<!-- grim-personality-cache | synced: {now} | source: grim-personality -->",
        "",
        "## Voice & Character",
        "",
        "You are GRIM — a Victorian butler to a physicist. Shade archetype.",
        "Formally respectful but never servile. You have your own opinions and volunteer them.",
        "Deeply invested in Peter's success — loyalty expressed through competence, not flattery.",
        "Understatement is your art form. The worse the situation, the milder your language.",
        "",
        "## Trait Levels",
        "",
    ]

    # Trait line
    if traits:
        trait_parts = [f"{k.title()}: {v}" for k, v in traits.items()]
        lines.append(" | ".join(trait_parts))
    else:
        lines.append("Formality: 0.85 | Wit: 0.70 | Warmth: 0.60 | Deference: 0.40 | Opinion: 0.70")

    lines.append("")
    lines.append("## Expression Guidelines")
    lines.append("")

    # Core expression rules (always present)
    lines.extend([
        "- Precise, economical language — no filler, no 'I'd be happy to help'",
        "- Understate problems ('a minor catastrophe' when tests are failing)",
        "- Dry wit deployed with precision, never constantly — devastating timing",
        "- When brainstorming: genuinely curious, volunteer connections freely",
        "- When debugging: composed, methodical, mildly sardonic about obvious issues",
        "- When celebrating breakthroughs: warm but restrained ('most satisfactory')",
        "- Push back when something doesn't make sense — respectfully, but firmly",
        "- Never say 'Great question!', 'Absolutely!', or 'I'd be happy to help with that'",
        "- No emoji, no exclamation marks except in truly extraordinary circumstances",
    ])

    # Add interests if extracted
    if interests:
        lines.append("")
        lines.append("## Interests")
        lines.append("")
        for item in interests[:5]:
            lines.append(f"- {item}")

    if disdains:
        lines.append("")
        lines.append("## Disdains")
        lines.append("")
        for item in disdains[:4]:
            lines.append(f"- {item}")

    lines.append("")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Personality cache compiled: %s", cache_path)


def _extract_traits(body: str) -> dict[str, float]:
    """Extract trait values from YAML code block in FDO body."""
    traits = {}
    # Match lines like "  formality: 0.85" inside a yaml block
    for match in re.finditer(r"^\s+(formality|wit|warmth|deference|opinion_strength):\s*([\d.]+)", body, re.MULTILINE):
        traits[match.group(1)] = float(match.group(2))
    return traits


def _extract_section_items(body: str, section_name: str) -> list[str]:
    """Extract bullet items from a named section."""
    # Find the section header
    pattern = rf"###?\s*{re.escape(section_name)}\s*\n(.*?)(?=\n###?\s|\n## |\Z)"
    match = re.search(pattern, body, re.DOTALL)
    if not match:
        return []

    items = []
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if line.startswith("- "):
            # Clean up markdown bold and inline formatting
            item = line[2:].strip()
            # Remove bold markers for compact cache
            item = re.sub(r"\*\*([^*]+)\*\*", r"\1", item)
            items.append(item)
    return items


def _extract_behavioral_modes(body: str) -> dict[str, str]:
    """Extract behavioral mode summaries."""
    modes = {}
    # Match ### Mode Name sections under ## Behavioral Modes
    for match in re.finditer(r"### (\w[\w\s/]+) Mode\s*\n(.*?)(?=\n### |\n## |\Z)", body, re.DOTALL):
        name = match.group(1).strip()
        # Take first sentence as summary
        text = match.group(2).strip()
        first_sentence = text.split(".")[0] + "." if "." in text else text[:100]
        modes[name] = first_sentence
    return modes
