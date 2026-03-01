"""Memory tools — LangChain tools for GRIM's persistent working memory.

These let agents read and update memory.md in the kronos-vault.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

# Vault path — set at startup by the memory agent factory
_vault_path: Path | None = None


def set_memory_vault_path(vault_path: Path) -> None:
    """Configure the vault path for memory tools."""
    global _vault_path
    _vault_path = vault_path


@tool
def read_grim_memory() -> str:
    """Read GRIM's persistent working memory (memory.md from kronos-vault).

    Returns the full memory content including sections:
    Active Objectives, Recent Topics, User Preferences,
    Key Learnings, Future Goals, Session Notes.
    """
    if _vault_path is None:
        return "Error: vault path not configured"
    from core.memory_store import read_memory
    content = read_memory(_vault_path)
    return content if content else "(memory is empty)"


@tool
def update_grim_memory(section: str, content: str) -> str:
    """Update a specific section of GRIM's persistent working memory.

    Args:
        section: The section name to update (e.g., "Recent Topics",
                 "Key Learnings", "User Preferences", "Future Goals",
                 "Session Notes", "Active Objectives")
        content: The new content for that section (markdown format)
    """
    if _vault_path is None:
        return "Error: vault path not configured"
    from core.memory_store import read_memory, update_section, write_memory
    current = read_memory(_vault_path)
    if not current:
        current = "# GRIM Working Memory\n"
    updated = update_section(current, section, content)
    write_memory(_vault_path, updated)
    return f"Updated section '{section}' in memory.md"


MEMORY_TOOLS = [read_grim_memory, update_grim_memory]
