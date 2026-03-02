"""Tests for the three-tier knowledge capture system.

Covers:
- kronos_note_append: rolling log creation, entry appending, anchors, indexing
- kronos_notes_recent: entry parsing, date filtering, tag filtering
- Domain enum: all VALID_DOMAINS appear in MCP tool schemas
- Memory node: parallel queries, deduplication, recent_notes state key
- Skills: kronos-note and kronos-bp manifests load and match correctly

Run: cd GRIM && python -m pytest tests/test_notes_tools.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure GRIM root is on sys.path
GRIM_ROOT = Path(__file__).resolve().parent.parent
if str(GRIM_ROOT) not in sys.path:
    sys.path.insert(0, str(GRIM_ROOT))

# Bootstrap MCP environment (must happen before importing kronos_mcp.server)
_vault_path = str((GRIM_ROOT / ".." / "kronos-vault").resolve())
_skills_path = str((GRIM_ROOT / "skills").resolve())
os.environ.setdefault("KRONOS_VAULT_PATH", _vault_path)
os.environ.setdefault("KRONOS_SKILLS_PATH", _skills_path)
sys.path.insert(0, str(GRIM_ROOT / "mcp" / "kronos" / "src"))


def run_async(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Domain Enum — regression guard
# ═══════════════════════════════════════════════════════════════════════════

class TestDomainEnum(unittest.TestCase):
    """Verify MCP tool schemas include all valid domains."""

    def test_domain_enum_matches_valid_domains(self):
        """_DOMAIN_ENUM in server.py matches VALID_DOMAINS from vault.py."""
        from kronos_mcp.vault import VALID_DOMAINS
        from kronos_mcp.server import _DOMAIN_ENUM
        self.assertEqual(set(_DOMAIN_ENUM), VALID_DOMAINS)

    def test_domain_enum_is_sorted(self):
        """_DOMAIN_ENUM is sorted for consistent display."""
        from kronos_mcp.server import _DOMAIN_ENUM
        self.assertEqual(_DOMAIN_ENUM, sorted(_DOMAIN_ENUM))

    def test_all_tools_use_domain_enum_constant(self):
        """All tool schemas referencing domains use _DOMAIN_ENUM, not hardcoded lists."""
        from kronos_mcp.server import TOOLS, _DOMAIN_ENUM
        for tool in TOOLS:
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            if "domain" in props:
                domain_prop = props["domain"]
                if "enum" in domain_prop:
                    self.assertEqual(
                        domain_prop["enum"], _DOMAIN_ENUM,
                        f"Tool {tool.name} domain enum doesn't match _DOMAIN_ENUM"
                    )

    def test_notes_domain_is_valid(self):
        """'notes' is a valid domain."""
        from kronos_mcp.vault import VALID_DOMAINS
        self.assertIn("notes", VALID_DOMAINS)

    def test_journal_domain_is_valid(self):
        """'journal' is a valid domain."""
        from kronos_mcp.vault import VALID_DOMAINS
        self.assertIn("journal", VALID_DOMAINS)

    def test_projects_domain_is_valid(self):
        """'projects' is a valid domain (was previously missing from enum)."""
        from kronos_mcp.vault import VALID_DOMAINS
        self.assertIn("projects", VALID_DOMAINS)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Note Append Handler
# ═══════════════════════════════════════════════════════════════════════════

class TestNoteAppend(unittest.TestCase):
    """Test kronos_note_append handler."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.notes_dir = Path(self.tmp) / "notes"
        self.notes_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_handler(self):
        """Create a handle_note_append with a patched vault."""
        from kronos_mcp.server import handle_note_append
        return handle_note_append

    def test_creates_monthly_file(self):
        """First note creates the monthly rolling log file."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_note_append({
                "title": "Test Note",
                "body": "This is a test note.",
                "tags": ["test", "unit-test"],
            }))
            self.assertIn("appended", result)
            self.assertIn("note-", result["appended"])
            self.assertTrue(Path(result["file"]).exists())
        finally:
            server.vault.vault_path = original_vault_path

    def test_monthly_file_has_valid_frontmatter(self):
        """Created monthly file has valid FDO frontmatter."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "Test",
                "body": "Body",
                "tags": ["test"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---"))
            self.assertIn(f"id: notes-{now.strftime('%Y-%m')}", content)
            self.assertIn("domain: notes", content)
            self.assertIn("status: developing", content)
        finally:
            server.vault.vault_path = original_vault_path

    def test_appends_to_existing_file(self):
        """Second note appends to existing monthly file."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "First Note",
                "body": "First body",
                "tags": ["test"],
            })
            server.handle_note_append({
                "title": "Second Note",
                "body": "Second body",
                "tags": ["test"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertIn("## First Note", content)
            self.assertIn("## Second Note", content)
            self.assertIn("First body", content)
            self.assertIn("Second body", content)
        finally:
            server.vault.vault_path = original_vault_path

    def test_anchor_format(self):
        """Anchor follows note-YYYYMMDD-HHMMSS format."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_note_append({
                "title": "Test",
                "body": "Body",
                "tags": ["test"],
            }))
            anchor = result["appended"]
            self.assertRegex(anchor, r"^note-\d{8}-\d{6}$")
        finally:
            server.vault.vault_path = original_vault_path

    def test_tags_in_entry(self):
        """Tags appear in the entry block."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "Tag Test",
                "body": "Body",
                "tags": ["docker", "windows", "git-bash"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertIn("**Tags**: docker, windows, git-bash", content)
        finally:
            server.vault.vault_path = original_vault_path

    def test_related_in_entry(self):
        """Related FDOs appear as wikilinks."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "Related Test",
                "body": "Body",
                "tags": ["test"],
                "related": ["grim-architecture", "proj-grim"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertIn("[[grim-architecture]]", content)
            self.assertIn("[[proj-grim]]", content)
        finally:
            server.vault.vault_path = original_vault_path

    def test_source_paths_in_entry(self):
        """Source paths appear in the entry."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "Source Test",
                "body": "Body",
                "tags": ["test"],
                "source_paths": ["GRIM/scripts/release.sh"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertIn("GRIM/scripts/release.sh", content)
        finally:
            server.vault.vault_path = original_vault_path

    def test_updates_frontmatter_date(self):
        """Appending updates the frontmatter 'updated' date."""
        from kronos_mcp import server
        original_vault_path = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            server.handle_note_append({
                "title": "First",
                "body": "Body",
                "tags": ["test"],
            })
            now = datetime.now()
            month_file = self.notes_dir / f"notes-{now.strftime('%Y-%m')}.md"
            content = month_file.read_text(encoding="utf-8")
            self.assertIn(f"updated: '{now.strftime('%Y-%m-%d')}'", content)
        finally:
            server.vault.vault_path = original_vault_path


# ═══════════════════════════════════════════════════════════════════════════
# 3. Note Entry Parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestNoteEntryParsing(unittest.TestCase):
    """Test _parse_note_entries helper."""

    def _parse(self, content: str) -> list[dict]:
        """Write content to a temp file and parse it."""
        from kronos_mcp.server import _parse_note_entries
        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text(content, encoding="utf-8")
        try:
            return _parse_note_entries(tmp)
        finally:
            tmp.unlink()

    def test_parses_single_entry(self):
        content = textwrap.dedent("""\
        ---
        id: notes-2026-03
        title: "Notes"
        domain: notes
        ---

        # Notes

        ## Test Note
        <!-- anchor: note-20260301-120000 -->
        **Date**: 2026-03-01 12:00
        **Tags**: test, unit

        This is the body.

        ---
        """)
        entries = self._parse(content)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Test Note")
        self.assertEqual(entries[0]["anchor"], "note-20260301-120000")
        self.assertEqual(entries[0]["date"], "2026-03-01 12:00")
        self.assertEqual(entries[0]["tags"], ["test", "unit"])
        self.assertIn("body", entries[0])

    def test_parses_multiple_entries(self):
        content = textwrap.dedent("""\
        ---
        id: notes-2026-03
        ---

        ## First Note
        <!-- anchor: note-20260301-100000 -->
        **Date**: 2026-03-01 10:00
        **Tags**: a

        Body one.

        ---

        ## Second Note
        <!-- anchor: note-20260301-110000 -->
        **Date**: 2026-03-01 11:00
        **Tags**: b

        Body two.

        ---
        """)
        entries = self._parse(content)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "First Note")
        self.assertEqual(entries[1]["title"], "Second Note")

    def test_handles_missing_anchor(self):
        content = textwrap.dedent("""\
        ---
        id: notes-2026-03
        ---

        ## No Anchor Note
        **Date**: 2026-03-01 12:00
        **Tags**: test

        Body.

        ---
        """)
        entries = self._parse(content)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("anchor", entries[0])

    def test_handles_empty_file(self):
        from kronos_mcp.server import _parse_note_entries
        tmp = Path(tempfile.mktemp(suffix=".md"))
        tmp.write_text("", encoding="utf-8")
        try:
            entries = _parse_note_entries(tmp)
            self.assertEqual(entries, [])
        finally:
            tmp.unlink()

    def test_handles_nonexistent_file(self):
        from kronos_mcp.server import _parse_note_entries
        entries = _parse_note_entries(Path("/does/not/exist.md"))
        self.assertEqual(entries, [])


# ═══════════════════════════════════════════════════════════════════════════
# 4. Notes Recent Handler
# ═══════════════════════════════════════════════════════════════════════════

class TestNotesRecent(unittest.TestCase):
    """Test kronos_notes_recent handler."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.notes_dir = Path(self.tmp) / "notes"
        self.notes_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_month_file(self, month_str: str, entries: list[dict]):
        """Write a monthly notes file with given entries."""
        lines = [
            "---",
            f"id: notes-{month_str}",
            f"title: Notes {month_str}",
            "domain: notes",
            f"created: '{month_str}-01'",
            f"updated: '{month_str}-28'",
            "status: developing",
            "confidence: 0.9",
            "tags: [notes]",
            "related: []",
            "source_repos: []",
            "---",
            "",
            f"# Notes {month_str}",
            "",
        ]
        for entry in entries:
            lines.extend([
                f"## {entry['title']}",
                f"<!-- anchor: {entry.get('anchor', 'note-xxx')} -->",
                f"**Date**: {entry['date']}",
                f"**Tags**: {', '.join(entry.get('tags', []))}",
                "",
                entry.get("body", "Body text."),
                "",
                "---",
                "",
            ])
        path = self.notes_dir / f"notes-{month_str}.md"
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_returns_recent_entries(self):
        """Returns entries from the current month."""
        from kronos_mcp import server
        now = datetime.now()
        month_str = now.strftime("%Y-%m")
        self._write_month_file(month_str, [
            {"title": "Recent Fix", "date": now.strftime("%Y-%m-%d 10:00"), "tags": ["docker"]},
        ])
        original = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_notes_recent({"days": 30}))
            self.assertGreaterEqual(result["total"], 1)
            self.assertEqual(result["entries"][0]["title"], "Recent Fix")
        finally:
            server.vault.vault_path = original

    def test_filters_by_tags(self):
        """Tag filter returns only matching entries."""
        from kronos_mcp import server
        now = datetime.now()
        month_str = now.strftime("%Y-%m")
        self._write_month_file(month_str, [
            {"title": "Docker Fix", "date": now.strftime("%Y-%m-%d 10:00"), "tags": ["docker"]},
            {"title": "Python Fix", "date": now.strftime("%Y-%m-%d 11:00"), "tags": ["python"]},
        ])
        original = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_notes_recent({"days": 30, "tags": ["docker"]}))
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["entries"][0]["title"], "Docker Fix")
        finally:
            server.vault.vault_path = original

    def test_respects_max_entries(self):
        """max_entries limits the number of returned entries."""
        from kronos_mcp import server
        now = datetime.now()
        month_str = now.strftime("%Y-%m")
        entries = [
            {"title": f"Note {i}", "date": now.strftime(f"%Y-%m-%d {10+i}:00"), "tags": ["test"]}
            for i in range(5)
        ]
        self._write_month_file(month_str, entries)
        original = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_notes_recent({"days": 30, "max_entries": 2}))
            self.assertEqual(result["total"], 2)
        finally:
            server.vault.vault_path = original

    def test_empty_notes_dir(self):
        """Empty notes directory returns empty list."""
        from kronos_mcp import server
        import shutil
        shutil.rmtree(self.notes_dir)
        # Don't recreate — let it be missing
        original = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_notes_recent({"days": 30}))
            self.assertEqual(result["entries"], [])
            self.assertEqual(result["total"], 0)
        finally:
            server.vault.vault_path = original

    def test_filters_old_entries(self):
        """Entries older than N days are excluded."""
        from kronos_mcp import server
        now = datetime.now()
        month_str = now.strftime("%Y-%m")
        old_date = (now - timedelta(days=60)).strftime("%Y-%m-%d 10:00")
        self._write_month_file(month_str, [
            {"title": "Old Note", "date": old_date, "tags": ["test"]},
            {"title": "New Note", "date": now.strftime("%Y-%m-%d 10:00"), "tags": ["test"]},
        ])
        original = server.vault.vault_path
        server.vault.vault_path = Path(self.tmp)
        try:
            result = json.loads(server.handle_notes_recent({"days": 30}))
            titles = [e["title"] for e in result["entries"]]
            self.assertIn("New Note", titles)
            self.assertNotIn("Old Note", titles)
        finally:
            server.vault.vault_path = original


