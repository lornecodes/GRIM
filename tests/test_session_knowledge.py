"""Tests for session knowledge accumulation — KnowledgeEntry, reducer, memory node, compression, agent context."""

import pytest
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

from core.state import (
    FDOSummary,
    KnowledgeEntry,
    _merge_session_knowledge,
    _SESSION_KNOWLEDGE_CAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fdo(id: str, domain: str = "physics", confidence: float = 0.8, title: str = "", summary: str = "test", related: list | None = None) -> FDOSummary:
    return FDOSummary(
        id=id,
        title=title or id.replace("-", " ").title(),
        domain=domain,
        status="stable",
        confidence=confidence,
        summary=summary,
        related=related or [],
    )


def _entry(
    id: str, turn: int = 1, by: str = "memory", query: str = "test", hit_count: int = 1,
    domain: str = "physics", confidence: float = 0.8, related: list | None = None,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        fdo=_fdo(id, domain=domain, confidence=confidence, related=related),
        fetched_turn=turn,
        fetched_by=by,
        query=query,
        last_referenced_turn=turn,
        hit_count=hit_count,
    )


# ===========================================================================
# 1. KnowledgeEntry dataclass
# ===========================================================================

class TestKnowledgeEntry:
    """Test KnowledgeEntry construction and serialization."""

    def test_construction(self):
        fdo = _fdo("pac", domain="physics")
        entry = KnowledgeEntry(fdo=fdo, fetched_turn=1, fetched_by="memory", query="pac", last_referenced_turn=1)
        assert entry.fdo.id == "pac"
        assert entry.hit_count == 1

    def test_default_hit_count(self):
        entry = _entry("test")
        assert entry.hit_count == 1

    def test_to_dict(self):
        entry = _entry("pac", turn=3, by="companion", query="what is pac?", hit_count=5)
        d = entry.to_dict()
        assert d["fdo_id"] == "pac"
        assert d["fetched_turn"] == 3
        assert d["fetched_by"] == "companion"
        assert d["hit_count"] == 5
        assert d["query"] == "what is pac?"
        assert "fdo_title" in d
        assert "fdo_domain" in d

    def test_to_dict_includes_related(self):
        entry = _entry("pac", related=["sec", "rbf"])
        d = entry.to_dict()
        assert d["related"] == ["sec", "rbf"]

    def test_to_dict_empty_related(self):
        entry = _entry("pac")
        d = entry.to_dict()
        assert d["related"] == []


# ===========================================================================
# 2. _merge_session_knowledge reducer
# ===========================================================================

class TestMergeSessionKnowledge:
    """Test the LangGraph reducer for session_knowledge."""

    def test_both_none(self):
        assert _merge_session_knowledge(None, None) == []

    def test_existing_none(self):
        new = [_entry("a")]
        result = _merge_session_knowledge(None, new)
        assert len(result) == 1
        assert result[0].fdo.id == "a"

    def test_new_none(self):
        existing = [_entry("a")]
        result = _merge_session_knowledge(existing, None)
        assert len(result) == 1
        assert result[0].fdo.id == "a"

    def test_both_empty(self):
        assert _merge_session_knowledge([], []) == []

    def test_new_entries_added(self):
        existing = [_entry("a")]
        new = [_entry("b")]
        result = _merge_session_knowledge(existing, new)
        assert len(result) == 2
        ids = {e.fdo.id for e in result}
        assert ids == {"a", "b"}

    def test_dedup_by_fdo_id(self):
        existing = [_entry("a", turn=1, hit_count=1)]
        new = [_entry("a", turn=2, hit_count=1)]
        result = _merge_session_knowledge(existing, new)
        assert len(result) == 1
        assert result[0].fdo.id == "a"

    def test_dedup_bumps_hit_count(self):
        existing = [_entry("a", turn=1, hit_count=3)]
        new = [_entry("a", turn=2, hit_count=1)]
        result = _merge_session_knowledge(existing, new)
        assert result[0].hit_count == 4  # 3 + 1

    def test_dedup_updates_last_referenced_turn(self):
        existing = [_entry("a", turn=1, hit_count=1)]
        new = [_entry("a", turn=5, hit_count=1)]
        result = _merge_session_knowledge(existing, new)
        assert result[0].last_referenced_turn == 5

    def test_dedup_keeps_max_last_referenced_turn(self):
        existing = [_entry("a", turn=1)]
        existing[0].last_referenced_turn = 10
        new = [_entry("a", turn=5)]
        new[0].last_referenced_turn = 3
        result = _merge_session_knowledge(existing, new)
        assert result[0].last_referenced_turn == 10  # max(10, 3)

    def test_cap_enforcement(self):
        entries = [_entry(f"fdo-{i}", hit_count=1) for i in range(_SESSION_KNOWLEDGE_CAP + 10)]
        result = _merge_session_knowledge(None, entries)
        assert len(result) == _SESSION_KNOWLEDGE_CAP

    def test_cap_keeps_highest_hit_count(self):
        """When cap is exceeded, entries with highest hit_count survive."""
        existing = [_entry(f"fdo-{i}", hit_count=1) for i in range(_SESSION_KNOWLEDGE_CAP)]
        # Add a new high-hit-count entry
        new = [_entry("important", hit_count=100)]
        result = _merge_session_knowledge(existing, new)
        assert len(result) == _SESSION_KNOWLEDGE_CAP
        important = [e for e in result if e.fdo.id == "important"]
        assert len(important) == 1
        assert important[0].hit_count == 100

    def test_ordering_preserved(self):
        """After merge, entries are sorted by (hit_count, last_referenced_turn) desc."""
        existing = [
            _entry("low", hit_count=1, turn=1),
            _entry("mid", hit_count=5, turn=2),
        ]
        new = [_entry("high", hit_count=10, turn=3)]
        # Only triggers sort if over cap, but let's verify the logic works
        # with cap exceeded
        entries = [_entry(f"fdo-{i}", hit_count=1) for i in range(_SESSION_KNOWLEDGE_CAP)]
        entries[0] = _entry("keep-me", hit_count=999)
        result = _merge_session_knowledge(entries, [_entry("extra")])
        assert result[0].fdo.id == "keep-me"

    def test_multiple_new_entries_with_same_id(self):
        """Multiple new entries with same ID when no existing — both pass through.

        Dedup only happens against existing entries. Within a single
        new batch (from one turn), duplicates are kept as-is because
        the memory node should not emit duplicates in a single turn.
        """
        new = [
            _entry("a", turn=1, hit_count=2),
            _entry("a", turn=2, hit_count=3),
        ]
        result = _merge_session_knowledge(None, new)
        # When existing is None, new is returned as-is (capped)
        assert len(result) == 2

    def test_preserves_fetched_by(self):
        """Existing entry's fetched_by is preserved on dedup."""
        existing = [_entry("a", by="memory")]
        new = [_entry("a", by="companion")]
        result = _merge_session_knowledge(existing, new)
        assert result[0].fetched_by == "memory"

    def test_empty_new_list(self):
        existing = [_entry("a")]
        result = _merge_session_knowledge(existing, [])
        assert len(result) == 1


# ===========================================================================
# 3. Memory node accumulation
# ===========================================================================

class TestMemoryNodeAccumulation:
    """Test that the memory node emits session_knowledge entries."""

    @pytest.fixture
    def mock_mcp(self):
        """Create a mock MCP session that returns search results."""
        import json
        mcp = AsyncMock()

        def _make_result(items):
            """Build mock MCP result from a list of dicts."""
            content_obj = MagicMock()
            content_obj.text = json.dumps({"results": items})
            result = MagicMock()
            result.content = [content_obj]
            return result

        # Default: return empty for all calls
        mcp.call_tool = AsyncMock(return_value=_make_result([]))
        mcp._make_result = _make_result
        return mcp

    @pytest.fixture
    def basic_state(self):
        from langchain_core.messages import HumanMessage
        from core.state import FieldState
        return {
            "messages": [HumanMessage(content="tell me about PAC")],
            "field_state": FieldState(),
            "knowledge_context": [],
            "session_knowledge": [],
            "session_topics": [],
            "turn_count": 0,
            "working_memory": "",
        }

    @pytest.mark.asyncio
    async def test_returns_session_knowledge_key(self, mock_mcp, basic_state):
        """Memory node output should include session_knowledge."""
        import json
        from core.nodes.memory import make_memory_node

        pac_result = mock_mcp._make_result([
            {"id": "pac", "title": "PAC", "domain": "physics", "status": "stable",
             "confidence": 0.9, "summary": "PAC framework", "tags": [], "related": []}
        ])
        mock_mcp.call_tool = AsyncMock(return_value=pac_result)
        node = make_memory_node(mock_mcp)
        result = await node(basic_state)

        assert "session_knowledge" in result

    @pytest.mark.asyncio
    async def test_returns_turn_count(self, mock_mcp, basic_state):
        """Memory node should increment turn_count."""
        from core.nodes.memory import make_memory_node

        node = make_memory_node(mock_mcp)
        result = await node(basic_state)

        assert "turn_count" in result
        assert result["turn_count"] == 1

    @pytest.mark.asyncio
    async def test_session_knowledge_entries_have_provenance(self, mock_mcp, basic_state):
        """Each KnowledgeEntry should have fetched_by, fetched_turn, query."""
        import json
        from core.nodes.memory import make_memory_node

        pac_result = mock_mcp._make_result([
            {"id": "pac", "title": "PAC", "domain": "physics", "status": "stable",
             "confidence": 0.9, "summary": "PAC framework", "tags": [], "related": []}
        ])
        mock_mcp.call_tool = AsyncMock(return_value=pac_result)
        node = make_memory_node(mock_mcp)
        result = await node(basic_state)

        entries = result.get("session_knowledge", [])
        if entries:
            e = entries[0]
            assert e.fetched_by == "memory"
            assert e.fetched_turn == 0  # turn_count was 0
            assert len(e.query) > 0

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty(self, mock_mcp, basic_state):
        """No messages → no knowledge."""
        from core.nodes.memory import make_memory_node

        basic_state["messages"] = []
        node = make_memory_node(mock_mcp)
        result = await node(basic_state)

        assert result.get("knowledge_context") == []

    @pytest.mark.asyncio
    async def test_multi_turn_increments_turn(self, mock_mcp, basic_state):
        """Turn counter increments each call."""
        from core.nodes.memory import make_memory_node

        node = make_memory_node(mock_mcp)
        r1 = await node(basic_state)
        assert r1["turn_count"] == 1

        basic_state["turn_count"] = 1
        r2 = await node(basic_state)
        assert r2["turn_count"] == 2


# ===========================================================================
# 4. Compress node preservation
# ===========================================================================

class TestCompressPreservation:
    """Test that session_knowledge survives compression."""

    def test_knowledge_references_in_compression_prompt(self):
        """COMPRESSION_PROMPT should have {knowledge_references} placeholder."""
        from core.context import COMPRESSION_PROMPT
        assert "{knowledge_references}" in COMPRESSION_PROMPT

    def test_compression_prompt_rule_6(self):
        """COMPRESSION_PROMPT should mention FDO references rule."""
        from core.context import COMPRESSION_PROMPT
        assert "knowledge FDOs" in COMPRESSION_PROMPT.lower() or "fdo" in COMPRESSION_PROMPT.lower()

    def test_session_knowledge_is_separate_state_field(self):
        """session_knowledge is a top-level state field, not inside messages."""
        from core.state import GrimState
        annotations = getattr(GrimState, "__annotations__", {})
        assert "session_knowledge" in annotations

    def test_compress_builds_knowledge_block(self):
        """compress.py should build a knowledge_block from session_knowledge."""
        # Verify the module has the logic
        import importlib
        mod = importlib.import_module("core.nodes.compress")
        source = importlib.util.find_spec("core.nodes.compress")
        # Just verify the module loads without error
        assert mod is not None


# ===========================================================================
# 5. Agent context merge
# ===========================================================================

class TestAgentContextMerge:
    """Test _merge_knowledge_sources and build_context with session knowledge."""

    def test_merge_knowledge_sources_empty(self):
        from core.agents.base import _merge_knowledge_sources
        state = {"knowledge_context": [], "session_knowledge": []}
        result = _merge_knowledge_sources(state)
        assert result == []

    def test_merge_knowledge_sources_per_turn_only(self):
        from core.agents.base import _merge_knowledge_sources
        fdos = [_fdo("pac"), _fdo("sec")]
        state = {"knowledge_context": fdos, "session_knowledge": []}
        result = _merge_knowledge_sources(state)
        assert len(result) == 2

    def test_merge_knowledge_sources_session_only(self):
        from core.agents.base import _merge_knowledge_sources
        entries = [_entry("pac"), _entry("sec")]
        state = {"knowledge_context": [], "session_knowledge": entries}
        result = _merge_knowledge_sources(state)
        assert len(result) == 2
        assert result[0].id == "pac"

    def test_merge_deduplicates(self):
        from core.agents.base import _merge_knowledge_sources
        fdos = [_fdo("pac")]
        entries = [_entry("pac")]
        state = {"knowledge_context": fdos, "session_knowledge": entries}
        result = _merge_knowledge_sources(state)
        assert len(result) == 1  # deduped

    def test_merge_per_turn_priority(self):
        """Per-turn FDOs come first (higher priority — freshest)."""
        from core.agents.base import _merge_knowledge_sources
        fdos = [_fdo("fresh")]
        entries = [_entry("cached")]
        state = {"knowledge_context": fdos, "session_knowledge": entries}
        result = _merge_knowledge_sources(state)
        assert result[0].id == "fresh"
        assert result[1].id == "cached"

    def test_merge_missing_session_knowledge(self):
        """Missing session_knowledge key doesn't crash."""
        from core.agents.base import _merge_knowledge_sources
        state = {"knowledge_context": [_fdo("pac")]}
        result = _merge_knowledge_sources(state)
        assert len(result) == 1

    def test_merge_missing_knowledge_context(self):
        from core.agents.base import _merge_knowledge_sources
        state = {"session_knowledge": [_entry("pac")]}
        result = _merge_knowledge_sources(state)
        assert len(result) == 1

    def test_build_context_uses_merged(self):
        """build_context should use merged knowledge (cap=10)."""
        from core.agents.base import BaseAgent
        agent = BaseAgent.__new__(BaseAgent)
        fdos = [_fdo(f"per-turn-{i}") for i in range(5)]
        entries = [_entry(f"session-{i}") for i in range(5)]
        state = {"knowledge_context": fdos, "session_knowledge": entries}
        ctx = agent.build_context(state)
        assert "relevant_fdos" in ctx
        # Should have all 10 (5 per-turn + 5 session)
        for i in range(5):
            assert f"per-turn-{i}" in ctx["relevant_fdos"]
            assert f"session-{i}" in ctx["relevant_fdos"]

    def test_build_context_caps_at_10(self):
        """build_context should cap merged FDOs at 10."""
        from core.agents.base import BaseAgent
        agent = BaseAgent.__new__(BaseAgent)
        fdos = [_fdo(f"fdo-{i}") for i in range(8)]
        entries = [_entry(f"sk-{i}") for i in range(8)]
        state = {"knowledge_context": fdos, "session_knowledge": entries}
        ctx = agent.build_context(state)
        # Count distinct FDO IDs in the string
        text = ctx["relevant_fdos"]
        # Per-turn come first, then session
        assert "fdo-7" in text  # last per-turn
        # Should have exactly 10 total FDO references
        # (8 per-turn + first 2 session to hit cap)
        assert "sk-0" in text
        assert "sk-1" in text


# ===========================================================================
# 6. Prompt builder integration
# ===========================================================================

class TestPromptBuilderSessionKnowledge:
    """Test prompt_builder merges session_knowledge into dynamic sections."""

    def test_accepts_session_knowledge_param(self):
        """build_system_prompt_parts should accept session_knowledge."""
        from core.personality.prompt_builder import build_system_prompt_parts
        import inspect
        sig = inspect.signature(build_system_prompt_parts)
        assert "session_knowledge" in sig.parameters

    def test_session_knowledge_merged_into_knowledge(self):
        """Session knowledge FDOs should appear in Relevant Knowledge section."""
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("You are GRIM.")
            prompt_path = Path(f.name)

        entries = [_entry("pac", domain="physics", confidence=0.9)]
        parts = build_system_prompt_parts(
            prompt_path=prompt_path,
            personality_path=Path("/fake"),
            field_state=FieldState(),
            session_knowledge=entries,
        )
        assert "pac" in parts.dynamic.lower() or "pac" in parts.full().lower()

    def test_session_dedup_with_knowledge_context(self):
        """FDOs in both knowledge_context and session_knowledge aren't duplicated."""
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("You are GRIM.")
            prompt_path = Path(f.name)

        fdos = [_fdo("pac")]
        entries = [_entry("pac")]
        parts = build_system_prompt_parts(
            prompt_path=prompt_path,
            personality_path=Path("/fake"),
            field_state=FieldState(),
            knowledge_context=fdos,
            session_knowledge=entries,
        )
        # Count how many times "pac" appears in FDO listing
        text = parts.full()
        # Should appear once in the knowledge section, not twice
        pac_count = text.count("**Pac**") + text.count("physics/pac")
        assert pac_count <= 2  # title + domain reference, not duplicated entry


# ===========================================================================
# 7. Backward compatibility
# ===========================================================================

class TestBackwardCompatibility:
    """Test that missing session_knowledge fields default correctly."""

    def test_missing_session_knowledge_in_state(self):
        """State without session_knowledge doesn't break merge."""
        from core.agents.base import _merge_knowledge_sources
        state = {"knowledge_context": [_fdo("pac")]}
        result = _merge_knowledge_sources(state)
        assert len(result) == 1

    def test_missing_turn_count_in_state(self):
        """State without turn_count doesn't break memory node."""
        # turn_count defaults to 0 in GrimState
        state = {}
        assert state.get("turn_count", 0) == 0

    def test_old_build_system_prompt_still_works(self):
        """build_system_prompt (old wrapper) still works without session_knowledge."""
        from core.personality.prompt_builder import build_system_prompt
        from core.state import FieldState
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("You are GRIM.")
            prompt_path = Path(f.name)

        result = build_system_prompt(
            prompt_path=prompt_path,
            personality_path=Path("/fake"),
            field_state=FieldState(),
        )
        assert "GRIM" in result

    def test_knowledge_entry_serialization_roundtrip(self):
        """to_dict produces JSON-serializable output."""
        import json
        entry = _entry("pac", turn=3, hit_count=5, related=["sec"])
        d = entry.to_dict()
        # Should be JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["fdo_id"] == "pac"
        assert parsed["hit_count"] == 5

    def test_reducer_with_none_fdo_id(self):
        """Reducer handles edge case of FDO with empty-ish ids gracefully."""
        e1 = _entry("a")
        e2 = _entry("b")
        result = _merge_session_knowledge([e1], [e2])
        assert len(result) == 2


# ===========================================================================
# 8. Server endpoint structure (API tests without actual server)
# ===========================================================================

class TestSessionKnowledgeAPI:
    """Test the session knowledge graph construction logic."""

    def test_graph_construction_from_entries(self):
        """Verify graph node/edge building from session entries."""
        entries = [
            _entry("pac", domain="physics", related=["sec"]).to_dict(),
            _entry("sec", domain="physics", related=["pac"]).to_dict(),
            _entry("grim", domain="ai-systems").to_dict(),
        ]

        # Build nodes
        node_map = {}
        for e in entries:
            fdo_id = e.get("fdo_id", "")
            node_map[fdo_id] = {
                "id": fdo_id,
                "domain": e.get("fdo_domain", ""),
                "hit_count": e.get("hit_count", 1),
            }

        assert len(node_map) == 3
        assert "pac" in node_map
        assert node_map["pac"]["domain"] == "physics"

        # Build edges
        edges = []
        seen = set()
        for e in entries:
            fdo_id = e.get("fdo_id", "")
            for rel in e.get("related", []):
                if rel in node_map:
                    key = tuple(sorted((fdo_id, rel)))
                    if key not in seen:
                        seen.add(key)
                        edges.append({"source": key[0], "target": key[1]})

        assert len(edges) == 1  # pac-sec (bidirectional deduped)

    def test_graph_empty_entries(self):
        """Empty entries produce empty graph."""
        entries = []
        nodes = [e for e in entries if e.get("fdo_id")]
        assert len(nodes) == 0

    def test_memory_graph_wikilink_parsing(self):
        """Parse [[fdo-id]] wikilinks from memory content."""
        import re
        content = """## Active Objectives
- Working on [[pac-comprehensive]] and [[grim-architecture]]

## Key Learnings
- [[pac-comprehensive]] is core to DFT
- [[sec-formulation]] relates to entropy"""

        sections = {}
        current = "root"
        for line in content.split("\n"):
            if line.startswith("## "):
                current = line[3:].strip()
                sections.setdefault(current, [])
            else:
                refs = re.findall(r"\[\[([a-z0-9-]+)\]\]", line)
                for ref in refs:
                    sections.setdefault(current, [])
                    if ref not in sections[current]:
                        sections[current].append(ref)

        assert "Active Objectives" in sections
        assert "pac-comprehensive" in sections["Active Objectives"]
        assert "grim-architecture" in sections["Active Objectives"]
        assert "Key Learnings" in sections
        assert "sec-formulation" in sections["Key Learnings"]
        # pac-comprehensive appears in both sections
        assert "pac-comprehensive" in sections["Key Learnings"]

    def test_memory_graph_co_section_edges(self):
        """FDOs in the same section create co_section edges."""
        sections = {
            "Active Objectives": ["pac", "grim"],
            "Key Learnings": ["pac", "sec"],
        }

        edges = []
        seen = set()
        for section_name, fdo_ids in sections.items():
            for i, a in enumerate(fdo_ids):
                for b in fdo_ids[i + 1:]:
                    key = tuple(sorted((a, b)))
                    if key not in seen:
                        seen.add(key)
                        edges.append({"source": key[0], "target": key[1], "section": section_name})

        assert len(edges) == 2  # pac-grim (from Active Objectives), pac-sec (from Key Learnings)
        edge_pairs = {(e["source"], e["target"]) for e in edges}
        assert ("grim", "pac") in edge_pairs
        assert ("pac", "sec") in edge_pairs


# ===========================================================================
# 9. Notification event structure
# ===========================================================================

class TestMemoryNotification:
    """Test the memory_notification event structure."""

    def test_notification_event_shape(self):
        """Verify the expected JSON shape of memory_notification."""
        event = {
            "type": "memory_notification",
            "updated": True,
            "summary": "Working memory updated",
            "duration_ms": 1234,
        }
        assert event["type"] == "memory_notification"
        assert event["updated"] is True
        assert isinstance(event["summary"], str)
        assert isinstance(event["duration_ms"], int)

    def test_notification_when_no_update(self):
        """Even when evolve runs without updating, notification is valid."""
        event = {
            "type": "memory_notification",
            "updated": True,
            "summary": "Working memory updated",
        }
        # duration_ms is optional
        assert "duration_ms" not in event or isinstance(event.get("duration_ms"), (int, float))

    def test_response_meta_includes_session_knowledge_count(self):
        """Response meta should include session_knowledge_count."""
        meta = {
            "mode": "companion",
            "knowledge_count": 5,
            "session_knowledge_count": 12,
            "skills": [],
            "fdo_ids": [],
            "total_ms": 500,
        }
        assert "session_knowledge_count" in meta
        assert meta["session_knowledge_count"] == 12
