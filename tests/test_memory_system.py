"""Tests for GRIM's persistent memory system.

Covers:
- memory_store.py (read/write/parse/update sections)
- memory_tools.py (LangChain tools for agents)
- identity node (working memory injection into system prompt)
- evolve node (auto-update memory.md)
- router node (memory skill routing, follow-up signals)
- config.py (save_config_updates)
- server/app.py (GET/POST /api/memory, POST /api/config)
- skill manifests (memory-read, memory-update)

Run: cd GRIM && python -m pytest tests/test_memory_system.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

# Ensure GRIM root and MCP server are on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))
sys.path.insert(0, str(GRIM_ROOT / "mcp" / "kronos" / "src"))

# Set KRONOS_VAULT_PATH for MCP server import (uses a temp dir, overridden per-test)
if not os.environ.get("KRONOS_VAULT_PATH"):
    os.environ["KRONOS_VAULT_PATH"] = str(GRIM_ROOT / "tests" / "vault")

from core.state import AgentResult, FDOSummary, FieldState, GrimState, SkillContext


# ═══════════════════════════════════════════════════════════════════════════
# Test infrastructure (same patterns as test_grim_core.py)
# ═══════════════════════════════════════════════════════════════════════════

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


class MockMCPResult:
    def __init__(self, data: dict | list | str):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(text=text)]


class MockMCPSession:
    def __init__(self, responses: dict[str, Any] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, method: str, args: dict | None = None) -> MockMCPResult:
        self.calls.append((method, args or {}))
        if method in self._responses:
            return MockMCPResult(self._responses[method])
        return MockMCPResult({"results": []})


def make_test_config(**overrides):
    from core.config import GrimConfig
    cfg = GrimConfig(
        env="debug",
        vault_path=GRIM_ROOT / "tests" / "vault",
        skills_path=GRIM_ROOT / "skills",
        identity_prompt_path=GRIM_ROOT / "identity" / "system_prompt.md",
        identity_personality_path=GRIM_ROOT / "identity" / "personality.yaml",
        personality_cache_path=GRIM_ROOT / "identity" / "personality.cache.md",
        local_dir=GRIM_ROOT / "local",
        model="claude-sonnet-4-6",
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_human_message(text: str):
    from langchain_core.messages import HumanMessage
    return HumanMessage(content=text)


def make_ai_message(text: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content=text)


SAMPLE_MEMORY = textwrap.dedent("""\
    # GRIM Working Memory

    ## Active Objectives
    - [active] Implement persistent memory system
    - [active] Fix capability routing bug
    - [completed] Build Settings page

    ## Recent Topics
    - 2026-02-28: Memory system design
    - 2026-02-28: UI terminal restyle

    ## User Preferences
    - Peter prefers terminal/CLI aesthetic in UI
    - Vault sync is mandatory after code changes

    ## Key Learnings
    - Trace data needs _activeNode tagging for step bubbles
    - Agent fallback protocols prevent capability amnesia

    ## Future Goals
    - IronClaw Phase 2 integration
    - Multi-user support

    ## Session Notes
    - 2026-02-28: Built memory system, settings page, routing fixes