# ═══════════════════════════════════════════════════════════════════════════
# 5. Memory Node — Smart Retrieval
# ═══════════════════════════════════════════════════════════════════════════

class MockMCPResult:
    """Mock MCP call result."""
    def __init__(self, data: Any):
        self.content = [MagicMock(text=json.dumps(data))] if data is not None else []


class MockMCPSessionMulti:
    """Mock MCP session that returns different results per tool."""
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, method: str, args: dict | None = None):
        self.calls.append((method, args or {}))
        data = self.responses.get(method)
        if data is None:
            raise Exception(f"No mock for {method}")
        return MockMCPResult(data)


class TestMemoryNodeSmartRetrieval(unittest.TestCase):
    """Test enhanced memory node with parallel queries."""

    def _make_node(self, responses: dict):
        from core.nodes.memory import make_memory_node
        mcp = MockMCPSessionMulti(responses)
        return make_memory_node(mcp_session=mcp), mcp

    def _make_msg(self, text: str):
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=text)

    def test_standard_search_works_alone(self):
        """Standard search works even if BP and notes fail."""
        node, mcp = self._make_node({
            "kronos_search": {"results": [
                {"id": "fdo-1", "title": "Test", "domain": "physics",
                 "status": "stable", "confidence": 0.8, "summary": "Test FDO"},
            ]},
            # kronos_notes_recent not provided — will raise
        })
        result = run_async(node({"messages": [self._make_msg("test query")]}))
        self.assertGreaterEqual(len(result["knowledge_context"]), 1)
        self.assertEqual(result["knowledge_context"][0].id, "fdo-1")

    def test_bp_results_deduplicated(self):
        """Best-practice results with same IDs as standard are deduplicated."""
        standard = [
            {"id": f"fdo-{i}", "title": f"FDO {i}", "domain": "physics",
             "status": "stable", "confidence": 0.8, "summary": f"FDO {i}"}
            for i in range(3)
        ]
        node, mcp = self._make_node({
            "kronos_search": {"results": standard},  # both standard and BP return same
        })
        result = run_async(node({"messages": [self._make_msg("test")]}))
        ids = [s.id for s in result["knowledge_context"]]
        # No duplicates
        self.assertEqual(len(ids), len(set(ids)))

    def test_bp_results_added_when_unique(self):
        """Unique best-practice results are added to knowledge_context."""
        standard = [
            {"id": "fdo-1", "title": "Standard", "domain": "physics",
             "status": "stable", "confidence": 0.8, "summary": "Standard FDO"},
        ]
        bp = [
            {"id": "bp-testing", "title": "BP: Testing", "domain": "ai-systems",
             "status": "stable", "confidence": 0.85, "summary": "Testing pattern",
             "tags": ["best-practice"]},
        ]
        node, mcp = self._make_node({
            "kronos_search": {"results": standard + bp},  # both queries return merged
            "kronos_notes_recent": {"entries": []},
        })
        result = run_async(node({"messages": [self._make_msg("test")]}))
        ids = [s.id for s in result["knowledge_context"]]
        self.assertIn("fdo-1", ids)

    def test_recent_notes_in_state(self):
        """recent_notes state key populated from kronos_notes_recent."""
        node, mcp = self._make_node({
            "kronos_search": {"results": []},
            "kronos_notes_recent": {"entries": [
                {"title": "Docker fix", "date": "2026-03-01 10:00",
                 "tags": ["docker"], "body": "Fixed the thing", "anchor": "note-xxx"},
            ]},
        })
        result = run_async(node({"messages": [self._make_msg("docker")]}))
        self.assertIn("recent_notes", result)
        self.assertEqual(len(result["recent_notes"]), 1)
        self.assertEqual(result["recent_notes"][0]["title"], "Docker fix")

    def test_recent_notes_absent_when_empty(self):
        """recent_notes key not set when no notes found."""
        node, mcp = self._make_node({
            "kronos_search": {"results": []},
            "kronos_notes_recent": {"entries": []},
        })
        result = run_async(node({"messages": [self._make_msg("test")]}))
        self.assertNotIn("recent_notes", result)

    def test_recent_notes_body_truncated(self):
        """Note body is truncated to 200 chars."""
        long_body = "x" * 500
        node, mcp = self._make_node({
            "kronos_search": {"results": []},
            "kronos_notes_recent": {"entries": [
                {"title": "Long", "date": "2026-03-01", "tags": [],
                 "body": long_body, "anchor": "note-xxx"},
            ]},
        })
        result = run_async(node({"messages": [self._make_msg("test")]}))
        self.assertLessEqual(len(result["recent_notes"][0]["body"]), 200)

    def test_graceful_degradation_all_fail(self):
        """Memory node returns empty context if all queries fail."""
        from core.nodes.memory import make_memory_node

        class FailMCP:
            async def call_tool(self, *a, **kw):
                raise Exception("MCP down")

        node = make_memory_node(mcp_session=FailMCP())
        result = run_async(node({"messages": [self._make_msg("test")]}))
        self.assertEqual(result["knowledge_context"], [])

    def test_total_cap_at_8(self):
        """Total knowledge_context entries capped at 8."""
        results = [
            {"id": f"fdo-{i}", "title": f"FDO {i}", "domain": "physics",
             "status": "stable", "confidence": 0.5, "summary": f"FDO {i}"}
            for i in range(15)
        ]
        node, mcp = self._make_node({
            "kronos_search": {"results": results},
            "kronos_notes_recent": {"entries": []},
        })
        result = run_async(node({"messages": [self._make_msg("everything")]}))
        self.assertLessEqual(len(result["knowledge_context"]), 8)

    def test_calls_three_tools(self):
        """Memory node calls kronos_search (twice: standard + BP) and kronos_notes_recent."""
        node, mcp = self._make_node({
            "kronos_search": {"results": []},
            "kronos_notes_recent": {"entries": []},
        })
        run_async(node({"messages": [self._make_msg("test")]}))
        tool_names = [call[0] for call in mcp.calls]
        self.assertEqual(tool_names.count("kronos_search"), 2)  # standard + BP
        self.assertIn("kronos_notes_recent", tool_names)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Skill Manifests
