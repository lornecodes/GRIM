"""Tests for the ContextBuilder — rich agent instruction assembly.

Uses temp vaults with real FDO files and temp workspaces with source files.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.daemon.context import (
    ContextBuilder,
    MAX_CHARS,
    MAX_SOURCE_FILES,
    SOURCE_SNIPPET_LINES,
    _extract_section,
)


# ── Helpers ──────────────────────────────────────────────────────


def _make_fdo_file(vault_path: Path, domain: str, fdo_id: str, fm: dict, body: str) -> None:
    """Write an FDO file to the temp vault."""
    domain_dir = vault_path / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    (domain_dir / f"{fdo_id}.md").write_text(
        f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8"
    )


def _make_project(vault_path: Path, proj_id: str, related: list[str] = None,
                  source_paths: list[dict] = None) -> None:
    """Create a minimal project FDO."""
    fm = {
        "id": proj_id,
        "title": f"Project {proj_id}",
        "domain": "projects",
        "created": "2026-03-06",
        "updated": "2026-03-06",
        "status": "developing",
        "confidence": 0.7,
        "related": related or [],
        "source_repos": ["GRIM"],
        "tags": ["epic"],
        "source_paths": source_paths or [],
    }
    body = f"# {proj_id}\n\n## Summary\nTest project."
    _make_fdo_file(vault_path, "projects", proj_id, fm, body)


def _make_adr(vault_path: Path, adr_id: str, body: str,
              source_paths: list[dict] = None, related: list[str] = None) -> None:
    """Create an ADR FDO."""
    fm = {
        "id": adr_id,
        "title": f"ADR: {adr_id}",
        "domain": "decisions",
        "created": "2026-03-06",
        "updated": "2026-03-06",
        "status": "stable",
        "confidence": 0.9,
        "related": related or [],
        "source_repos": ["GRIM"],
        "tags": ["adr", "decision"],
        "source_paths": source_paths or [],
    }
    _make_fdo_file(vault_path, "decisions", adr_id, fm, body)


def _make_source_file(workspace: Path, repo: str, path: str, content: str) -> None:
    """Create a source file in the workspace."""
    full_path = workspace / repo / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")


def _make_meta_yaml(workspace: Path, repo: str, dir_path: str, description: str) -> None:
    """Create a meta.yaml file."""
    meta_path = workspace / repo / dir_path / "meta.yaml"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(yaml.dump({"description": description}), encoding="utf-8")


def _story_data(**overrides) -> dict:
    """Build a story data dict with sensible defaults."""
    data = {
        "id": "story-test-001",
        "title": "Implement feature X",
        "description": "Build the X feature with tests.",
        "acceptance_criteria": ["Tests pass", "Docs updated"],
        "assignee": "code",
        "priority": "high",
        "tags": ["feature"],
    }
    data.update(overrides)
    return data


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path) -> Path:
    """Temp workspace root."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def vault(workspace) -> Path:
    """Temp vault inside workspace."""
    v = workspace / "kronos-vault"
    v.mkdir()
    return v


@pytest.fixture
def builder(vault, workspace) -> ContextBuilder:
    """ContextBuilder with temp vault."""
    return ContextBuilder(vault, workspace)


# ── _extract_section tests ───────────────────────────────────────