""")


# ═══════════════════════════════════════════════════════════════════════════
# 1. memory_store.py — read/write/parse/update
# ═══════════════════════════════════════════════════════════════════════════

class TestMemoryStore(unittest.TestCase):
    """Test memory_store.py file I/O and parsing."""

    def test_read_memory_exists(self):
        """Read memory.md when it exists."""
        from core.memory_store import read_memory
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            content = read_memory(vault)
            self.assertIn("Active Objectives", content)
            self.assertIn("GRIM Working Memory", content)

    def test_read_memory_missing(self):
        """Read returns empty string when file doesn't exist."""
        from core.memory_store import read_memory
        with tempfile.TemporaryDirectory() as tmp:
            content = read_memory(Path(tmp))
            self.assertEqual(content, "")

    def test_write_memory(self):
        """Write memory.md to vault."""
        from core.memory_store import read_memory, write_memory
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write_memory(vault, SAMPLE_MEMORY)
            self.assertTrue((vault / "memory.md").exists())
            content = read_memory(vault)
            self.assertEqual(content, SAMPLE_MEMORY)

    def test_parse_sections(self):
        """Parse H2 sections from markdown."""
        from core.memory_store import parse_memory_sections
        sections = parse_memory_sections(SAMPLE_MEMORY)
        self.assertIn("Active Objectives", sections)
        self.assertIn("Recent Topics", sections)
        self.assertIn("User Preferences", sections)
        self.assertIn("Key Learnings", sections)
        self.assertIn("Future Goals", sections)
        self.assertIn("Session Notes", sections)
        self.assertEqual(len(sections), 6)

    def test_parse_sections_content(self):
        """Parsed section content is correct."""
        from core.memory_store import parse_memory_sections
        sections = parse_memory_sections(SAMPLE_MEMORY)
        objectives = sections["Active Objectives"]
        self.assertIn("[active] Implement persistent memory system", objectives)
        self.assertIn("[completed] Build Settings page", objectives)

    def test_parse_sections_strips_comments(self):
        """HTML comments are stripped from section content."""
        from core.memory_store import parse_memory_sections
        md = "# Memory\n\n## Test\n<!-- comment -->\n- item\n"
        sections = parse_memory_sections(md)
        self.assertNotIn("comment", sections["Test"])
        self.assertIn("item", sections["Test"])

    def test_parse_empty_content(self):
        """Parsing empty content returns empty dict."""
        from core.memory_store import parse_memory_sections
        self.assertEqual(parse_memory_sections(""), {})
        self.assertEqual(parse_memory_sections("   "), {})

    def test_update_section_existing(self):
        """Update an existing section."""
        from core.memory_store import update_section
        updated = update_section(
            SAMPLE_MEMORY,
            "Future Goals",
            "- World domination\n- Better testing",
        )
        self.assertIn("World domination", updated)
        self.assertIn("Better testing", updated)
        # Other sections should still be present
        self.assertIn("Active Objectives", updated)
        self.assertIn("User Preferences", updated)

    def test_update_section_new(self):
        """Append a new section when it doesn't exist."""
        from core.memory_store import update_section
        updated = update_section(
            SAMPLE_MEMORY,
            "Debug Notes",
            "- Found a tricky bug in router",
        )
        self.assertIn("## Debug Notes", updated)
        self.assertIn("tricky bug", updated)

    def test_memory_path(self):
        """memory_path returns correct path."""
        from core.memory_store import memory_path
        p = memory_path(Path("/some/vault"))
        self.assertEqual(p, Path("/some/vault/memory.md"))

    def test_roundtrip(self):
        """Write then read back produces identical content."""
        from core.memory_store import read_memory, write_memory
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write_memory(vault, SAMPLE_MEMORY)
            result = read_memory(vault)
            self.assertEqual(result, SAMPLE_MEMORY)


# ═══════════════════════════════════════════════════════════════════════════
# 2. memory_tools.py — LangChain tools (now MCP-backed)
# ═══════════════════════════════════════════════════════════════════════════

class TestMemoryTools(unittest.TestCase):
    """Test LangChain memory tools (MCP-backed)."""

    def test_read_tool_returns_content(self):
        """read_grim_memory returns content via MCP."""
        from core.tools.memory_tools import read_grim_memory
        mock_session = MockMCPSession(responses={
            "kronos_memory_read": {"content": SAMPLE_MEMORY, "sections": ["Active Objectives"]},
        })
        with patch("core.tools.kronos_read._mcp_session", mock_session):
            result = run_async(read_grim_memory.ainvoke({}))
            self.assertIn("Active Objectives", result)

    def test_read_tool_empty_memory(self):
        """read_grim_memory handles empty memory gracefully."""
        from core.tools.memory_tools import read_grim_memory
        mock_session = MockMCPSession(responses={
            "kronos_memory_read": {"content": "", "sections": []},
        })
        with patch("core.tools.kronos_read._mcp_session", mock_session):
            result = run_async(read_grim_memory.ainvoke({}))
            # Empty content returns "(memory is empty)"
            self.assertIn("empty", result.lower())

    def test_read_tool_no_mcp(self):
        """read_grim_memory without MCP session returns error."""
        from core.tools.memory_tools import read_grim_memory
        with patch("core.tools.kronos_read._mcp_session", None):
            result = run_async(read_grim_memory.ainvoke({}))
            # Should get an error about vault not connected
            self.assertTrue("error" in result.lower() or "not connected" in result.lower())

    def test_update_tool_calls_mcp(self):
        """update_grim_memory calls MCP memory_update."""
        from core.tools.memory_tools import update_grim_memory
        mock_session = MockMCPSession(responses={
            "kronos_memory_update": {"ok": True, "section": "Key Learnings", "char_count": 30},
        })
        with patch("core.tools.kronos_read._mcp_session", mock_session):
            result = run_async(update_grim_memory.ainvoke({
                "section": "Key Learnings",
                "content": "- New learning: tests matter",
            }))
            self.assertIn("Updated", result)
            # Verify MCP was called
            self.assertTrue(any(
                call[0] == "kronos_memory_update" for call in mock_session.calls
            ))

    def test_read_tool_with_section(self):
        """read_grim_memory can read a specific section."""
        from core.tools.memory_tools import read_grim_memory
        mock_session = MockMCPSession(responses={
            "kronos_memory_read": {"content": "- Peter prefers CLI aesthetic", "section": "User Preferences"},
        })
        with patch("core.tools.kronos_read._mcp_session", mock_session):
            result = run_async(read_grim_memory.ainvoke({"section": "User Preferences"}))
            self.assertIn("CLI aesthetic", result)

    def test_tools_list(self):
        """MEMORY_TOOLS contains both tools."""
        from core.tools.memory_tools import MEMORY_TOOLS
        self.assertEqual(len(MEMORY_TOOLS), 2)
        names = {t.name for t in MEMORY_TOOLS}
        self.assertIn("read_grim_memory", names)
        self.assertIn("update_grim_memory", names)


