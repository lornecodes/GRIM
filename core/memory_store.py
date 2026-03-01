"""GRIM persistent memory — read/write/parse memory.md in kronos-vault.

The memory file lives at {vault_path}/memory.md and is structured as
markdown with H2 sections. Each section can be read/updated independently.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_FILENAME = "memory.md"


def memory_path(vault_path: Path) -> Path:
    """Return the full path to memory.md in the vault."""
    return vault_path / MEMORY_FILENAME


def read_memory(vault_path: Path) -> str:
    """Read the full memory.md content. Returns empty string if missing."""
    p = memory_path(vault_path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read memory.md")
        return ""


def write_memory(vault_path: Path, content: str) -> None:
    """Write content to memory.md in the vault."""
    p = memory_path(vault_path)
    try:
        p.write_text(content, encoding="utf-8")
        logger.info("Updated memory.md (%d chars)", len(content))
    except Exception:
        logger.exception("Failed to write memory.md")


def parse_memory_sections(content: str) -> dict[str, str]:
    """Parse markdown H2 sections into a dict.

    Returns {"Active Objectives": "...", "Recent Topics": "...", ...}
    HTML comments are stripped from section content.
    """
    if not content.strip():
        return {}

    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            if current_name is not None:
                sections[current_name] = _clean_section("\n".join(current_lines))
            current_name = match.group(1).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        sections[current_name] = _clean_section("\n".join(current_lines))

    return sections


def update_section(content: str, section_name: str, new_text: str) -> str:
    """Replace a specific H2 section's content, preserving the rest.

    If the section doesn't exist, appends it at the end.
    """
    pattern = re.compile(
        rf"(^##\s+{re.escape(section_name)}\s*\n)"  # section header
        rf"(.*?)"                                      # section body
        rf"(?=^##\s|\Z)",                              # next section or end
        re.MULTILINE | re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        replacement = f"{match.group(1)}{new_text.strip()}\n\n"
        return content[: match.start()] + replacement + content[match.end() :]

    # Section not found — append
    return content.rstrip() + f"\n\n## {section_name}\n{new_text.strip()}\n"


def _clean_section(text: str) -> str:
    """Strip HTML comments and leading/trailing whitespace from section text."""
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return cleaned.strip()