class TestExtractSection:
    """Test markdown section extraction helper."""

    def test_extracts_h2(self):
        body = "## Context\nSome context.\n\n## Decision\nWe decided X.\n\n## Details\nMore."
        assert _extract_section(body, "Decision") == "We decided X."

    def test_extracts_h3(self):
        body = "### Foo\nfoo content\n\n### Bar\nbar content"
        assert _extract_section(body, "Foo", level=3) == "foo content"

    def test_missing_section(self):
        body = "## Context\nSome text."
        assert _extract_section(body, "NonExistent") == ""

    def test_last_section(self):
        body = "## Context\nSome context.\n\n## Decision\nFinal section."
        assert _extract_section(body, "Decision") == "Final section."

    def test_multiline_content(self):
        body = "## Decision\nLine 1\nLine 2\nLine 3\n\n## Next\nother"
        result = _extract_section(body, "Decision")
        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result

    def test_decision_boundaries_with_subsections(self):
        body = (
            "## Decision Boundaries\n\n"
            "### Agent Handles Autonomously\n"
            "- Implementation details\n"
            "- Test structure\n\n"
            "### Escalate to Human\n"
            "- Scope changes\n"
            "- Architecture choices\n\n"
            "## Acceptance Criteria\n"
            "- [ ] Done"
        )
        result = _extract_section(body, "Decision Boundaries")
        assert "Agent Handles Autonomously" in result
        assert "Escalate to Human" in result
        assert "Implementation details" in result
        assert "Acceptance Criteria" not in result

    def test_empty_body(self):
        assert _extract_section("", "Decision") == ""

    def test_heading_with_extra_spaces(self):
        body = "##  Decision  \nContent here.\n\n## Next\nother"
        # \s+ in regex tolerates extra spaces around heading text
        assert _extract_section(body, "Decision") == "Content here."


# ── Story header tests ───────────────────────────────────────────


class TestStoryHeader:

    def test_full_header(self, builder):
        result = builder._resolve_story_header(_story_data())
        assert "story-test-001" in result
        assert "Implement feature X" in result
        assert "Build the X feature" in result
        assert "Tests pass" in result
        assert "Docs updated" in result

    def test_missing_description(self, builder):
        result = builder._resolve_story_header(_story_data(description=""))
        assert "story-test-001" in result
        assert "Implement feature X" in result

    def test_no_acceptance_criteria(self, builder):
        result = builder._resolve_story_header(_story_data(acceptance_criteria=[]))
        assert "Acceptance Criteria" not in result

    def test_missing_title(self, builder):
        result = builder._resolve_story_header({"id": "s1"})
        assert "Untitled" in result

    def test_empty_story(self, builder):
        result = builder._resolve_story_header({})
        assert "Agent Instructions" in result


# ── ADR discovery tests ──────────────────────────────────────────


class TestADRDiscovery:

    def test_finds_related_adrs(self, vault, builder):
        _make_adr(vault, "adr-test-one", "# ADR\n\n## Decision\nWe do X.")
        _make_project(vault, "proj-test", related=["adr-test-one"])

        adrs = builder._resolve_adrs("proj-test")
        assert len(adrs) == 1
        assert adrs[0].id == "adr-test-one"

    def test_no_related_adrs(self, vault, builder):
        _make_project(vault, "proj-test", related=["some-other-fdo"])
        assert builder._resolve_adrs("proj-test") == []

    def test_missing_project(self, builder):
        assert builder._resolve_adrs("proj-nonexistent") == []

    def test_multiple_adrs(self, vault, builder):
        _make_adr(vault, "adr-one", "# ADR One\n\n## Decision\nX.")
        _make_adr(vault, "adr-two", "# ADR Two\n\n## Decision\nY.")
        _make_project(vault, "proj-test", related=["adr-one", "adr-two", "other-fdo"])

        adrs = builder._resolve_adrs("proj-test")
        assert len(adrs) == 2

    def test_missing_adr_skipped(self, vault, builder):
        _make_project(vault, "proj-test", related=["adr-ghost"])
        adrs = builder._resolve_adrs("proj-test")
        assert len(adrs) == 0


# ── Decision boundaries tests ────────────────────────────────────