# ═══════════════════════════════════════════════════════════════════════════
# 2b. MCP Memory handlers (server-side)
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPMemoryHandlers(unittest.TestCase):
    """Test MCP memory tool handlers directly.

    These test the pure-function handlers from the MCP server without
    importing the full server module (which requires KRONOS_VAULT_PATH).
    We test the memory parsing/writing helpers directly instead.
    """

    def test_memory_parse_sections(self):
        """Memory section parsing works correctly."""
        # Import from the MCP server source directly
        from kronos_mcp.server import _parse_memory_sections
        sections = _parse_memory_sections(SAMPLE_MEMORY)
        self.assertIn("Active Objectives", sections)
        self.assertIn("User Preferences", sections)
        self.assertEqual(len(sections), 6)

    def test_memory_update_section(self):
        """Memory section update replaces content."""
        from kronos_mcp.server import _update_memory_section
        updated = _update_memory_section(
            SAMPLE_MEMORY,
            "Key Learnings",
            "- MCP memory tools work great",
        )
        self.assertIn("MCP memory tools work great", updated)
        # Other sections preserved
        self.assertIn("Active Objectives", updated)

    def test_memory_update_new_section(self):
        """Memory update appends new section when it doesn't exist."""
        from kronos_mcp.server import _update_memory_section
        updated = _update_memory_section(SAMPLE_MEMORY, "Debug Notes", "- found a bug")
        self.assertIn("## Debug Notes", updated)
        self.assertIn("found a bug", updated)

    def test_memory_append_section(self):
        """Memory append adds to existing section."""
        from kronos_mcp.server import _append_to_memory_section
        updated = _append_to_memory_section(
            SAMPLE_MEMORY,
            "Key Learnings",
            "- Appended learning",
        )
        # Original content still present
        self.assertIn("Trace data needs _activeNode", updated)
        # New content added
        self.assertIn("Appended learning", updated)

    def test_memory_clean_section(self):
        """HTML comments are stripped from sections."""
        from kronos_mcp.server import _clean_memory_section
        result = _clean_memory_section("<!-- comment -->\n- item\n")
        self.assertNotIn("comment", result)
        self.assertIn("item", result)

    def test_memory_read_write_roundtrip(self):
        """Read/write roundtrip via handler helpers."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            from kronos_mcp.server import _read_memory_file, _write_memory_file
            with patch("kronos_mcp.server.vault_path", str(vault)):
                content = _read_memory_file()
                self.assertIn("Active Objectives", content)
                _write_memory_file("# New\n\n## Test\n- hello\n")
                content2 = _read_memory_file()
                self.assertIn("hello", content2)

    def test_handle_memory_read_full(self):
        """handle_memory_read returns full content."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            from kronos_mcp.server import handle_memory_read
            with patch("kronos_mcp.server.vault_path", str(vault)):
                result = json.loads(handle_memory_read({}))
                self.assertIn("content", result)
                self.assertIn("Active Objectives", result["content"])

    def test_handle_memory_update_section(self):
        """handle_memory_update replaces a section and persists."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            from kronos_mcp.server import handle_memory_update
            with patch("kronos_mcp.server.vault_path", str(vault)):
                result = json.loads(handle_memory_update({
                    "section": "Key Learnings",
                    "content": "- MCP memory tools work great",
                }))
                self.assertTrue(result.get("ok"))
                content = (vault / "memory.md").read_text(encoding="utf-8")
                self.assertIn("MCP memory tools work great", content)

    def test_handle_memory_update_full_content(self):
        """handle_memory_update with full_content replaces entire file."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            new_content = "# GRIM Working Memory\n\n## New Section\n- brand new content\n"
            from kronos_mcp.server import handle_memory_update
            with patch("kronos_mcp.server.vault_path", str(vault)):
                result = json.loads(handle_memory_update({"full_content": new_content}))
                self.assertTrue(result.get("ok"))
                content = (vault / "memory.md").read_text(encoding="utf-8")
                self.assertIn("brand new content", content)

    def test_handle_memory_update_rejects_both(self):
        """handle_memory_update rejects section + full_content together."""
        from kronos_mcp.server import handle_memory_update
        result = json.loads(handle_memory_update({
            "section": "Test",
            "content": "x",
            "full_content": "# Full\n\n## Test\n- x\n",
        }))
        self.assertIn("error", result)

    def test_handle_memory_sections(self):
        """handle_memory_sections lists all sections."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            from kronos_mcp.server import handle_memory_sections
            with patch("kronos_mcp.server.vault_path", str(vault)):
                result = json.loads(handle_memory_sections({}))
                self.assertIn("sections", result)
                names = [s["name"] for s in result["sections"]]
                self.assertIn("Active Objectives", names)
                self.assertEqual(len(names), 6)

    def test_tool_groups(self):
        """TOOL_GROUPS contains expected groups."""
        from kronos_mcp.server import TOOL_GROUPS
        self.assertIn("vault:read", TOOL_GROUPS)
        self.assertIn("vault:write", TOOL_GROUPS)
        self.assertIn("memory:read", TOOL_GROUPS)
        self.assertIn("memory:write", TOOL_GROUPS)
        self.assertIn("source:read", TOOL_GROUPS)
        self.assertIn("system", TOOL_GROUPS)
        # Memory tools are in correct groups
        self.assertIn("kronos_memory_read", TOOL_GROUPS["memory:read"])
        self.assertIn("kronos_memory_update", TOOL_GROUPS["memory:write"])

    def test_memory_write_doesnt_touch_fdos(self):
        """Memory write operations should never modify FDO files."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            ai_dir = vault / "ai-systems"
            ai_dir.mkdir()
            fdo_content = "---\nid: test-fdo\n---\n# Test\n"
            (ai_dir / "test-fdo.md").write_text(fdo_content, encoding="utf-8")
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")

            from kronos_mcp.server import handle_memory_update
            with patch("kronos_mcp.server.vault_path", str(vault)):
                handle_memory_update({
                    "section": "Session Notes",
                    "content": "- Test session note",
                })
                fdo_after = (ai_dir / "test-fdo.md").read_text(encoding="utf-8")
                self.assertEqual(fdo_after, fdo_content)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Identity node — working memory loading
