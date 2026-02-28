"""User cache — compile a people FDO into a compact prompt section.

The owner profile (peter.md) lives in kronos-vault as an FDO. The cache is a
compact distillation (~10-15 lines) injected into the system prompt so GRIM
knows who it's talking to. Same staleness pattern as personality cache.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# Hardcoded fallback for Peter if vault/cache unavailable.
# Keeps GRIM functional even when Kronos is down.
PETER_FALLBACK = """## Caller: Peter Lorne (owner)

Physicist, DFI founder. Thinks in recursive structures and information theory.
Builds things to understand them — theory and implementation inseparable.
Values directness and intellectual honesty. Push back when needed.
Current focus: DFT milestone 4, GRIM Phase 2, Fracton integration."""


def is_user_cache_stale(cache_path: Path, max_age_seconds: int = 3600) -> bool:
    """Check if the user cache needs recompilation.

    Returns True if cache is missing, has no timestamp, or is older than max_age.
    Reuses the same pattern as personality cache staleness check.
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


def compile_user_cache(user_fdo: dict, cache_path: Path) -> None:
    """Compile a people FDO into a compact cache file.

    Extracts key sections (role, working style, communication, focus)
    into a prompt-ready file.
    """
    body = user_fdo.get("body", "")
    frontmatter = user_fdo.get("frontmatter", {})

    title = frontmatter.get("title", "Unknown Caller")
    fdo_id = frontmatter.get("id", "unknown")
    role = frontmatter.get("role", "unknown")

    # Extract key sections from the FDO body
    working_style = _extract_section_items(body, "Working Style")
    communication = _extract_section_items(body, "Communication Preferences")
    current_focus = _extract_section_items(body, "Current Priorities")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"<!-- grim-user-cache | synced: {now} | source: {fdo_id} -->",
        "",
        f"## Caller: {title} ({role})",
        "",
    ]

    if working_style:
        for item in working_style[:5]:
            lines.append(f"- {item}")
        lines.append("")

    if communication:
        lines.append("### Communication")
        for item in communication[:5]:
            lines.append(f"- {item}")
        lines.append("")

    if current_focus:
        lines.append("### Current Focus")
        for item in current_focus[:4]:
            lines.append(f"- {item}")
        lines.append("")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("User cache compiled: %s (source: %s)", cache_path, fdo_id)


def compile_caller_summary(caller_fdo: dict) -> str:
    """Compile a non-owner caller FDO into a prompt section (no disk cache).

    Used for services (IronClaw) and friends — one-time vault lookup per session.
    """
    body = caller_fdo.get("body", "")
    frontmatter = caller_fdo.get("frontmatter", {})

    title = frontmatter.get("title", "Unknown")
    role = frontmatter.get("role", "unknown")
    fdo_type = frontmatter.get("type", "unknown")

    # Extract context and communication style
    context_items = _extract_section_items(body, "Context")
    comm_items = _extract_section_items(body, "Communication Style")

    lines = [f"## Caller: {title} ({fdo_type}, {role})", ""]

    if context_items:
        for item in context_items[:3]:
            lines.append(f"- {item}")
        lines.append("")

    if comm_items:
        lines.append("### Communication Style")
        for item in comm_items[:3]:
            lines.append(f"- {item}")

    return "\n".join(lines)


def _extract_section_items(body: str, section_name: str) -> list[str]:
    """Extract bullet items from a named section."""
    pattern = rf"###?\s*{re.escape(section_name)}\s*\n(.*?)(?=\n###?\s|\n## |\Z)"
    match = re.search(pattern, body, re.DOTALL)
    if not match:
        return []

    items = []
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if line.startswith("- "):
            item = line[2:].strip()
            item = re.sub(r"\*\*([^*]+)\*\*", r"\1", item)
            items.append(item)
    return items