class TestDecisionBoundaries:

    def test_extracts_boundaries(self, vault, builder):
        body = (
            "# ADR\n\n## Decision\nWe decided.\n\n"
            "## Decision Boundaries\n\n"
            "### Agent Handles Autonomously\n"
            "- File organization\n\n"
            "### Escalate to Human\n"
            "- Scope changes\n\n"
            "## Acceptance Criteria\n- Done"
        )
        _make_adr(vault, "adr-test", body)
        _make_project(vault, "proj-test", related=["adr-test"])

        adrs = builder._resolve_adrs("proj-test")
        result = builder._resolve_decision_boundaries(adrs)
        assert "File organization" in result
        assert "Scope changes" in result
        assert "Acceptance Criteria" not in result

    def test_no_boundaries_section(self, vault, builder):
        _make_adr(vault, "adr-test", "# ADR\n\n## Decision\nJust a decision.")
        _make_project(vault, "proj-test", related=["adr-test"])

        adrs = builder._resolve_adrs("proj-test")
        result = builder._resolve_decision_boundaries(adrs)
        assert result == ""

    def test_no_adrs(self, builder):
        assert builder._resolve_decision_boundaries([]) == ""


# ── ADR context tests ────────────────────────────────────────────


class TestADRContext:

    def test_extracts_decision(self, vault, builder):
        body = "# ADR\n\n## Context\nBackground.\n\n## Decision\nWe chose approach A because reasons."
        _make_adr(vault, "adr-test", body)
        _make_project(vault, "proj-test", related=["adr-test"])

        adrs = builder._resolve_adrs("proj-test")
        result = builder._resolve_adr_context(adrs)
        assert "Design Context" in result
        assert "approach A" in result

    def test_no_decision_section(self, vault, builder):
        body = "# ADR\n\n## Context\nJust context."
        _make_adr(vault, "adr-test", body)
        _make_project(vault, "proj-test", related=["adr-test"])

        adrs = builder._resolve_adrs("proj-test")
        result = builder._resolve_adr_context(adrs)
        assert result == ""

    def test_no_adrs(self, builder):
        assert builder._resolve_adr_context([]) == ""


# ── Source path collection tests ─────────────────────────────────


class TestSourcePathCollection:

    def test_merges_adr_and_project(self, vault, builder):
        adr_paths = [{"repo": "GRIM", "path": "core/a.py", "type": "module"}]
        proj_paths = [{"repo": "GRIM", "path": "core/b.py", "type": "module"}]
        _make_adr(vault, "adr-test", "# ADR", source_paths=adr_paths)
        _make_project(vault, "proj-test", related=["adr-test"], source_paths=proj_paths)

        adrs = builder._resolve_adrs("proj-test")
        result = builder._collect_source_paths("proj-test", adrs)
        paths = [(sp["repo"], sp["path"]) for sp in result]
        assert ("GRIM", "core/a.py") in paths
        assert ("GRIM", "core/b.py") in paths

    def test_deduplicates(self, vault, builder):
        same_path = [{"repo": "GRIM", "path": "core/x.py", "type": "module"}]
        _make_adr(vault, "adr-test", "# ADR", source_paths=same_path)
        _make_project(vault, "proj-test", related=["adr-test"], source_paths=same_path)

        adrs = builder._resolve_adrs("proj-test")
        result = builder._collect_source_paths("proj-test", adrs)
        assert len(result) == 1

    def test_sorts_by_type(self, vault, builder):
        paths = [
            {"repo": "GRIM", "path": "doc.md", "type": "doc"},
            {"repo": "GRIM", "path": "main.py", "type": "module"},
            {"repo": "GRIM", "path": "run.sh", "type": "script"},
        ]
        _make_project(vault, "proj-test", source_paths=paths)

        result = builder._collect_source_paths("proj-test", [])
        types = [sp["type"] for sp in result]
        assert types == ["module", "script", "doc"]


# ── Orientation tests ────────────────────────────────────────────