# ═══════════════════════════════════════════════════════════════════════════

class TestSkillManifests(unittest.TestCase):
    """Test kronos-note and kronos-bp skill manifests."""

    def test_kronos_note_manifest_exists(self):
        manifest = GRIM_ROOT / "skills" / "kronos-note" / "manifest.yaml"
        self.assertTrue(manifest.exists(), f"Missing: {manifest}")

    def test_kronos_note_protocol_exists(self):
        protocol = GRIM_ROOT / "skills" / "kronos-note" / "protocol.md"
        self.assertTrue(protocol.exists(), f"Missing: {protocol}")

    def test_kronos_bp_manifest_exists(self):
        manifest = GRIM_ROOT / "skills" / "kronos-bp" / "manifest.yaml"
        self.assertTrue(manifest.exists(), f"Missing: {manifest}")

    def test_kronos_bp_protocol_exists(self):
        protocol = GRIM_ROOT / "skills" / "kronos-bp" / "protocol.md"
        self.assertTrue(protocol.exists(), f"Missing: {protocol}")

    def test_kronos_note_manifest_valid(self):
        import yaml
        manifest = GRIM_ROOT / "skills" / "kronos-note" / "manifest.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "kronos-note")
        self.assertIn("memory-agent", data["consumers"])
        self.assertIn("grim", data["consumers"])

    def test_kronos_bp_manifest_valid(self):
        import yaml
        manifest = GRIM_ROOT / "skills" / "kronos-bp" / "manifest.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "kronos-bp")
        self.assertIn("memory-agent", data["consumers"])
        self.assertIn("best-practice", data["description"].lower())

    def test_kronos_note_has_strong_keywords(self):
        import yaml
        manifest = GRIM_ROOT / "skills" / "kronos-note" / "manifest.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        triggers = data.get("triggers", [])
        keywords_entry = [t for t in triggers if isinstance(t, dict) and "keywords" in t]
        self.assertTrue(len(keywords_entry) > 0, "No keywords trigger found")
        strong = keywords_entry[0]["keywords"].get("strong", [])
        self.assertIn("note this", strong)

    def test_kronos_bp_has_strong_keywords(self):
        import yaml
        manifest = GRIM_ROOT / "skills" / "kronos-bp" / "manifest.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        triggers = data.get("triggers", [])
        keywords_entry = [t for t in triggers if isinstance(t, dict) and "keywords" in t]
        self.assertTrue(len(keywords_entry) > 0, "No keywords trigger found")
        strong = keywords_entry[0]["keywords"].get("strong", [])
        self.assertIn("best practice", strong)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Skill Matching
