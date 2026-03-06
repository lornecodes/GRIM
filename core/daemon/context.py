"""ContextBuilder — assembles rich agent instructions from vault + filesystem.

Replaces the thin _build_instructions() in ManagementEngine with a
budget-managed, multi-section instruction document that gives pool agents
enough context to start working immediately while telling them about
Kronos tools for self-directed research.

No LLM calls — purely mechanical assembly from FDOs, meta.yaml, and source files.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Section budgets (chars) ──────────────────────────────────────

MAX_CHARS = 8000
MAX_ADR_CHARS = 2000
MAX_SOURCE_CHARS = 2000
MAX_ORIENTATION_CHARS = 1000
SOURCE_SNIPPET_LINES = 30
MAX_SOURCE_FILES = 5

# ── Research prompt templates per job type ────────────────────────

_RESEARCH_PROMPT_BASE = """\
## Research Tools

You have Kronos MCP tools for self-directed research:
- `kronos_search(query)` — search the knowledge vault for concepts and FDOs
- `kronos_get(id)` — read a specific FDO by ID
- `kronos_navigate(path)` — explore repo directory structure via meta.yaml
- `kronos_read_source(repo, path)` — read source files in any repo
- `kronos_find_implementation(repo, symbol)` — find where a function/class is defined

Use these to explore unfamiliar code, find patterns, and understand architecture \
before making changes. Search the vault first if unsure about conventions."""

_RESEARCH_PROMPTS: dict[str, str] = {
    "code": _RESEARCH_PROMPT_BASE,
    "research": _RESEARCH_PROMPT_BASE + """
- `kronos_graph(id, depth)` — traverse relationships between FDOs
- `kronos_deep_dive(query)` — gather all source material for a concept
- `kronos_search_source(query, pattern)` — grep across a concept's source files""",
    "audit": _RESEARCH_PROMPT_BASE.replace(
        "before making changes.",
        "before evaluating code quality and security.",
    ),
    "plan": _RESEARCH_PROMPT_BASE.replace(
        "before making changes.",
        "before designing the implementation approach.",
    ),
}


def _extract_section(body: str, heading: str, level: int = 2) -> str:
    """Extract a markdown section's content by heading name.

    Returns the text between the heading and the next heading at the same
    or higher level. Returns empty string if section not found.
    """
    prefix = "#" * level
    # Match the heading line, then capture everything until the next
    # same-or-higher level heading or end of string.
    pattern = (
        rf"^{re.escape(prefix)}\s+{re.escape(heading)}\s*$"
        rf"(.*?)"
        rf"(?=^#{{1,{level}}}\s|\Z)"
    )
    m = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