# ═══════════════════════════════════════════════════════════════════════════

class TestIdentityMemory(unittest.TestCase):
    """Test that identity node loads memory.md into system prompt."""

    def test_identity_loads_memory(self):
        """Identity node includes working memory in system prompt."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            cfg = make_test_config(
                vault_path=vault,
                personality_cache_path=Path(tmp) / "personality.cache.md",
                objectives_path=Path(tmp) / "objectives",
            )
            node = make_identity_node(cfg, mcp_session=None)
            result = run_async(node({}))
            prompt = result["system_prompt"]
            self.assertIn("Active Objectives", prompt)
            self.assertIn("GRIM Working Memory", prompt)

    def test_identity_no_memory_file(self):
        """Identity node works fine when memory.md doesn't exist."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            # No memory.md created
            cfg = make_test_config(
                vault_path=vault,
                personality_cache_path=Path(tmp) / "personality.cache.md",
                objectives_path=Path(tmp) / "objectives",
            )
            node = make_identity_node(cfg, mcp_session=None)
            result = run_async(node({}))
            # Should still produce a valid system prompt
            self.assertIn("system_prompt", result)
            self.assertIn("GRIM", result["system_prompt"])

    def test_identity_truncates_large_memory(self):
        """Identity node truncates memory > 2000 chars."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            # Create a very large memory file
            large_memory = "# GRIM Working Memory\n\n## Notes\n" + ("A" * 3000)
            (vault / "memory.md").write_text(large_memory, encoding="utf-8")
            cfg = make_test_config(
                vault_path=vault,
                personality_cache_path=Path(tmp) / "personality.cache.md",
                objectives_path=Path(tmp) / "objectives",
            )
            node = make_identity_node(cfg, mcp_session=None)
            result = run_async(node({}))
            prompt = result["system_prompt"]
            # Memory should be truncated
            self.assertIn("truncated", prompt)
            # Full 3000-char block should NOT be in the prompt
            self.assertNotIn("A" * 3000, prompt)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Evolve node — memory update wiring
# ═══════════════════════════════════════════════════════════════════════════

class TestEvolveMemory(unittest.TestCase):
    """Test that evolve node calls _update_working_memory."""

    def test_evolve_calls_memory_update_at_interval(self):
        """Evolve node triggers memory update every N turns."""
        from core.nodes.evolve import make_evolve_node
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")
            cfg = make_test_config(
                vault_path=vault,
                evolution_dir=Path(tmp) / "evolution",
                objectives_path=Path(tmp) / "objectives",
            )
            node = make_evolve_node(cfg)

            messages = [
                make_human_message("hello"),
                make_ai_message("hi there"),
                make_human_message("what is PAC?"),
                make_ai_message("PAC is a conservation law"),
            ]

            # Patch where the import resolves (langchain_anthropic module)
            with patch("langchain_anthropic.ChatAnthropic") as MockLLM:
                mock_instance = MagicMock()
                mock_response = MagicMock()
                mock_response.content = SAMPLE_MEMORY  # return valid memory
                mock_instance.ainvoke = AsyncMock(return_value=mock_response)
                MockLLM.return_value = mock_instance

                # Run evolve 5 times to trigger the interval
                for i in range(5):
                    run_async(node({
                        "field_state": FieldState(coherence=0.7, valence=0.3),
                        "knowledge_context": [],
                        "session_topics": [],
                        "messages": messages,
                        "objectives": [],
                    }))

                # LLM should have been called at turn 5 (interval)
                # Two calls: one for objectives, one for memory
                self.assertTrue(mock_instance.ainvoke.call_count >= 1)

    def test_evolve_memory_update_handles_failure(self):
        """Memory update failure doesn't crash the evolve node."""
        from core.nodes.evolve import make_evolve_node
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_test_config(
                vault_path=Path(tmp),
                evolution_dir=Path(tmp) / "evolution",
                objectives_path=Path(tmp) / "objectives",
            )
            node = make_evolve_node(cfg)

            messages = [make_human_message("hi"), make_ai_message("hello")] * 3

            with patch("langchain_anthropic.ChatAnthropic") as MockLLM:
                mock_instance = MagicMock()
                mock_instance.ainvoke = AsyncMock(side_effect=Exception("LLM down"))
                MockLLM.return_value = mock_instance

                # Should not raise — evolve gracefully handles failures
                for i in range(5):
                    result = run_async(node({
                        "field_state": FieldState(),
                        "knowledge_context": [],
                        "session_topics": [],
                        "messages": messages,
                        "objectives": [],
                    }))
                    self.assertIn("field_state", result)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Router node — memory skill routing + follow-up signals
