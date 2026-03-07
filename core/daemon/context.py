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

MAX_CHARS = 12000
MAX_ADR_CHARS = 2000
MAX_SOURCE_CHARS = 2000
MAX_ORIENTATION_CHARS = 1000
MAX_SKILL_CHARS = 2500
MAX_RESEARCH_CONTEXT_CHARS = 2000
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


# ── Execution detection ──────────────────────────────────────────

_EXECUTION_TAGS = frozenset({"experiment", "run", "execute", "benchmark", "validate"})

_EXECUTION_KEYWORDS = re.compile(
    r"\b(run\s+(the\s+)?(\w+\s+)?(script|experiment|benchmark|simulation|test suite|pipeline))"
    r"|\b(execute\s+(the\s+)?(\w+\s+)?(script|experiment|benchmark|simulation|pipeline))"
    r"|\b(capture\s+(the\s+)?(output|results|metrics))"
    r"|\b(report\s+(the\s+)?(results|output|metrics))",
    re.IGNORECASE,
)


def _is_execution_story(story_data: dict) -> bool:
    """Detect whether a story requires script/experiment execution.

    Checks story tags (exact match against known execution tags) and
    title + description (keyword pattern match).
    """
    tags = set(story_data.get("tags") or [])
    if tags & _EXECUTION_TAGS:
        return True
    text = f"{story_data.get('title', '')} {story_data.get('description', '')}"
    return bool(_EXECUTION_KEYWORDS.search(text))


# ── Domain skill cards ───────────────────────────────────────────
# Compressed distillations of .claude/instructions/ files.
# Keyed by tag name — multiple tags can match, cards concatenated.
# See _SKILL_CARD_SOURCES for which instruction file each card derives from.
# When updating instruction files, check the corresponding card here.

_SKILL_CARD_SOURCES: dict[str, str | None] = {
    "experiment": ".claude/instructions/experiment-schema.instructions.md",
    "dft": ".claude/instructions/dawn-field-theory.instructions.md",
    "physics": None,  # alias for dft
    "spec": ".claude/instructions/spec-driven-development.instructions.md",
    "changelog": ".claude/instructions/changelog.instructions.md",
    "vault-sync": ".claude/instructions/main.instructions.md",
    "vault": None,  # alias for vault-sync
    "library": ".claude/instructions/library.instructions.md",
    "module": None,  # alias for library
}

_SKILL_CARDS: dict[str, str] = {
    "experiment": (
        "### Experiment Schema\n"
        "Folder: `meta.yaml` + `README.md` + `scripts/` + `results/` + `journals/`\n"
        "Scripts: `exp_NN_name.py` naming. Results: `exp_NN_name_YYYYMMDD_HHMMSS.json`\n"
        "Every directory needs `meta.yaml` (schema v2.0, description, semantic_scope, files).\n"
        "Journals: `YYYY-MM-DD_slug.md` with Summary, Timeline, Key Findings, Next Steps.\n"
        "Include falsification tests — they're often more valuable than successes.\n"
        "POCs: hypothesis in README, success criteria quantified, status tracked in POC_REGISTRY.md."
    ),
    "dft": (
        "### Dawn Field Theory Context\n"
        "Exploratory physics: information gradients as generative foundations of reality.\n"
        "Four pillars: PAC (f(Parent)=Sum f(Children)), SEC (dS/dt=a*grad(I)-b*grad(H)), "
        "RBF (self-regulation), MED (depth<=2, nodes<=3).\n"
        "Key constants: phi (1.618), Xi (~1.057), 1/phi (0.618) — should emerge independently.\n"
        "Epistemic stance: use 'suggests', 'might', 'appears to' — not certainty.\n"
        "Navigation: Read meta.yaml first, then SYNTHESIS.md, then exp_*falsification*.py."
    ),
    "physics": None,  # alias for dft, resolved below
    "spec": (
        "### Spec-Driven Development\n"
        "Before implementation: read `.spec/*.spec.md`, understand constraints.\n"
        "If spec missing for major feature: propose spec FIRST and wait for approval.\n"
        "If changes deviate from spec: propose spec update before implementing.\n"
        "Check `challenges.md` for open questions. Failed POCs are valuable documentation."
    ),
    "changelog": (
        "### Changelog Convention\n"
        "Folder: `.changelog/YYYYMMDD_HHMMSS_brief_slug.md` — one file per session.\n"
        "NEVER create summary .md files at repo roots.\n"
        "Type tags: engineering | research | documentation | refactor | bugfix | release.\n"
        "Sections: Summary, Changes (Added/Changed/Fixed/Removed), Details, Related.\n"
        "Include commit hash, reasoning for decisions, links to artifacts."
    ),
    "vault-sync": (
        "### Vault Sync (Mandatory)\n"
        "After ANY task that modifies code, experiments, or project structure:\n"
        "1. Identify affected FDOs (`kronos_search` by tags, source_paths, project)\n"
        "2. Update: source_paths when files move, counts/status when things change\n"
        "3. Add log entries to project FDOs\n"
        "Vault drift is the #1 documentation failure mode. This is NOT optional."
    ),
    "vault": None,  # alias for vault-sync, resolved below
    "library": (
        "### Library Conventions\n"
        "Module structure: public API in __init__.py, sub-packages for domains.\n"
        "Tests: mirror source structure in tests/ folder, pytest with descriptive names.\n"
        "Type hints on all public functions. Docstrings on public API (Google style).\n"
        "Keep diffs small and focused. Preserve existing interfaces.\n"
        "Protected files: .env*, *.key, CI/CD configs, lock files."
    ),
    "module": None,  # alias for library, resolved below
}