class ContextBuilder:
    """Assembles rich agent instructions from vault + filesystem context.

    Sections are assembled in priority order with per-section char budgets.
    If total exceeds MAX_CHARS, lower-priority sections are dropped.

    Usage::

        builder = ContextBuilder(vault_path, workspace_root)
        instructions = builder.build(story_data, project_id)
    """

    def __init__(self, vault_path: Path, workspace_root: Path) -> None:
        self._vault_path = vault_path
        self._workspace_root = workspace_root
        self._vault = None  # lazy init

    @property
    def vault(self):
        """Lazy-load VaultEngine to avoid import-time cost."""
        if self._vault is None:
            from kronos_mcp.vault import VaultEngine
            self._vault = VaultEngine(str(self._vault_path))
        return self._vault

    def build(self, story_data: dict, project_id: str) -> str:
        """Build complete agent instructions. Returns markdown string.

        Args:
            story_data: Story dict from TaskEngine (title, description, etc.)
            project_id: Project FDO ID (e.g. "proj-mewtwo")
        """
        assignee = story_data.get("assignee", "code")

        # 1. Mandatory: story header
        header = self._resolve_story_header(story_data)

        # 2. Find related ADRs
        adrs = self._resolve_adrs(project_id)

        # 3. Decision boundaries from ADRs
        boundaries = self._resolve_decision_boundaries(adrs)

        # 4. Research prompt (tells agent about available tools)
        research = _RESEARCH_PROMPTS.get(assignee, _RESEARCH_PROMPT_BASE)

        # 5. ADR context (Decision section)
        adr_context = self._resolve_adr_context(adrs)

        # 6. Codebase orientation (meta.yaml)
        source_paths = self._collect_source_paths(project_id, adrs)
        orientation = self._resolve_orientation(source_paths)

        # 7. Source snippets
        snippets = self._resolve_source_snippets(source_paths)

        # Assemble with budget management
        sections = [
            ("header", header, 600),
            ("boundaries", boundaries, 800),
            ("research", research, 850),
            ("adr_context", adr_context, MAX_ADR_CHARS),
            ("orientation", orientation, MAX_ORIENTATION_CHARS),
            ("snippets", snippets, MAX_SOURCE_CHARS),
        ]

        return self._assemble(sections)

    # ── Resolvers ────────────────────────────────────────────────

    def _resolve_story_header(self, story_data: dict) -> str:
        """Format story metadata as the instruction header."""
        story_id = story_data.get("id", "unknown")
        title = story_data.get("title", "Untitled")
        description = story_data.get("description", "")
        ac = story_data.get("acceptance_criteria", [])

        parts = [f"# Agent Instructions\n\n## Story\n**{story_id}**: {title}"]

        if description:
            parts.append(f"\n{description}")

        if ac:
            parts.append("\n### Acceptance Criteria")
            for criterion in ac:
                parts.append(f"- {criterion}")

        return "\n".join(parts)

    def _resolve_adrs(self, project_id: str) -> list:
        """Find ADR FDOs related to the project."""
        try:
            project_fdo = self.vault.get(project_id)
            if project_fdo is None:
                return []

            adr_ids = [r for r in project_fdo.related if r.startswith("adr-")]
            adrs = []
            for adr_id in adr_ids:
                fdo = self.vault.get(adr_id)
                if fdo is not None:
                    adrs.append(fdo)
            return adrs
        except Exception:
            logger.warning("Failed to resolve ADRs for %s", project_id)
            return []

    def _resolve_decision_boundaries(self, adrs: list) -> str:
        """Extract Decision Boundaries sections from ADR FDOs."""
        if not adrs:
            return ""

        parts = ["## Decision Boundaries"]
        found = False

        for adr in adrs:
            section = _extract_section(adr.body, "Decision Boundaries")
            if section:
                if len(adrs) > 1:
                    parts.append(f"\n*From {adr.title}:*")
                parts.append(section)
                found = True

        return "\n".join(parts) if found else ""

    def _resolve_adr_context(self, adrs: list) -> str:
        """Extract Decision sections from ADR FDOs."""
        if not adrs:
            return ""

        parts = ["## Design Context"]

        for adr in adrs:
            decision = _extract_section(adr.body, "Decision")
            if decision:
                parts.append(f"\n### {adr.title}")
                parts.append(decision)

        return "\n".join(parts) if len(parts) > 1 else ""

    def _collect_source_paths(self, project_id: str, adrs: list) -> list[dict]:
        """Merge source_paths from project FDO + ADR FDOs, deduplicated."""
        seen: set[tuple[str, str]] = set()
        result: list[dict] = []

        # ADR paths first (more specific to the work)
        for adr in adrs:
            for sp in getattr(adr, "source_paths", []) or []:
                key = (sp.get("repo", ""), sp.get("path", ""))
                if key not in seen:
                    seen.add(key)
                    result.append(sp)

        # Then project paths
        try:
            project_fdo = self.vault.get(project_id)
            if project_fdo:
                for sp in getattr(project_fdo, "source_paths", []) or []:
                    key = (sp.get("repo", ""), sp.get("path", ""))
                    if key not in seen:
                        seen.add(key)
                        result.append(sp)
        except Exception:
            pass

        # Sort: modules first, then scripts, then others
        type_order = {"module": 0, "script": 1, "config": 2, "doc": 3, "experiment": 4, "data": 5}
        result.sort(key=lambda sp: type_order.get(sp.get("type", ""), 9))

        return result

    def _resolve_orientation(self, source_paths: list[dict]) -> str:
        """Read meta.yaml descriptions for directories referenced in source_paths."""
        if not source_paths:
            return ""

        seen_dirs: set[str] = set()
        entries: list[str] = []

        for sp in source_paths:
            repo = sp.get("repo", "")
            path = sp.get("path", "")
            if not repo or not path:
                continue

            # Get the directory of this source path
            dir_path = Path(path).parent.as_posix()
            dir_key = f"{repo}/{dir_path}"

            if dir_key in seen_dirs or dir_path == ".":
                continue
            seen_dirs.add(dir_key)

            # Read meta.yaml
            meta_path = self._workspace_root / repo / dir_path / "meta.yaml"
            try:
                if meta_path.exists():
                    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                    desc = meta.get("description", "")
                    if desc:
                        entries.append(f"**{repo}/{dir_path}/**: {desc}")
            except Exception:
                continue

        if not entries:
            return ""

        return "## Codebase Orientation\n\n" + "\n".join(entries)

    def _resolve_source_snippets(self, source_paths: list[dict]) -> str:
        """Read first N lines of key source files to show existing patterns."""
        if not source_paths:
            return ""

        # Filter to modules and scripts only
        candidates = [
            sp for sp in source_paths
            if sp.get("type", "") in ("module", "script")
        ][:MAX_SOURCE_FILES]

        if not candidates:
            return ""

        parts = ["## Key Source Files"]

        for sp in candidates:
            repo = sp.get("repo", "")
            path = sp.get("path", "")
            if not repo or not path:
                continue

            file_path = self._workspace_root / repo / path
            try:
                if not file_path.exists():
                    continue
                lines = file_path.read_text(encoding="utf-8").splitlines()
                snippet = "\n".join(lines[:SOURCE_SNIPPET_LINES])
                ext = Path(path).suffix.lstrip(".")
                parts.append(f"\n### {repo}/{path}\n```{ext}\n{snippet}\n```")
            except Exception:
                continue

        return "\n".join(parts) if len(parts) > 1 else ""

    # ── Assembly ─────────────────────────────────────────────────

    def _assemble(self, sections: list[tuple[str, str, int]]) -> str:
        """Assemble sections respecting budgets.

        sections: list of (name, content, max_chars)
        Sections are in priority order — lower priority dropped first if over budget.
        """
        result_parts: list[str] = []
        total = 0

        for _name, content, budget in sections:
            if not content:
                continue
            truncated = content[:budget]
            joiner_cost = 2 if result_parts else 0  # "\n\n" between sections
            if total + joiner_cost + len(truncated) > MAX_CHARS:
                # Try fitting with truncation
                remaining = MAX_CHARS - total - joiner_cost
                if remaining > 100:  # only include if meaningful
                    result_parts.append(content[:remaining])
                break
            result_parts.append(truncated)
            total += joiner_cost + len(truncated)

        return "\n\n".join(result_parts)