# ═══════════════════════════════════════════════════════════════════════════

class TestRouterMemory(unittest.TestCase):
    """Test router handles memory skills and follow-up signals."""

    def _route(self, message: str, **state_overrides):
        """Helper: run router on a message and return result."""
        from core.nodes.router import make_router_node
        from core.config import GrimConfig
        router = make_router_node(GrimConfig())
        state = {
            "messages": [make_human_message(message)],
            "matched_skills": [],
            **state_overrides,
        }
        return run_async(router(state))

    def test_memory_skill_routes_to_memory(self):
        """memory-read skill routes to memory agent."""
        result = self._route(
            "what do you remember?",
            matched_skills=[
                SkillContext(
                    name="memory-read",
                    version="1.0",
                    description="Read GRIM's persistent working memory",
                    permissions=["memory:read"],
                ),
            ],
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")

    def test_memory_update_skill_routes_to_memory(self):
        """memory-update skill routes to memory agent."""
        result = self._route(
            "remember this for next time",
            matched_skills=[
                SkillContext(
                    name="memory-update",
                    version="1.0",
                    description="Update GRIM's persistent working memory",
                    permissions=["memory:write"],
                ),
            ],
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")

    def test_followup_dont_you(self):
        """'dont you have' triggers continuity re-delegation."""
        result = self._route(
            "dont you have cli access?",
            last_delegation_type="operate",
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_followup_cant_you(self):
        """'cant you' triggers continuity re-delegation."""
        result = self._route(
            "cant you just use the same tool?",
            last_delegation_type="operate",
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_followup_you_have(self):
        """'you have' triggers continuity re-delegation."""
        result = self._route(
            "you have shell access right?",
            last_delegation_type="operate",
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_followup_i_just_asked(self):
        """'i just asked' triggers continuity re-delegation."""
        result = self._route(
            "i just asked you to do that",
            last_delegation_type="code",
        )
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "code")

    def test_keyword_my_ip(self):
        """'my ip' keyword routes to operate."""
        result = self._route("whats my ip")
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_keyword_ip_address(self):
        """'ip address' keyword routes to operate."""
        result = self._route("show me my ip address")
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_action_intent_cli(self):
        """Action verb + 'cli' routes to operate."""
        result = self._route("check my cli setup")
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "operate")

    def test_no_followup_without_last_delegation(self):
        """Follow-up signals don't trigger without last_delegation_type."""
        result = self._route(
            "dont you think thats interesting?",
            # No last_delegation_type set
        )
        # Should fall through to companion (no keyword match for this)
        self.assertEqual(result["mode"], "companion")

    def test_memory_keyword_remember(self):
        """'remember this' routes to memory via keywords."""
        result = self._route("remember this for later")
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")

    def test_memory_keyword_vault(self):
        """'update the vault' routes to memory via keywords."""
        result = self._route("update the vault with this info")
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Config save/reload
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigSave(unittest.TestCase):
    """Test save_config_updates writes to grim.yaml and reloads."""

    def _make_config_dir(self, tmp: str) -> Path:
        """Create a minimal config directory with grim.yaml."""
        root = Path(tmp) / "grim"
        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        (root / "identity").mkdir()
        (root / "identity" / "system_prompt.md").write_text("You are GRIM.")
        (root / "identity" / "personality.yaml").write_text("field_state: {}")

        config = {
            "env": "debug",
            "vault_path": "../kronos-vault",
            "agent": {"default_model": "claude-sonnet-4-6", "temperature": 0.7, "max_tokens": 4096},
            "routing": {"enabled": True, "default_tier": "sonnet"},
            "context_management": {"max_tokens": 160000, "keep_recent": 12},
            "objectives": {"max_active": 10},
        }
        (config_dir / "grim.yaml").write_text(
            yaml.dump(config, default_flow_style=False),
            encoding="utf-8",
        )
        return root

    def test_save_model(self):
        """Save model update persists to YAML."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            cfg = save_config_updates({"model": "claude-opus-4-6"}, grim_root=root)
            self.assertEqual(cfg.model, "claude-opus-4-6")
            # Verify YAML was updated
            raw = yaml.safe_load((root / "config" / "grim.yaml").read_text())
            self.assertEqual(raw["agent"]["default_model"], "claude-opus-4-6")

    def test_save_temperature(self):
        """Save temperature update."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            cfg = save_config_updates({"temperature": 0.3}, grim_root=root)
            self.assertAlmostEqual(cfg.temperature, 0.3)

    def test_save_routing(self):
        """Save routing update."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            cfg = save_config_updates(
                {"routing": {"default_tier": "opus"}},
                grim_root=root,
            )
            self.assertEqual(cfg.routing_default_tier, "opus")

    def test_save_objectives_max(self):
        """Save objectives_max_active."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            cfg = save_config_updates(
                {"objectives_max_active": 20},
                grim_root=root,
            )
            self.assertEqual(cfg.objectives_max_active, 20)

    def test_save_context(self):
        """Save context window update."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            cfg = save_config_updates(
                {"context": {"max_tokens": 200000, "keep_recent": 16}},
                grim_root=root,
            )
            self.assertEqual(cfg.context_max_tokens, 200000)
            self.assertEqual(cfg.context_keep_recent, 16)

    def test_save_missing_config(self):
        """save_config_updates raises when config file is missing."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                save_config_updates({"model": "opus"}, grim_root=Path(tmp))

    def test_save_preserves_existing_keys(self):
        """Saving one key doesn't remove others."""
        from core.config import save_config_updates
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_config_dir(tmp)
            save_config_updates({"model": "claude-opus-4-6"}, grim_root=root)
            raw = yaml.safe_load((root / "config" / "grim.yaml").read_text())
            # Original keys should still be there
            self.assertIn("routing", raw)
            self.assertIn("context_management", raw)
            self.assertEqual(raw["env"], "debug")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Server API — GET/POST /api/memory, POST /api/config
# ═══════════════════════════════════════════════════════════════════════════

class TestMemoryAPI(unittest.TestCase):
    """Test FastAPI memory endpoints using TestClient."""

    @classmethod
    def setUpClass(cls):
        """Set up a test vault directory."""
        cls._tmp = tempfile.TemporaryDirectory()
        cls._vault = Path(cls._tmp.name)
        (cls._vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _make_client(self):
        """Create a FastAPI TestClient with mocked globals."""
        from fastapi.testclient import TestClient
        import server.app as app_module
        from core.tools.kronos_read import set_mcp_session

        # Ensure no MCP session — API should use direct file fallback
        set_mcp_session(None)

        # Restore sample memory (may have been modified by previous tests)
        (self._vault / "memory.md").write_text(SAMPLE_MEMORY, encoding="utf-8")

        # Patch server globals
        cfg = make_test_config(vault_path=self._vault)
        app_module._config = cfg
        app_module._graph = MagicMock()
        return TestClient(app_module.app)

    def test_get_memory(self):
        """GET /api/memory returns content and sections."""
        client = self._make_client()
        resp = client.get("/api/memory")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("content", data)
        self.assertIn("sections", data)
        self.assertIn("Active Objectives", data["sections"])
        self.assertIn("GRIM Working Memory", data["content"])

    def test_get_memory_sections_count(self):
        """GET /api/memory returns all 6 sections."""
        client = self._make_client()
        data = client.get("/api/memory").json()
        self.assertEqual(len(data["sections"]), 6)

    def test_post_memory(self):
        """POST /api/memory updates the file."""
        client = self._make_client()
        new_content = "# GRIM Working Memory\n\n## Test\n- hello world\n"
        resp = client.post(
            "/api/memory",
            json={"content": new_content},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Test", data["sections"])
        self.assertIn("hello world", data["sections"]["Test"])

    def test_post_memory_then_get(self):
        """POST then GET returns the updated content."""
        client = self._make_client()
        new_content = "# Memory\n\n## Updated\n- fresh data\n"
        client.post("/api/memory", json={"content": new_content})
        data = client.get("/api/memory").json()
        self.assertIn("Updated", data["sections"])

    def test_get_memory_no_config(self):
        """GET /api/memory returns 503 when config not loaded."""
        from fastapi.testclient import TestClient
        import server.app as app_module
        app_module._config = None
        client = TestClient(app_module.app)
        resp = client.get("/api/memory")
        self.assertEqual(resp.status_code, 503)


class TestConfigAPI(unittest.TestCase):
    """Test POST /api/config endpoint."""

    def _make_client_with_config(self):
        """Create a test client with a writable config."""
        from fastapi.testclient import TestClient
        import server.app as app_module

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        (root / "identity").mkdir()
        (root / "identity" / "system_prompt.md").write_text("You are GRIM.")
        (root / "identity" / "personality.yaml").write_text("field_state: {}")

        config = {
            "env": "debug",
            "vault_path": "../kronos-vault",
            "agent": {"default_model": "claude-sonnet-4-6", "temperature": 0.7},
            "routing": {"enabled": True, "default_tier": "sonnet"},
            "context_management": {"max_tokens": 160000, "keep_recent": 12},
            "objectives": {"max_active": 10},
        }
        (config_dir / "grim.yaml").write_text(
            yaml.dump(config, default_flow_style=False),
            encoding="utf-8",
        )

        cfg = make_test_config(vault_path=root / ".." / "kronos-vault")
        app_module._config = cfg
        app_module._graph = MagicMock()

        # Patch _grim_root to return our temp dir
        original_grim_root = app_module._grim_root
        app_module._grim_root = lambda: root

        client = TestClient(app_module.app)
        return client, root, original_grim_root

    def test_post_config_updates_model(self):
        """POST /api/config updates the model."""
        client, root, orig = self._make_client_with_config()
        try:
            import server.app as app_module
            resp = client.post(
                "/api/config",
                json={"model": "claude-opus-4-6"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["model"], "claude-opus-4-6")
        finally:
            import server.app as app_module
            app_module._grim_root = orig
            self._tmp.cleanup()

    def test_post_config_empty_updates(self):
        """POST /api/config with no updates returns 400."""
        client, root, orig = self._make_client_with_config()
        try:
            resp = client.post("/api/config", json={})
            self.assertEqual(resp.status_code, 400)
        finally:
            import server.app as app_module
            app_module._grim_root = orig
            self._tmp.cleanup()


# ═══════════════════════════════════════════════════════════════════════════
# 8. Skill manifests — memory-read, memory-update
# ═══════════════════════════════════════════════════════════════════════════

class TestMemorySkillManifests(unittest.TestCase):
    """Test that memory skill manifests are valid."""

    def _load_manifest(self, skill_name: str) -> dict:
        path = GRIM_ROOT / "skills" / skill_name / "manifest.yaml"
        self.assertTrue(path.exists(), f"Missing manifest: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_memory_read_manifest(self):
        """memory-read manifest is valid."""
        m = self._load_manifest("memory-read")
        self.assertEqual(m["name"], "memory-read")
        self.assertEqual(m["type"], "instruction-protocol")
        self.assertIn("memory:read", m["permissions"])
        self.assertIn("keywords", m["triggers"])
        self.assertTrue(len(m["triggers"]["keywords"]) >= 5)

    def test_memory_update_manifest(self):
        """memory-update manifest is valid."""
        m = self._load_manifest("memory-update")
        self.assertEqual(m["name"], "memory-update")
        self.assertEqual(m["type"], "instruction-protocol")
        self.assertIn("memory:write", m["permissions"])
        self.assertIn("keywords", m["triggers"])
        self.assertTrue(len(m["triggers"]["keywords"]) >= 5)

    def test_memory_read_protocol_exists(self):
        """memory-read protocol.md exists."""
        path = GRIM_ROOT / "skills" / "memory-read" / "protocol.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("read_grim_memory", content)

    def test_memory_update_protocol_exists(self):
        """memory-update protocol.md exists."""
        path = GRIM_ROOT / "skills" / "memory-update" / "protocol.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("update_grim_memory", content)

    def test_memory_read_keywords_useful(self):
        """memory-read keywords cover common queries."""
        m = self._load_manifest("memory-read")
        keywords = m["triggers"]["keywords"]
        keyword_set = set(keywords)
        self.assertIn("what do you remember", keyword_set)
        self.assertIn("current objectives", keyword_set)
        self.assertIn("working memory", keyword_set)

    def test_memory_update_keywords_useful(self):
        """memory-update keywords cover common requests."""
        m = self._load_manifest("memory-update")
        keywords = m["triggers"]["keywords"]
        keyword_set = set(keywords)
        self.assertIn("remember this", keyword_set)
        self.assertIn("don't forget", keyword_set)
        self.assertIn("update your memory", keyword_set)

    def test_memory_read_has_consumers(self):
        """memory-read declares memory-agent as consumer."""
        m = self._load_manifest("memory-read")
        self.assertIn("consumers", m)
        self.assertIn("memory-agent", m["consumers"])

    def test_memory_update_has_consumers(self):
        """memory-update declares memory-agent as consumer."""
        m = self._load_manifest("memory-update")
        self.assertIn("consumers", m)
        self.assertIn("memory-agent", m["consumers"])


# ═══════════════════════════════════════════════════════════════════════════
# 9. Memory agent — tool wiring
# ═══════════════════════════════════════════════════════════════════════════

class TestMemoryAgentWiring(unittest.TestCase):
    """Test memory agent has memory tools wired in."""

    def test_memory_agent_has_memory_tools(self):
        """MemoryAgent includes read_grim_memory and update_grim_memory."""
        from core.agents.memory_agent import MemoryAgent
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_test_config(vault_path=Path(tmp))
            agent = MemoryAgent(cfg)
            tool_names = {t.name for t in agent.tools}
            self.assertIn("read_grim_memory", tool_names)
            self.assertIn("update_grim_memory", tool_names)

    def test_memory_agent_has_kronos_tools(self):
        """MemoryAgent still has Kronos vault tools."""
        from core.agents.memory_agent import MemoryAgent
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_test_config(vault_path=Path(tmp))
            agent = MemoryAgent(cfg)
            tool_names = {t.name for t in agent.tools}
            # Should have both memory AND kronos tools
            self.assertTrue(len(tool_names) > 2, f"Expected more than 2 tools, got {tool_names}")

    def test_memory_agent_name(self):
        """MemoryAgent has correct agent_name."""
        from core.agents.memory_agent import MemoryAgent
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_test_config(vault_path=Path(tmp))
            agent = MemoryAgent(cfg)
            self.assertEqual(agent.agent_name, "memory")


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