class TestOrientation:

    def test_reads_meta_yaml(self, workspace, builder):
        _make_meta_yaml(workspace, "GRIM", "core/daemon", "Management daemon orchestration loop")
        source_paths = [{"repo": "GRIM", "path": "core/daemon/engine.py", "type": "module"}]
        result = builder._resolve_orientation(source_paths)
        assert "Management daemon" in result
        assert "core/daemon/" in result

    def test_deduplicates_directories(self, workspace, builder):
        _make_meta_yaml(workspace, "GRIM", "core/pool", "Execution pool")
        source_paths = [
            {"repo": "GRIM", "path": "core/pool/queue.py", "type": "module"},
            {"repo": "GRIM", "path": "core/pool/slot.py", "type": "module"},
        ]
        result = builder._resolve_orientation(source_paths)
        # Should only appear once despite two files in same dir
        assert result.count("core/pool/") == 1

    def test_missing_meta_yaml(self, workspace, builder):
        source_paths = [{"repo": "GRIM", "path": "nonexistent/dir/file.py", "type": "module"}]
        result = builder._resolve_orientation(source_paths)
        assert result == ""

    def test_empty_source_paths(self, builder):
        assert builder._resolve_orientation([]) == ""

    def test_meta_yaml_without_description(self, workspace, builder):
        meta_path = workspace / "GRIM" / "core" / "meta.yaml"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text("files:\n  - app.py\n", encoding="utf-8")
        source_paths = [{"repo": "GRIM", "path": "core/app.py", "type": "module"}]
        result = builder._resolve_orientation(source_paths)
        assert result == ""


# ── Source snippet tests ─────────────────────────────────────────


class TestSourceSnippets:

    def test_reads_file_snippet(self, workspace, builder):
        content = "# Module docstring\nimport os\n\ndef main():\n    pass\n"
        _make_source_file(workspace, "GRIM", "core/daemon/engine.py", content)
        source_paths = [{"repo": "GRIM", "path": "core/daemon/engine.py", "type": "module"}]

        result = builder._resolve_source_snippets(source_paths)
        assert "import os" in result
        assert "def main" in result
        assert "GRIM/core/daemon/engine.py" in result

    def test_caps_at_max_lines(self, workspace, builder):
        lines = [f"line {i}" for i in range(100)]
        _make_source_file(workspace, "GRIM", "big.py", "\n".join(lines))
        source_paths = [{"repo": "GRIM", "path": "big.py", "type": "module"}]

        result = builder._resolve_source_snippets(source_paths)
        assert "line 0" in result
        assert f"line {SOURCE_SNIPPET_LINES - 1}" in result
        assert f"line {SOURCE_SNIPPET_LINES + 5}" not in result

    def test_caps_at_max_files(self, workspace, builder):
        source_paths = []
        for i in range(MAX_SOURCE_FILES + 3):
            _make_source_file(workspace, "GRIM", f"mod{i}.py", f"# module {i}")
            source_paths.append({"repo": "GRIM", "path": f"mod{i}.py", "type": "module"})

        result = builder._resolve_source_snippets(source_paths)
        # Should have at most MAX_SOURCE_FILES file sections
        assert result.count("### GRIM/") <= MAX_SOURCE_FILES

    def test_skips_missing_files(self, workspace, builder):
        source_paths = [{"repo": "GRIM", "path": "nonexistent.py", "type": "module"}]
        result = builder._resolve_source_snippets(source_paths)
        assert result == ""

    def test_skips_non_module_types(self, builder):
        source_paths = [
            {"repo": "GRIM", "path": "readme.md", "type": "doc"},
            {"repo": "GRIM", "path": "data.csv", "type": "data"},
        ]
        result = builder._resolve_source_snippets(source_paths)
        assert result == ""

    def test_empty_source_paths(self, builder):
        assert builder._resolve_source_snippets([]) == ""


# ── Budget assembly tests ────────────────────────────────────────