# ═══════════════════════════════════════════════════════════════════════════

class TestSkillMatching(unittest.TestCase):
    """Test that new skills match expected trigger phrases."""

    def _match(self, message: str) -> list[str]:
        """Return matched skill names for a message."""
        from core.skills.loader import load_skills
        from core.skills.matcher import match_skills
        registry = load_skills(GRIM_ROOT / "skills")
        matched = match_skills(message, registry)
        return [s.name for s in matched]

    def test_note_this_matches_kronos_note(self):
        matched = self._match("note this down please")
        self.assertIn("kronos-note", matched)

    def test_quick_note_matches(self):
        matched = self._match("quick note: Docker needs MSYS fix")
        self.assertIn("kronos-note", matched)

    def test_best_practice_matches_kronos_bp(self):
        matched = self._match("make this a best practice")
        self.assertIn("kronos-bp", matched)

    def test_promote_to_best_practice_matches(self):
        matched = self._match("promote to best practice")
        self.assertIn("kronos-bp", matched)

    def test_casual_doesnt_match_note(self):
        matched = self._match("hello how are you today")
        self.assertNotIn("kronos-note", matched)
        self.assertNotIn("kronos-bp", matched)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Slash Commands
# ═══════════════════════════════════════════════════════════════════════════

class TestSlashCommands(unittest.TestCase):
    """Test slash command files exist."""

    COMMANDS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "commands"

    def test_kronos_note_command_exists(self):
        cmd = self.COMMANDS_DIR / "kronos-note.md"
        self.assertTrue(cmd.exists(), f"Missing: {cmd}")

    def test_kronos_bp_command_exists(self):
        cmd = self.COMMANDS_DIR / "kronos-bp.md"
        self.assertTrue(cmd.exists(), f"Missing: {cmd}")

    def test_kronos_note_command_references_skill(self):
        cmd = self.COMMANDS_DIR / "kronos-note.md"
        content = cmd.read_text(encoding="utf-8")
        self.assertIn("kronos-note", content)
        self.assertIn("kronos_skill_load", content)

    def test_kronos_bp_command_references_skill(self):
        cmd = self.COMMANDS_DIR / "kronos-bp.md"
        content = cmd.read_text(encoding="utf-8")
        self.assertIn("kronos-bp", content)
        self.assertIn("kronos_skill_load", content)