# Resolve aliases
_SKILL_CARDS["physics"] = _SKILL_CARDS["dft"]
_SKILL_CARDS["vault"] = _SKILL_CARDS["vault-sync"]
_SKILL_CARDS["module"] = _SKILL_CARDS["library"]

# Tags that trigger skill card injection — exported for task creation.
KNOWN_SKILL_TAGS: frozenset[str] = frozenset(
    name for name, card in _SKILL_CARDS.items() if card is not None
)

# Keyword patterns for tag suggestion (tag → trigger words).
_TAG_KEYWORDS: dict[str, set[str]] = {
    "experiment": {"experiment", "exp_", "hypothesis", "falsification", "milestone"},
    "dft": {"dawn field", "pac", "sec ", "rbf", "med ", "entropy", "information gradient"},
    "physics": {"physics", "quantum", "field theory", "pillar"},
    "spec": {"spec", "specification", "design doc"},
    "changelog": {"changelog", "release note"},
    "vault-sync": {"vault sync", "update fdo", "source_paths"},
    "vault": {"vault", "fdo", "knowledge graph"},
    "library": {"library", "module", "package", "public api", "fracton"},
    "run": {"run script", "execute script", "benchmark", "pipeline"},
    "validate": {"validate", "verification", "check results"},
}


def suggest_tags(title: str, description: str = "") -> list[str]:
    """Suggest skill-relevant tags based on title/description keywords.

    Simple case-insensitive substring matching. Returns sorted unique
    suggestions. Intended as advisory, not enforcement.
    """
    text = f"{title} {description}".lower()
    suggestions: list[str] = []

    for tag, keywords in _TAG_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                suggestions.append(tag)
                break

    return sorted(set(suggestions))


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

    def __init__(self, vault_path: Path, workspace_root: Path, pipeline_store=None) -> None:
        self._vault_path = vault_path
        self._workspace_root = workspace_root
        self._vault = None  # lazy init
        self._pipeline_store = pipeline_store  # PipelineStore for research context

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

        # 2. Prior research context (from completed research dependencies)
        prior_research = self._resolve_research_context(story_data)

        # 3. Find related ADRs
        adrs = self._resolve_adrs(project_id)

        # 4. Decision boundaries from ADRs
        boundaries = self._resolve_decision_boundaries(adrs)

        # 5. Research prompt (tells agent about available tools)
        research = _RESEARCH_PROMPTS.get(assignee, _RESEARCH_PROMPT_BASE)

        # 6. Execution protocol (only for run/experiment stories)
        execution = self._resolve_execution_instructions(story_data)

        # 7. Domain skill cards (based on story tags)
        skills = self._resolve_skill_cards(story_data)

        # 8. ADR context (Decision section)
        adr_context = self._resolve_adr_context(adrs)

        # 9. Codebase orientation (meta.yaml)
        source_paths = self._collect_source_paths(project_id, adrs)
        orientation = self._resolve_orientation(source_paths)

        # 10. Source snippets
        snippets = self._resolve_source_snippets(source_paths)

        # Assemble with budget management
        sections = [
            ("header", header, 600),
            ("prior_research", prior_research, MAX_RESEARCH_CONTEXT_CHARS),
            ("boundaries", boundaries, 800),
            ("research", research, 850),
            ("execution", execution, 600),
            ("skills", skills, MAX_SKILL_CHARS),
            ("adr_context", adr_context, MAX_ADR_CHARS),
            ("orientation", orientation, MAX_ORIENTATION_CHARS),
            ("snippets", snippets, MAX_SOURCE_CHARS),
        ]

        return self._assemble(sections)

    # ── Resolvers ────────────────────────────────────────────────

    def _resolve_execution_instructions(self, story_data: dict) -> str:
        """Build execution protocol when the story requires running scripts.

        Returns empty string for pure code-writing stories.
        """
        if not _is_execution_story(story_data):
            return ""

        ac = story_data.get("acceptance_criteria") or []
        ac_block = ""
        if ac:
            ac_lines = "\n".join(f"  - {c}" for c in ac)
            ac_block = f"\n- Validate results against acceptance criteria:\n{ac_lines}"

        return (
            "## Execution Protocol\n\n"
            "This story requires running code, not just writing it.\n\n"
            "1. **Run** the target script/experiment with `python <script>.py`\n"
            "2. **Capture** the full stdout/stderr output\n"
            "3. **Report** key metrics, return codes, and pass/fail status\n"
            "4. **Diagnose** failures — fix the issue and re-run, don't just report errors"
            f"{ac_block}\n\n"
            "Do NOT just write the code and stop. The deliverable is working, "
            "validated results."
        )

    def _resolve_research_context(self, story_data: dict) -> str:
        """Inject prior research results when story depends on completed research.

        Queries the pipeline store for completed research stories that this
        story depends on, and includes their result_summary as context.
        Returns empty string if no pipeline store, no dependencies, or no
        research results found.
        """
        if self._pipeline_store is None:
            return ""

        depends_on_raw = story_data.get("depends_on") or ""
        if not depends_on_raw:
            return ""

        # Parse dependency IDs (stored as JSON array string or plain list)
        import json as _json
        if isinstance(depends_on_raw, str):
            try:
                dep_ids = _json.loads(depends_on_raw)
            except (ValueError, _json.JSONDecodeError):
                dep_ids = [d.strip() for d in depends_on_raw.split(",") if d.strip()]
        elif isinstance(depends_on_raw, list):
            dep_ids = depends_on_raw
        else:
            return ""

        if not dep_ids:
            return ""

        # Look up each dependency — try pipeline DB first, then vault notes
        parts: list[str] = []
        total = 0

        for dep_id in dep_ids:
            summary = ""

            # Try pipeline DB first (fast, has result_summary)
            try:
                item = self._get_pipeline_item_sync(dep_id)
                if item is not None:
                    summary = getattr(item, "result_summary", "")
            except Exception:
                logger.debug("Could not fetch pipeline item for dep %s", dep_id)

            # Fallback: search vault notes for persisted job results
            if not summary:
                summary = self._get_vault_note_result(dep_id)

            if not summary:
                continue

            entry = f"### Prior: {dep_id}\n{summary}"
            if total + len(entry) > MAX_RESEARCH_CONTEXT_CHARS - 50:
                break
            parts.append(entry)
            total += len(entry)

        if not parts:
            return ""

        return "## Prior Research\n\n" + "\n\n".join(parts)

    def _get_vault_note_result(self, story_id: str) -> str:
        """Search vault notes for a persisted job result by story ID tag.

        Returns the note body or empty string if not found. This is the
        fallback when the pipeline DB doesn't have the result (e.g., after
        a DB wipe or restart).
        """
        try:
            from kronos_mcp.server import handle_notes_recent

            result = handle_notes_recent({
                "tags": [story_id],
                "days": 90,
                "max_entries": 1,
            })

            import json as _json
            data = _json.loads(result) if isinstance(result, str) else result
            entries = data.get("entries", [])
            if entries:
                return entries[0].get("body", "")
        except Exception:
            logger.debug("Could not fetch vault note for %s", story_id)
        return ""

    def _get_pipeline_item_sync(self, story_id: str):
        """Synchronously fetch a pipeline item by story ID.

        Runs the async get_by_story() in a new event loop if needed.
        Returns PipelineItem or None.
        """
        import asyncio

        coro = self._pipeline_store.get_by_story(story_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context — can't nest event loops.
            # Use a thread to run the coroutine.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=5)
        else:
            return asyncio.run(coro)

    def _resolve_skill_cards(self, story_data: dict) -> str:
        """Inject domain knowledge based on story tags.

        Looks up compressed skill cards by tag and concatenates them,
        respecting the MAX_SKILL_CHARS budget.
        """
        tags = story_data.get("tags") or []
        seen_cards: set[int] = set()  # track by id() to skip aliases
        parts: list[str] = []
        total = 0

        for tag in tags:
            card = _SKILL_CARDS.get(tag)
            if card is None or id(card) in seen_cards:
                continue
            seen_cards.add(id(card))
            if total + len(card) > MAX_SKILL_CHARS:
                logger.debug("Skill card budget exceeded at tag '%s'", tag)
                break
            logger.debug("Injecting skill card for tag '%s'", tag)
            parts.append(card)
            total += len(card)

        if not parts:
            return ""

        return "## Domain Knowledge\n\n" + "\n\n".join(parts)

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
