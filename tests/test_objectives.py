"""Tests for persistent objectives — model, persistence, and integration.

All tests use mocked LLM and temp directories — no real API calls or disk state.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.objectives import Objective, load_objectives, save_objectives


# ─── Objective Model ────────────────────────────────────────────────────────


class TestObjectiveModel:

    def test_create_objective(self):
        obj = Objective(id="test-obj", description="A test objective")
        assert obj.id == "test-obj"
        assert obj.status == "active"
        assert obj.notes == []

    def test_to_dict(self):
        obj = Objective(
            id="test",
            description="desc",
            status="completed",
            created="2026-01-01",
            updated="2026-01-02",
            source_session="sess-1",
            notes=["note 1"],
        )
        d = obj.to_dict()
        assert d["id"] == "test"
        assert d["status"] == "completed"
        assert d["notes"] == ["note 1"]

    def test_from_dict(self):
        d = {
            "id": "test",
            "description": "desc",
            "status": "stalled",
            "created": "2026-01-01",
            "notes": ["n1", "n2"],
        }
        obj = Objective.from_dict(d)
        assert obj.id == "test"
        assert obj.status == "stalled"
        assert len(obj.notes) == 2

    def test_from_dict_defaults(self):
        obj = Objective.from_dict({})
        assert obj.id == ""
        assert obj.status == "active"
        assert obj.notes == []

    def test_roundtrip(self):
        original = Objective(
            id="roundtrip",
            description="Test roundtrip",
            status="active",
            created="2026-02-28T12:00:00",
            updated="2026-02-28T12:00:00",
            source_session="sess-abc",
            notes=["first note", "second note"],
        )
        restored = Objective.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.description == original.description
        assert restored.status == original.status
        assert restored.notes == original.notes


# ─── Persistence ────────────────────────────────────────────────────────────


class TestPersistence:

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            objectives = [
                Objective(id="obj-1", description="First"),
                Objective(id="obj-2", description="Second", status="completed"),
            ]
            save_objectives(objectives, path)
            loaded = load_objectives(path)
            assert len(loaded) == 2
            assert loaded[0].id == "obj-1"
            assert loaded[1].status == "completed"

    def test_load_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = load_objectives(Path(tmpdir))
            assert loaded == []

    def test_load_missing_directory(self):
        loaded = load_objectives(Path("/nonexistent/path/objectives"))
        assert loaded == []

    def test_save_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "deep" / "nested" / "objectives"
            objectives = [Objective(id="test", description="Test")]
            save_objectives(objectives, nested)
            assert (nested / "active.yaml").exists()

    def test_load_corrupt_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "active.yaml"
            path.write_text("{{invalid yaml: [", encoding="utf-8")
            loaded = load_objectives(Path(tmpdir))
            assert loaded == []

    def test_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            save_objectives([Objective(id="v1", description="V1")], path)
            save_objectives([Objective(id="v2", description="V2")], path)
            loaded = load_objectives(path)
            assert len(loaded) == 1
            assert loaded[0].id == "v2"


# ─── Identity Node Integration ─────────────────────────────────────────────


class TestIdentityLoadsObjectives:

    @pytest.mark.asyncio
    async def test_identity_node_loads_objectives(self):
        """Identity node should load objectives from disk."""
        from core.config import GrimConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            obj_path = Path(tmpdir) / "objectives"
            objectives = [Objective(id="test", description="Test obj")]
            save_objectives(objectives, obj_path)

            config = GrimConfig()
            config.objectives_path = obj_path
            config.identity_prompt_path = Path("/nonexistent")
            config.identity_personality_path = Path("/nonexistent")
            config.personality_cache_path = Path("/nonexistent")

            from core.nodes.identity import make_identity_node

            node = make_identity_node(config, mcp_session=None)
            state = {"caller_id": "peter"}
            result = await node(state)

            assert "objectives" in result
            assert len(result["objectives"]) == 1
            assert result["objectives"][0].id == "test"


# ─── Prompt Builder Integration ─────────────────────────────────────────────


class TestPromptBuilderObjectives:

    def test_objectives_in_dynamic_section(self):
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState

        objectives = [
            Objective(id="caching", description="Add two-layer caching"),
            Objective(id="stalled-obj", description="Old task", status="stalled"),
        ]

        parts = build_system_prompt_parts(
            prompt_path=Path("/nonexistent"),
            personality_path=Path("/nonexistent"),
            field_state=FieldState(),
            objectives=objectives,
        )

        assert "caching" in parts.dynamic
        assert "Add two-layer caching" in parts.dynamic
        # Stalled objectives should not appear
        assert "stalled-obj" not in parts.dynamic
        # Should not be in static
        assert "caching" not in parts.static

    def test_no_objectives_empty_section(self):
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState

        parts = build_system_prompt_parts(
            prompt_path=Path("/nonexistent"),
            personality_path=Path("/nonexistent"),
            field_state=FieldState(),
            objectives=[],
        )

        assert "Active Objectives" not in parts.dynamic

    def test_objectives_with_notes(self):
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState

        objectives = [
            Objective(
                id="test",
                description="Test objective",
                notes=["First note", "Latest note"],
            ),
        ]

        parts = build_system_prompt_parts(
            prompt_path=Path("/nonexistent"),
            personality_path=Path("/nonexistent"),
            field_state=FieldState(),
            objectives=objectives,
        )

        assert "Latest note" in parts.dynamic


# ─── Evolve Node Objective Extraction ───────────────────────────────────────


class TestEvolveObjectives:

    @pytest.mark.asyncio
    async def test_extraction_saves_objectives(self):
        """Evolve should extract and save objectives on interval."""
        from langchain_core.messages import AIMessage, HumanMessage

        from core.config import GrimConfig
        from core.nodes.evolve import _OBJECTIVE_EXTRACT_INTERVAL, make_evolve_node
        from core.state import FieldState

        with tempfile.TemporaryDirectory() as tmpdir:
            config = GrimConfig()
            config.evolution_dir = Path(tmpdir) / "evolution"
            config.objectives_path = Path(tmpdir) / "objectives"

            mock_response = MagicMock()
            mock_response.content = json.dumps({
                "objectives": [
                    {"id": "new-obj", "description": "New objective", "status": "active", "notes": []}
                ]
            })

            with patch("langchain_anthropic.ChatAnthropic") as MockLLM:
                mock_llm = AsyncMock()
                mock_llm.ainvoke.return_value = mock_response
                MockLLM.return_value = mock_llm

                node = make_evolve_node(config)

                # Run enough turns to trigger extraction
                msgs = [HumanMessage(content=f"msg {i}") for i in range(6)]
                state = {
                    "field_state": FieldState(),
                    "session_topics": [],
                    "knowledge_context": [],
                    "messages": msgs,
                    "objectives": [],
                    "session_start": datetime.now(),
                }

                # Run _OBJECTIVE_EXTRACT_INTERVAL times to trigger
                for i in range(_OBJECTIVE_EXTRACT_INTERVAL):
                    await node(state)

                # Check objectives were saved
                loaded = load_objectives(config.objectives_path)
                assert len(loaded) == 1
                assert loaded[0].id == "new-obj"

    @pytest.mark.asyncio
    async def test_extraction_failure_doesnt_block(self):
        """LLM failure should not prevent evolve from completing."""
        from core.config import GrimConfig
        from core.nodes.evolve import _OBJECTIVE_EXTRACT_INTERVAL, make_evolve_node
        from core.state import FieldState

        with tempfile.TemporaryDirectory() as tmpdir:
            config = GrimConfig()
            config.evolution_dir = Path(tmpdir) / "evolution"
            config.objectives_path = Path(tmpdir) / "objectives"

            with patch("langchain_anthropic.ChatAnthropic") as MockLLM:
                mock_llm = AsyncMock()
                mock_llm.ainvoke.side_effect = RuntimeError("LLM down")
                MockLLM.return_value = mock_llm

                node = make_evolve_node(config)
                state = {
                    "field_state": FieldState(),
                    "session_topics": [],
                    "knowledge_context": [],
                    "messages": [MagicMock(content=f"msg {i}") for i in range(6)],
                    "objectives": [],
                    "session_start": datetime.now(),
                }

                # Should not raise
                for i in range(_OBJECTIVE_EXTRACT_INTERVAL):
                    result = await node(state)

                assert "field_state" in result