class TestAssembly:

    def test_includes_all_when_under_budget(self, builder):
        sections = [
            ("a", "Section A content", 500),
            ("b", "Section B content", 500),
        ]
        result = builder._assemble(sections)
        assert "Section A" in result
        assert "Section B" in result

    def test_drops_empty_sections(self, builder):
        sections = [
            ("a", "Content A", 500),
            ("b", "", 500),
            ("c", "Content C", 500),
        ]
        result = builder._assemble(sections)
        assert "Content A" in result
        assert "Content C" in result

    def test_truncates_at_budget(self, builder):
        sections = [
            ("a", "A" * 100, 50),
        ]
        result = builder._assemble(sections)
        assert len(result) == 50

    def test_drops_when_over_max(self, builder):
        sections = [
            ("a", "A" * (MAX_CHARS - 200), MAX_CHARS),
            ("b", "B" * 500, 500),
        ]
        result = builder._assemble(sections)
        assert len(result) <= MAX_CHARS
        # First section should be mostly present
        assert "A" * 100 in result

    def test_skips_tiny_remaining(self, builder):
        sections = [
            ("a", "A" * (MAX_CHARS - 50), MAX_CHARS),
            ("b", "B" * 200, 200),
        ]
        result = builder._assemble(sections)
        # Remaining 50 chars is < 100 threshold, so "b" dropped
        assert "B" not in result


# ── Full build tests ─────────────────────────────────────────────


class TestFullBuild:

    def test_story_only(self, vault, builder):
        """Build with no project FDO — just story header + research prompt."""
        result = builder.build(_story_data(), "proj-nonexistent")
        assert "Agent Instructions" in result
        assert "story-test-001" in result
        assert "Implement feature X" in result
        assert "Research Tools" in result

    def test_with_adr(self, vault, builder):
        """Build with project + ADR — includes decision boundaries."""
        adr_body = (
            "# ADR\n\n"
            "## Decision\nWe chose Python orchestration loop.\n\n"
            "## Decision Boundaries\n\n"
            "### Agent Handles Autonomously\n"
            "- Implementation details within scope\n\n"
            "### Escalate to Human\n"
            "- Architecture changes\n\n"
            "## Acceptance Criteria\n- [ ] Tests pass"
        )
        _make_adr(vault, "adr-test", adr_body)
        _make_project(vault, "proj-test", related=["adr-test"])

        result = builder.build(_story_data(), "proj-test")
        assert "Decision Boundaries" in result
        assert "Implementation details" in result
        assert "Architecture changes" in result
        assert "Python orchestration loop" in result
        assert "Research Tools" in result

    def test_with_source_files(self, vault, workspace, builder):
        """Build with source files — includes snippets and orientation."""
        _make_source_file(workspace, "GRIM", "core/foo.py",
                          "\"\"\"Foo module.\"\"\"\nimport os\n\ndef foo():\n    pass\n")
        _make_meta_yaml(workspace, "GRIM", "core", "Core application logic")

        source_paths = [{"repo": "GRIM", "path": "core/foo.py", "type": "module"}]
        _make_project(vault, "proj-test", source_paths=source_paths)

        result = builder.build(_story_data(), "proj-test")
        assert "Core application logic" in result
        assert "import os" in result

    def test_under_max_chars(self, vault, builder):
        """Full build should never exceed MAX_CHARS."""
        # Create a large ADR
        adr_body = "# ADR\n\n## Decision\n" + "X " * 5000
        _make_adr(vault, "adr-big", adr_body)
        _make_project(vault, "proj-test", related=["adr-big"])

        result = builder.build(_story_data(), "proj-test")
        assert len(result) <= MAX_CHARS

    def test_research_prompt_varies_by_assignee(self, vault, builder):
        """Research prompt is customized per job type."""
        _make_project(vault, "proj-test")

        code_result = builder.build(_story_data(assignee="code"), "proj-test")
        research_result = builder.build(_story_data(assignee="research"), "proj-test")

        # Research agent gets additional tools
        assert "kronos_deep_dive" in research_result
        assert "kronos_deep_dive" not in code_result

    def test_audit_prompt(self, vault, builder):
        _make_project(vault, "proj-test")
        result = builder.build(_story_data(assignee="audit"), "proj-test")
        assert "evaluating code quality" in result

    def test_plan_prompt(self, vault, builder):
        _make_project(vault, "proj-test")
        result = builder.build(_story_data(assignee="plan"), "proj-test")
        assert "designing the implementation" in result