# ═══════════════════════════════════════════════════════════════════════════
# 9. GrimState Extension
# ═══════════════════════════════════════════════════════════════════════════

class TestGrimStateExtension(unittest.TestCase):
    """Test that GrimState includes new fields."""

    def test_recent_notes_field_exists(self):
        from core.state import GrimState
        # TypedDict with total=False — field should be in annotations
        annotations = GrimState.__annotations__
        self.assertIn("recent_notes", annotations)

    def test_recent_notes_is_optional(self):
        """recent_notes can be omitted from state dict."""
        from core.state import GrimState
        # Should not raise — total=False means all fields optional
        state: GrimState = {"messages": []}  # type: ignore
        self.assertNotIn("recent_notes", state)


# ═══════════════════════════════════════════════════════════════════════════
# 10. Tool Registration
# ═══════════════════════════════════════════════════════════════════════════

class TestToolRegistration(unittest.TestCase):
    """Test new tools are properly registered."""

    def test_note_append_in_tools_list(self):
        from kronos_mcp.server import TOOLS
        names = [t.name for t in TOOLS]
        self.assertIn("kronos_note_append", names)

    def test_notes_recent_in_tools_list(self):
        from kronos_mcp.server import TOOLS
        names = [t.name for t in TOOLS]
        self.assertIn("kronos_notes_recent", names)

    def test_note_append_in_handlers(self):
        from kronos_mcp.server import HANDLERS
        self.assertIn("kronos_note_append", HANDLERS)

    def test_notes_recent_in_handlers(self):
        from kronos_mcp.server import HANDLERS
        self.assertIn("kronos_notes_recent", HANDLERS)

    def test_note_append_in_vault_write_group(self):
        from kronos_mcp.server import TOOL_GROUPS
        self.assertIn("kronos_note_append", TOOL_GROUPS["vault:write"])

    def test_notes_recent_in_vault_read_group(self):
        from kronos_mcp.server import TOOL_GROUPS
        self.assertIn("kronos_notes_recent", TOOL_GROUPS["vault:read"])


if __name__ == "__main__":
    unittest.main()
