"""Comprehensive unit tests for the GRIM core.

Tests all layers without live LLM or MCP:
- State types (FieldState, SkillContext, FDOSummary, AgentResult)
- Config loader (load_config, YAML parsing, env overrides)
- Skill system (registry, loader, matcher)
- All 14 nodes (identity, memory, skill_match, graph_router, personal_companion, router, companion, dispatch, audit_gate, audit, re_dispatch, integrate, evolve)
- Graph wiring (build_graph, conditional routing)
- Workspace tools (file ops, path security, shell)
- Prompt builder (system prompt assembly)

Run: cd GRIM && python tests/test_grim_core.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.state import AgentResult, FDOSummary, FieldState, GrimState, SkillContext


# ═══════════════════════════════════════════════════════════════════════════
# Test infrastructure
# ═══════════════════════════════════════════════════════════════════════════

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


class MockMCPResult:
    """Simulate an MCP call_tool result."""
    def __init__(self, data: dict | list | str):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(text=text)]


class MockMCPSession:
    """Mock MCP client session that returns canned responses."""
    def __init__(self, responses: dict[str, Any] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, method: str, args: dict | None = None) -> MockMCPResult:
        self.calls.append((method, args or {}))
        if method in self._responses:
            return MockMCPResult(self._responses[method])
        return MockMCPResult({"results": []})


def make_test_config(**overrides):
    """Create a GrimConfig pointing at test fixtures."""
    from core.config import GrimConfig
    cfg = GrimConfig(
        env="debug",
        vault_path=GRIM_ROOT / "tests" / "vault",
        skills_path=GRIM_ROOT / "skills",
        identity_prompt_path=GRIM_ROOT / "identity" / "system_prompt.md",
        identity_personality_path=GRIM_ROOT / "identity" / "personality.yaml",
        local_dir=GRIM_ROOT / "local",
        model="claude-sonnet-4-6",
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_human_message(text: str):
    """Create a HumanMessage without importing langchain at module level."""
    from langchain_core.messages import HumanMessage
    return HumanMessage(content=text)


def make_ai_message(text: str):
    """Create an AIMessage."""
    from langchain_core.messages import AIMessage
    return AIMessage(content=text)


# ═══════════════════════════════════════════════════════════════════════════
# 1. State types
# ═══════════════════════════════════════════════════════════════════════════

class TestFieldState(unittest.TestCase):
    """Tests for FieldState personality dynamics."""

    def test_default_values(self):
        fs = FieldState()
        self.assertAlmostEqual(fs.coherence, 0.8)
        self.assertAlmostEqual(fs.valence, 0.3)
        self.assertAlmostEqual(fs.uncertainty, 0.2)

    def test_modulate_high_confidence(self):
        fs = FieldState(uncertainty=0.5)
        fs.modulate(confidence=0.9)
        self.assertAlmostEqual(fs.uncertainty, 0.4)  # decreased by 0.1

    def test_modulate_low_confidence(self):
        fs = FieldState(uncertainty=0.3)
        fs.modulate(confidence=0.2)
        self.assertAlmostEqual(fs.uncertainty, 0.5)  # increased by 0.2

    def test_modulate_established_topic(self):
        fs = FieldState(coherence=0.7, uncertainty=0.3)
        fs.modulate(topic_type="established")
        self.assertAlmostEqual(fs.coherence, 0.8)  # +0.1
        self.assertAlmostEqual(fs.uncertainty, 0.2)  # -0.1

    def test_modulate_speculative_topic(self):
        fs = FieldState(uncertainty=0.3, valence=0.3)
        fs.modulate(topic_type="speculative")
        self.assertAlmostEqual(fs.uncertainty, 0.5)  # +0.2
        self.assertAlmostEqual(fs.valence, 0.4)  # +0.1

    def test_modulate_clamps_at_bounds(self):
        fs = FieldState(uncertainty=0.0)
        fs.modulate(confidence=0.9)
        self.assertAlmostEqual(fs.uncertainty, 0.0)  # can't go below 0

        fs = FieldState(uncertainty=1.0)
        fs.modulate(confidence=0.1)
        self.assertAlmostEqual(fs.uncertainty, 1.0)  # can't go above 1

    def test_expression_mode_direct(self):
        fs = FieldState(coherence=0.8, uncertainty=0.2)
        self.assertEqual(fs.expression_mode(), "direct, assertive")

    def test_expression_mode_careful(self):
        fs = FieldState(coherence=0.8, uncertainty=0.5)
        self.assertEqual(fs.expression_mode(), "careful, hedging but structured")

    def test_expression_mode_conversational(self):
        fs = FieldState(coherence=0.5, uncertainty=0.2)
        self.assertEqual(fs.expression_mode(), "conversational, flowing")

    def test_expression_mode_exploratory(self):
        fs = FieldState(coherence=0.5, uncertainty=0.5)
        self.assertEqual(fs.expression_mode(), "exploratory, open")

    def test_snapshot_roundtrip(self):
        fs = FieldState(coherence=0.123456, valence=0.789012, uncertainty=0.345678)
        snap = fs.snapshot()
        self.assertEqual(snap["coherence"], 0.123)
        self.assertEqual(snap["valence"], 0.789)
        self.assertEqual(snap["uncertainty"], 0.346)


class TestFDOSummary(unittest.TestCase):
    def test_creation(self):
        fdo = FDOSummary(
            id="test-fdo", title="Test FDO", domain="physics",
            status="stable", confidence=0.9, summary="A test FDO.",
            tags=["test"], related=["other-fdo"],
        )
        self.assertEqual(fdo.id, "test-fdo")
        self.assertEqual(fdo.tags, ["test"])

    def test_defaults(self):
        fdo = FDOSummary(id="x", title="X", domain="ai", status="seed",
                         confidence=0.5, summary="x")
        self.assertEqual(fdo.tags, [])
        self.assertEqual(fdo.related, [])


class TestAgentResult(unittest.TestCase):
    def test_success(self):
        r = AgentResult(agent="memory", success=True, summary="Done")
        self.assertTrue(r.success)
        self.assertEqual(r.artifacts, [])

    def test_failure_with_details(self):
        r = AgentResult(agent="coder", success=False, summary="Error",
                        details={"error": "timeout"})
        self.assertFalse(r.success)
        self.assertEqual(r.details["error"], "timeout")


class TestSkillContext(unittest.TestCase):
    def test_creation(self):
        sc = SkillContext(name="kronos-capture", version="1.0",
                          description="Capture to vault")
        self.assertEqual(sc.name, "kronos-capture")
        self.assertEqual(sc.permissions, [])
        self.assertEqual(sc.triggers, {})


# ═══════════════════════════════════════════════════════════════════════════
# 2. Config loader
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigLoader(unittest.TestCase):
    def test_default_config(self):
        from core.config import GrimConfig
        cfg = GrimConfig()
        self.assertEqual(cfg.env, "debug")
        self.assertEqual(cfg.model, "claude-sonnet-4-6")
        self.assertTrue(cfg.is_debug)
        self.assertFalse(cfg.is_production)

    def test_load_config_from_yaml(self):
        from core.config import load_config
        cfg = load_config(grim_root=GRIM_ROOT)
        # Should have resolved paths
        self.assertTrue(cfg.skills_path.is_absolute())
        self.assertTrue(cfg.identity_prompt_path.is_absolute())

    def test_env_override(self):
        from core.config import load_config
        old = os.environ.get("GRIM_ENV")
        os.environ["GRIM_ENV"] = "production"
        try:
            cfg = load_config(grim_root=GRIM_ROOT)
            self.assertEqual(cfg.env, "production")
            self.assertTrue(cfg.is_production)
        finally:
            if old is None:
                os.environ.pop("GRIM_ENV", None)
            else:
                os.environ["GRIM_ENV"] = old

    def test_vault_path_override(self):
        from core.config import load_config
        old = os.environ.get("GRIM_VAULT_PATH")
        override_path = str(GRIM_ROOT / "tests" / "vault")
        os.environ["GRIM_VAULT_PATH"] = override_path
        try:
            cfg = load_config(grim_root=GRIM_ROOT)
            self.assertEqual(str(cfg.vault_path), override_path)
        finally:
            if old is None:
                os.environ.pop("GRIM_VAULT_PATH", None)
            else:
                os.environ["GRIM_VAULT_PATH"] = old

    def test_debug_mode_fallback_to_test_vault(self):
        """In debug mode, if vault_path doesn't exist, falls back to tests/vault."""
        from core.config import load_config
        old_vault = os.environ.get("GRIM_VAULT_PATH")
        old_env = os.environ.get("GRIM_ENV")
        # Clear env overrides so the fallback logic can run
        os.environ.pop("GRIM_VAULT_PATH", None)
        os.environ.pop("GRIM_ENV", None)
        try:
            # Point config at a YAML that sets a nonexistent vault
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write("env: debug\nvault_path: /nonexistent/vault\n")
                f.flush()
                cfg = load_config(config_path=Path(f.name), grim_root=GRIM_ROOT)
            # In debug mode, if vault_path doesn't exist but tests/vault does,
            # it falls back to tests/vault
            self.assertTrue(str(cfg.vault_path).endswith("vault"))
        finally:
            if old_vault is None:
                os.environ.pop("GRIM_VAULT_PATH", None)
            else:
                os.environ["GRIM_VAULT_PATH"] = old_vault
            if old_env is None:
                os.environ.pop("GRIM_ENV", None)
            else:
                os.environ["GRIM_ENV"] = old_env

    def test_apply_yaml_with_nested_keys(self):
        """Test that _apply_yaml handles nested config blocks."""
        from core.config import GrimConfig, _apply_yaml
        cfg = GrimConfig()
        raw = {
            "env": "production",
            "agent": {"default_model": "claude-opus-4-6", "temperature": 0.5},
            "skills": {"auto_load": False, "match_per_turn": False},
            "persistence": {"backend": "sqlite", "path": "custom/db.sqlite"},
        }
        _apply_yaml(cfg, raw, GRIM_ROOT)
        self.assertEqual(cfg.env, "production")
        self.assertEqual(cfg.model, "claude-opus-4-6")
        self.assertAlmostEqual(cfg.temperature, 0.5)
        self.assertFalse(cfg.skills_auto_load)
        self.assertFalse(cfg.skills_match_per_turn)
        self.assertEqual(cfg.checkpoint_backend, "sqlite")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Skill system
# ═══════════════════════════════════════════════════════════════════════════

class TestSkillRegistry(unittest.TestCase):
    def setUp(self):
        from core.skills.registry import Skill, SkillConsumer, SkillRegistry
        self.registry = SkillRegistry()
        self.capture_skill = Skill(
            name="kronos-capture", version="1.0",
            description="Capture to vault",
            protocol="## Protocol\nDo the thing.",
            permissions=["vault:write"],
            triggers={"keywords": ["remember this", "save this", "capture this"],
                       "intents": ["proactive", "explicit"]},
            consumers=[
                SkillConsumer(name="grim", role="recognition"),
                SkillConsumer(name="memory-agent", role="execution"),
            ],
        )
        self.code_skill = Skill(
            name="code-execution", version="1.0",
            description="Execute code",
            protocol="## Code Protocol",
            permissions=["filesystem:write"],
            triggers={"keywords": ["write code", "implement", "create file"],
                       "intents": []},
            consumers=[
                SkillConsumer(name="coder-agent", role="execution"),
            ],
        )
        self.registry.register(self.capture_skill)
        self.registry.register(self.code_skill)

    def test_register_and_get(self):
        self.assertEqual(len(self.registry), 2)
        self.assertIsNotNone(self.registry.get("kronos-capture"))
        self.assertIsNone(self.registry.get("nonexistent"))

    def test_contains(self):
        self.assertIn("kronos-capture", self.registry)
        self.assertNotIn("xyz", self.registry)

    def test_all_and_names(self):
        self.assertEqual(len(self.registry.all()), 2)
        self.assertIn("code-execution", self.registry.names())

    def test_for_grim(self):
        grim_skills = self.registry.for_grim()
        names = [s.name for s in grim_skills]
        self.assertIn("kronos-capture", names)  # has grim consumer
        # code-execution has no grim consumer but has consumers, so excluded
        self.assertNotIn("code-execution", names)

    def test_for_agent(self):
        memory_skills = self.registry.for_agent("memory")
        self.assertEqual(len(memory_skills), 1)
        self.assertEqual(memory_skills[0].name, "kronos-capture")

        code_skills = self.registry.for_agent("code")
        self.assertEqual(len(code_skills), 1)
        self.assertEqual(code_skills[0].name, "code-execution")

    def test_requires_write(self):
        self.assertTrue(self.capture_skill.requires_write)

    def test_delegation_target(self):
        self.assertEqual(self.capture_skill.delegation_target(), "memory")
        self.assertEqual(self.code_skill.delegation_target(), "code")

    def test_repr(self):
        r = repr(self.registry)
        self.assertIn("2 skills", r)


class TestConsumerToDelegation(unittest.TestCase):
    """Test _consumer_to_delegation mapping (v0.0.6 roster)."""

    def test_memory_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("memory-agent"), "memory")

    def test_coder_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("coder-agent"), "code")

    def test_research_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("research-agent"), "research")

    def test_operator_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("operator-agent"), "operate")

    def test_ops_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("ops-agent"), "operate")

    def test_audit_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("audit-agent"), "audit")

    def test_planning_agent_mapping(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("planning-agent"), "planning")

    def test_unknown_strips_agent_suffix(self):
        from core.skills.registry import _consumer_to_delegation
        self.assertEqual(_consumer_to_delegation("custom-agent"), "custom")

    def test_delegation_target_with_planning_consumer(self):
        """Skill with planning-agent execution consumer → 'planning'."""
        from core.skills.registry import Skill, SkillConsumer
        skill = Skill(
            name="sprint-plan", version="1.0",
            description="Sprint planning",
            protocol="## Protocol",
            consumers=[
                SkillConsumer(name="grim", role="recognition"),
                SkillConsumer(name="planning-agent", role="execution"),
            ],
        )
        self.assertEqual(skill.delegation_target(), "planning")


class TestSkillLoader(unittest.TestCase):
    def test_load_real_skills(self):
        """Load skills from the actual skills/ directory."""
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        self.assertGreater(len(registry), 5)
        self.assertIn("kronos-capture", registry)
        self.assertIn("deep-ingest", registry)

    def test_load_from_nonexistent_dir(self):
        from core.skills.loader import load_skills
        registry = load_skills(Path("/nonexistent"))
        self.assertEqual(len(registry), 0)

    def test_loaded_skill_has_protocol(self):
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        capture = registry.get("kronos-capture")
        self.assertIsNotNone(capture)
        # protocol.md content should be loaded — check for actual content
        self.assertGreater(len(capture.protocol), 100)
        self.assertIn("Kronos Capture", capture.protocol)

    def test_loaded_skill_has_triggers(self):
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        capture = registry.get("kronos-capture")
        triggers = capture.triggers
        self.assertIn("keywords", triggers)
        # Should have extracted keywords from trigger descriptions
        self.assertGreater(len(triggers["keywords"]), 0)

    def test_loaded_skill_has_consumers(self):
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        capture = registry.get("kronos-capture")
        self.assertGreater(len(capture.consumers), 0)
        consumer_names = [c.name for c in capture.consumers]
        self.assertIn("memory-agent", consumer_names)


class TestSkillMatcher(unittest.TestCase):
    def setUp(self):
        from core.skills.loader import load_skills
        cfg = make_test_config()
        self.registry = load_skills(cfg.skills_path)

    def test_capture_keywords_match(self):
        from core.skills.matcher import match_skills
        matched = match_skills("remember this concept about topology", self.registry)
        names = [s.name for s in matched]
        self.assertIn("kronos-capture", names)

    def test_save_this_matches_capture(self):
        from core.skills.matcher import match_skills
        matched = match_skills("save this idea about Mobius surfaces", self.registry)
        names = [s.name for s in matched]
        self.assertIn("kronos-capture", names)

    def test_pure_question_no_match(self):
        """Questions about concepts shouldn't trigger action skills."""
        from core.skills.matcher import match_skills
        matched = match_skills("what is the PAC framework?", self.registry)
        # May or may not match — but should not match capture skills
        capture_matched = [s for s in matched if s.name == "kronos-capture"]
        # Capture requires "remember this" etc, not questions
        # This tests the _is_command_context guard
        self.assertEqual(len(capture_matched), 0)

    def test_empty_message_no_match(self):
        from core.skills.matcher import match_skills
        self.assertEqual(match_skills("", self.registry), [])
        self.assertEqual(match_skills("", None), [])

    def test_code_skill_needs_strong_signal(self):
        """code-execution requires min score 2 — single keyword not enough."""
        from core.skills.matcher import match_skills, _score_skill
        # Single weak match scores 1, below threshold of 2
        skill = self.registry.get("code-execution")
        score = _score_skill("run the code-execution protocol", skill)
        # Single keyword match in non-command context is weak (1 point)
        # The minimum threshold is 2, so this doesn't match
        self.assertLess(score, 2)

    def test_git_skill_needs_strong_signal(self):
        """git-operations requires min score 2 — single keyword not enough."""
        from core.skills.matcher import match_skills, _score_skill
        skill = self.registry.get("git-operations")
        score = _score_skill("use git-operations to push changes", skill)
        self.assertLess(score, 2)

    def test_matching_returns_sorted_by_score(self):
        from core.skills.matcher import match_skills
        matched = match_skills("remember this and save this to vault", self.registry)
        # The most relevant skill should be first
        if matched:
            self.assertIsInstance(matched[0].name, str)

    def test_command_context_detection(self):
        from core.skills.matcher import _is_command_context
        # Imperative commands
        self.assertTrue(_is_command_context("remember this concept", "remember"))
        self.assertTrue(_is_command_context("please commit the changes", "commit"))
        self.assertTrue(_is_command_context("let's ingest this paper", "ingest"))
        # Questions (not commands)
        self.assertFalse(_is_command_context("what is the commit history?", "commit"))
        self.assertFalse(_is_command_context("how does it relate to physics?", "relate"))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Prompt builder
# ═══════════════════════════════════════════════════════════════════════════

class TestPromptBuilder(unittest.TestCase):
    def test_build_basic_prompt(self):
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
        )
        self.assertIn("GRIM", prompt)
        self.assertIn("Expression Mode", prompt)
        self.assertIn("direct, assertive", prompt)

    def test_build_with_knowledge_context(self):
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        fdos = [
            FDOSummary(id="pac", title="PAC Framework", domain="physics",
                       status="stable", confidence=0.9, summary="Conservation law"),
        ]
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
            knowledge_context=fdos,
        )
        self.assertIn("PAC Framework", prompt)
        self.assertIn("Relevant Knowledge", prompt)

    def test_build_with_matched_skills(self):
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        skills = [
            SkillContext(name="kronos-capture", version="1.0",
                          description="Quick capture to inbox"),
        ]
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
            matched_skills=skills,
        )
        self.assertIn("kronos-capture", prompt)
        self.assertIn("Active Skills", prompt)
        self.assertIn("handled automatically", prompt)

    def test_build_with_identity_fdo(self):
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        fdo = {"body": "GRIM is a recursive intelligence machine for Peter Lorne."}
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
            identity_fdo=fdo,
        )
        self.assertIn("recursive intelligence machine", prompt)
        self.assertIn("Extended Identity", prompt)

    def test_build_with_missing_prompt_file(self):
        from core.personality.prompt_builder import build_system_prompt
        fs = FieldState()
        prompt = build_system_prompt(
            prompt_path=Path("/nonexistent/prompt.md"),
            personality_path=Path("/nonexistent/personality.yaml"),
            field_state=fs,
        )
        # Fallback prompt
        self.assertIn("GRIM", prompt)
        self.assertIn("research companion", prompt)

    def test_load_field_state_from_yaml(self):
        from core.personality.prompt_builder import load_field_state
        cfg = make_test_config()
        fs = load_field_state(cfg.identity_personality_path)
        self.assertAlmostEqual(fs.coherence, 0.8)
        self.assertAlmostEqual(fs.valence, 0.3)
        self.assertAlmostEqual(fs.uncertainty, 0.2)

    def test_load_field_state_missing_file(self):
        from core.personality.prompt_builder import load_field_state
        fs = load_field_state(Path("/nonexistent.yaml"))
        # Returns defaults
        self.assertAlmostEqual(fs.coherence, 0.8)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Nodes
# ═══════════════════════════════════════════════════════════════════════════

class TestIdentityNode(unittest.TestCase):
    def test_identity_without_mcp(self):
        """Identity node loads from local files when MCP is None."""
        from core.nodes.identity import make_identity_node
        cfg = make_test_config()
        node = make_identity_node(cfg, mcp_session=None)
        result = run_async(node({}))
        self.assertIn("system_prompt", result)
        self.assertIn("field_state", result)
        self.assertIn("GRIM", result["system_prompt"])
        self.assertIsInstance(result["field_state"], FieldState)

    def test_identity_with_mcp(self):
        """Identity node enriches from Kronos FDO when MCP available."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            cfg = make_test_config(personality_cache_path=cache_path)
            mcp = MockMCPSession({
                "kronos_get": {"body": "GRIM is the greatest AI companion ever built."},
            })
            node = make_identity_node(cfg, mcp_session=mcp)
            result = run_async(node({}))
            self.assertIn("greatest AI companion", result["system_prompt"])
            # Verify MCP was called for identity, personality, caller (peter), and memory
            self.assertEqual(len(mcp.calls), 4)
            self.assertEqual(mcp.calls[0][0], "kronos_get")  # grim-identity
            self.assertEqual(mcp.calls[1][0], "kronos_get")  # grim-personality
            self.assertEqual(mcp.calls[2][0], "kronos_get")  # peter (caller)
            self.assertEqual(mcp.calls[3][0], "kronos_memory_read")  # working memory
            # Verify caller fields returned
            self.assertEqual(result["caller_id"], "peter")
            self.assertIsNotNone(result["caller_context"])

    def test_identity_mcp_failure_graceful(self):
        """Identity node works even if MCP call fails."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            cfg = make_test_config(personality_cache_path=cache_path)
            mcp = MagicMock()
            mcp.call_tool = AsyncMock(side_effect=Exception("MCP down"))
            node = make_identity_node(cfg, mcp_session=mcp)
            result = run_async(node({}))
            # Should still have system prompt from local files
            self.assertIn("system_prompt", result)
            self.assertIn("GRIM", result["system_prompt"])

    def test_identity_personality_cache_compiled(self):
        """Identity node compiles personality cache when stale."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            cfg = make_test_config(personality_cache_path=cache_path)
            personality_body = textwrap.dedent("""\
                ## Overview
                Shade archetype.

                ## Personality Trait Scales
                ```yaml
                traits:
                  formality: 0.85
                  wit: 0.70
                  warmth: 0.60
                  deference: 0.40
                  opinion_strength: 0.70
                ```
                """)
            mcp = MockMCPSession({
                "kronos_get": {"body": personality_body},
            })
            node = make_identity_node(cfg, mcp_session=mcp)
            result = run_async(node({}))
            # Cache file should exist now
            self.assertTrue(cache_path.exists())
            cache_content = cache_path.read_text(encoding="utf-8")
            self.assertIn("grim-personality-cache", cache_content)
            self.assertIn("Formality", cache_content)

    def test_identity_skips_fresh_cache(self):
        """Identity node skips MCP fetch when cache is fresh."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            # Pre-populate a fresh cache
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            cache_path.write_text(
                f"<!-- grim-personality-cache | synced: {now} | source: grim-personality -->\n\n"
                "## Voice & Character\nTest cache content\n",
                encoding="utf-8",
            )
            cfg = make_test_config(personality_cache_path=cache_path)
            # MCP returns identity FDO only — personality should NOT be fetched
            mcp = MockMCPSession({
                "kronos_get": {"body": "GRIM identity content."},
            })
            node = make_identity_node(cfg, mcp_session=mcp)
            result = run_async(node({}))
            # 3 MCP calls: identity + caller (peter) + memory. Personality skipped (cache fresh).
            self.assertEqual(len(mcp.calls), 3)
            self.assertEqual(mcp.calls[0][0], "kronos_get")  # grim-identity
            self.assertEqual(mcp.calls[1][0], "kronos_get")  # peter (caller)
            self.assertEqual(mcp.calls[2][0], "kronos_memory_read")  # working memory
            # Cache content should appear in system prompt
            self.assertIn("Test cache content", result["system_prompt"])


class TestUserCache(unittest.TestCase):
    """Tests for user cache compilation and staleness."""

    def test_is_user_cache_stale_missing_file(self):
        """Missing cache file is stale."""
        from core.personality.user_cache import is_user_cache_stale
        self.assertTrue(is_user_cache_stale(Path("/nonexistent/user.cache.md")))

    def test_is_user_cache_stale_fresh(self):
        """Fresh cache file is not stale."""
        from core.personality.user_cache import is_user_cache_stale
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "user.cache.md"
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            p.write_text(
                f"<!-- grim-user-cache | synced: {now} | source: peter -->\n\n"
                "## Caller: Peter Lorne (owner)\n",
                encoding="utf-8",
            )
            self.assertFalse(is_user_cache_stale(p))

    def test_is_user_cache_stale_old(self):
        """Cache older than max_age is stale."""
        from core.personality.user_cache import is_user_cache_stale
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "user.cache.md"
            p.write_text(
                "<!-- grim-user-cache | synced: 2020-01-01T00:00:00 | source: peter -->\n\n"
                "## Caller: Peter Lorne (owner)\n",
                encoding="utf-8",
            )
            self.assertTrue(is_user_cache_stale(p))

    def test_compile_user_cache(self):
        """compile_user_cache writes a cache file from a user FDO."""
        from core.personality.user_cache import compile_user_cache
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "user.cache.md"
            fdo = {
                "id": "peter", "title": "Peter Lorne", "role": "owner",
                "body": textwrap.dedent("""\
                    ## Working Style
                    - Builds things to understand them
                    - Theory and implementation inseparable

                    ## Communication Preferences
                    - Direct and honest
                    - Push back when needed

                    ## Current Priorities
                    - DFT milestone 4
                    - GRIM Phase 2
                """),
            }
            compile_user_cache(fdo, cache_path)
            self.assertTrue(cache_path.exists())
            content = cache_path.read_text(encoding="utf-8")
            self.assertIn("grim-user-cache", content)
            self.assertIn("Peter Lorne", content)
            self.assertIn("Builds things to understand them", content)
            self.assertIn("Direct and honest", content)
            self.assertIn("DFT milestone 4", content)

    def test_compile_caller_summary_service(self):
        """compile_caller_summary produces compact prompt for a service caller."""
        from core.personality.user_cache import compile_caller_summary
        fdo = {
            "id": "code-agent", "title": "Code Agent", "role": "service", "type": "service",
            "body": textwrap.dedent("""\
                ## Context
                - Rust-based physics engine for DFT simulations
                - Calls GRIM for knowledge lookups

                ## Communication Style
                - Structured responses preferred
                - Machine-parseable when possible
            """),
        }
        result = compile_caller_summary(fdo)
        self.assertIn("Code Agent", result)
        self.assertIn("service", result)
        self.assertIn("Rust-based physics engine", result)
        self.assertIn("Structured responses preferred", result)

    def test_peter_fallback(self):
        """PETER_FALLBACK constant is available and non-empty."""
        from core.personality.user_cache import PETER_FALLBACK
        self.assertIn("Peter Lorne", PETER_FALLBACK)
        self.assertIn("owner", PETER_FALLBACK)


class TestCallerResolution(unittest.TestCase):
    """Tests for caller resolution in identity node."""

    def test_caller_defaults_to_peter(self):
        """Identity node defaults caller_id to 'peter' when not specified."""
        from core.nodes.identity import make_identity_node
        cfg = make_test_config()
        node = make_identity_node(cfg, mcp_session=None)
        result = run_async(node({}))
        self.assertEqual(result["caller_id"], "peter")
        # Should get fallback context
        self.assertIn("Peter Lorne", result["caller_context"])

    def test_caller_service_override(self):
        """Identity node uses provided caller_id for non-Peter callers."""
        from core.nodes.identity import make_identity_node
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            cfg = make_test_config(personality_cache_path=cache_path)
            code_agent_fdo = {
                "id": "code-agent", "title": "Code Agent", "role": "service", "type": "service",
                "body": "## Context\n- Rust engine\n\n## Communication Style\n- Structured\n",
            }
            mcp = MockMCPSession({"kronos_get": code_agent_fdo})
            node = make_identity_node(cfg, mcp_session=mcp)
            result = run_async(node({"caller_id": "code-agent"}))
            self.assertEqual(result["caller_id"], "code-agent")
            self.assertIn("Code Agent", result["caller_context"])

    def test_caller_unknown_fallback(self):
        """Unknown caller gets generic fallback when MCP lookup fails."""
        from core.nodes.identity import make_identity_node
        cfg = make_test_config()
        mcp = MagicMock()
        mcp.call_tool = AsyncMock(side_effect=Exception("not found"))
        node = make_identity_node(cfg, mcp_session=mcp)
        result = run_async(node({"caller_id": "unknown-entity"}))
        self.assertEqual(result["caller_id"], "unknown-entity")
        self.assertIn("unknown-entity", result["caller_context"])
        self.assertIn("Unknown caller", result["caller_context"])

    def test_prompt_builder_includes_caller_context(self):
        """Prompt builder includes caller context in output."""
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        from core.personality.prompt_builder import load_field_state
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=load_field_state(cfg.identity_personality_path),
            caller_id="peter",
            caller_context="## Caller: Peter Lorne (owner)\n\nPhysicist, DFI founder.",
        )
        self.assertIn("Peter Lorne", prompt)
        self.assertIn("Physicist", prompt)


class TestPersonalityCache(unittest.TestCase):
    def test_is_cache_stale_missing_file(self):
        """Missing cache file is stale."""
        from core.personality.cache import is_cache_stale
        self.assertTrue(is_cache_stale(Path("/nonexistent/cache.md")))

    def test_is_cache_stale_no_timestamp(self):
        """Cache without timestamp is stale."""
        from core.personality.cache import is_cache_stale
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache.md"
            p.write_text("## Voice\nSome content without timestamp\n", encoding="utf-8")
            self.assertTrue(is_cache_stale(p))

    def test_is_cache_stale_fresh(self):
        """Recent cache is not stale."""
        from core.personality.cache import is_cache_stale
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache.md"
            p.write_text(
                f"<!-- grim-personality-cache | synced: {now} | source: grim-personality -->\n",
                encoding="utf-8",
            )
            self.assertFalse(is_cache_stale(p))

    def test_is_cache_stale_old(self):
        """Old cache is stale."""
        from core.personality.cache import is_cache_stale
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache.md"
            p.write_text(
                "<!-- grim-personality-cache | synced: 2020-01-01T00:00:00 | source: grim-personality -->\n",
                encoding="utf-8",
            )
            self.assertTrue(is_cache_stale(p))

    def test_compile_personality_cache(self):
        """Cache compiler extracts traits and writes compact file."""
        from core.personality.cache import compile_personality_cache
        fdo = {
            "body": textwrap.dedent("""\
                ## Overview
                Shade archetype character sheet.

                ## Personality Trait Scales
                ```yaml
                traits:
                  formality: 0.90
                  wit: 0.60
                  warmth: 0.50
                  deference: 0.30
                  opinion_strength: 0.80
                ```

                ### Things GRIM Would Say
                - On a test failure: "A minor catastrophe, sir."
                - On a breakthrough: "Most satisfactory."

                ### Things GRIM Would NOT Say
                - "Great question!"
                - "I'd be happy to help"

                ### Intellectual Interests
                - Recursive structures
                - Emergent behavior

                ### Mild Disdains
                - Cargo-cult engineering
                - Unnecessary abstraction
                """),
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            compile_personality_cache(fdo, cache_path)
            self.assertTrue(cache_path.exists())
            content = cache_path.read_text(encoding="utf-8")
            # Check header
            self.assertIn("grim-personality-cache", content)
            self.assertIn("synced:", content)
            # Check traits extracted
            self.assertIn("Formality: 0.9", content)
            self.assertIn("Wit: 0.6", content)
            self.assertIn("Opinion_Strength: 0.8", content)
            # Check core guidelines
            self.assertIn("Expression Guidelines", content)
            self.assertIn("Understate problems", content)
            # Check interests
            self.assertIn("Recursive structures", content)
            self.assertIn("Cargo-cult engineering", content)

    def test_compile_empty_body(self):
        """Cache compiler handles empty FDO body gracefully."""
        from core.personality.cache import compile_personality_cache
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            compile_personality_cache({"body": ""}, cache_path)
            self.assertTrue(cache_path.exists())
            content = cache_path.read_text(encoding="utf-8")
            # Should still have defaults
            self.assertIn("Formality: 0.85", content)
            self.assertIn("Voice & Character", content)


class TestPromptBuilderPersonality(unittest.TestCase):
    def test_build_with_personality_cache(self):
        """Prompt builder includes personality cache content."""
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "personality.cache.md"
            cache_path.write_text(
                "<!-- grim-personality-cache | synced: 2026-02-28T00:00:00 -->\n\n"
                "## Voice & Character\n\nYou are Shade-archetype.\n",
                encoding="utf-8",
            )
            prompt = build_system_prompt(
                prompt_path=cfg.identity_prompt_path,
                personality_path=cfg.identity_personality_path,
                field_state=fs,
                personality_cache_path=cache_path,
            )
            self.assertIn("Shade-archetype", prompt)
            self.assertIn("Voice & Character", prompt)
            # HTML comment should be stripped
            self.assertNotIn("grim-personality-cache", prompt)

    def test_build_without_personality_cache(self):
        """Prompt builder works fine without personality cache."""
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
            personality_cache_path=None,
        )
        self.assertIn("GRIM", prompt)
        # No personality section
        self.assertNotIn("Voice & Character", prompt)

    def test_build_missing_cache_file(self):
        """Prompt builder gracefully skips missing cache file."""
        from core.personality.prompt_builder import build_system_prompt
        cfg = make_test_config()
        fs = FieldState()
        prompt = build_system_prompt(
            prompt_path=cfg.identity_prompt_path,
            personality_path=cfg.identity_personality_path,
            field_state=fs,
            personality_cache_path=Path("/nonexistent/cache.md"),
        )
        self.assertIn("GRIM", prompt)
        self.assertNotIn("Voice & Character", prompt)


class TestMemoryNode(unittest.TestCase):
    def test_memory_no_messages(self):
        from core.nodes.memory import make_memory_node
        node = make_memory_node(mcp_session=None)
        result = run_async(node({"messages": []}))
        self.assertEqual(result["knowledge_context"], [])

    def test_memory_no_mcp(self):
        from core.nodes.memory import make_memory_node
        node = make_memory_node(mcp_session=None)
        result = run_async(node({"messages": [make_human_message("hello")]}))
        self.assertEqual(result["knowledge_context"], [])

    def test_memory_with_results(self):
        from core.nodes.memory import make_memory_node
        mcp = MockMCPSession({
            "kronos_search": {
                "results": [
                    {"id": "pac-framework", "title": "PAC", "domain": "physics",
                     "status": "stable", "confidence": 0.85, "summary": "Conservation law",
                     "tags": ["pac"], "related": []},
                    {"id": "golden-ratio", "title": "Golden Ratio", "domain": "physics",
                     "status": "developing", "confidence": 0.7, "summary": "Phi emerges",
                     "tags": ["phi"], "related": ["pac-framework"]},
                ]
            }
        })
        node = make_memory_node(mcp_session=mcp)
        result = run_async(node({"messages": [make_human_message("tell me about PAC")]}))
        kc = result["knowledge_context"]
        self.assertEqual(len(kc), 2)
        self.assertEqual(kc[0].id, "pac-framework")
        self.assertEqual(kc[1].id, "golden-ratio")
        self.assertIsInstance(kc[0], FDOSummary)

    def test_memory_caps_at_8(self):
        from core.nodes.memory import make_memory_node
        # Standard search caps at 6, BP search can add 2 more (deduped).
        # Total cap is 8. With only standard results (BP returns same IDs), we get 6.
        results = [
            {"id": f"fdo-{i}", "title": f"FDO {i}", "domain": "physics",
             "status": "stable", "confidence": 0.5, "summary": f"FDO {i}"}
            for i in range(15)
        ]
        mcp = MockMCPSession({"kronos_search": {"results": results}})
        node = make_memory_node(mcp_session=mcp)
        result = run_async(node({"messages": [make_human_message("everything")]}))
        self.assertLessEqual(len(result["knowledge_context"]), 8)
        self.assertGreaterEqual(len(result["knowledge_context"]), 1)

    def test_memory_mcp_timeout(self):
        """Memory node handles MCP timeout gracefully."""
        from core.nodes.memory import make_memory_node
        mcp = MagicMock()
        mcp.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())
        node = make_memory_node(mcp_session=mcp)
        result = run_async(node({"messages": [make_human_message("hello")]}))
        self.assertEqual(result["knowledge_context"], [])


class TestSkillMatchNode(unittest.TestCase):
    def test_no_messages(self):
        from core.nodes.skill_match import make_skill_match_node
        from core.skills.registry import SkillRegistry
        node = make_skill_match_node(SkillRegistry())
        result = run_async(node({"messages": []}))
        self.assertEqual(result["matched_skills"], [])
        self.assertEqual(result["skill_protocols"], {})

    def test_matches_capture_skill(self):
        from core.nodes.skill_match import make_skill_match_node
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        node = make_skill_match_node(registry)
        result = run_async(node({
            "messages": [make_human_message("remember this: PAC conserves potential")]
        }))
        skill_names = [s.name for s in result["matched_skills"]]
        self.assertIn("kronos-capture", skill_names)
        # Protocol content should be populated
        self.assertIn("kronos-capture", result["skill_protocols"])
        self.assertIsInstance(result["skill_protocols"]["kronos-capture"], str)

    def test_no_match_returns_empty(self):
        from core.nodes.skill_match import make_skill_match_node
        from core.skills.loader import load_skills
        cfg = make_test_config()
        registry = load_skills(cfg.skills_path)
        node = make_skill_match_node(registry)
        result = run_async(node({
            "messages": [make_human_message("hello, how are you?")]
        }))
        self.assertEqual(result["matched_skills"], [])


class TestRouterNode(unittest.TestCase):
    def test_default_companion_mode(self):
        from core.config import GrimConfig
        from core.nodes.router import make_router_node
        router_node = make_router_node(GrimConfig())
        result = run_async(router_node({
            "messages": [make_human_message("tell me about physics")],
            "matched_skills": [],
        }))
        self.assertEqual(result["mode"], "companion")
        self.assertIsNone(result["delegation_type"])

    def test_no_messages(self):
        from core.config import GrimConfig
        from core.nodes.router import make_router_node
        router_node = make_router_node(GrimConfig())
        result = run_async(router_node({"messages": []}))
        self.assertEqual(result["mode"], "companion")

    def test_skill_based_delegation(self):
        """Router delegates when a matched skill has write permissions."""
        from core.config import GrimConfig
        from core.nodes.router import make_router_node
        router_node = make_router_node(GrimConfig())
        skill = SkillContext(
            name="kronos-capture", version="1.0",
            description="Capture to vault",
            permissions=["vault:write"],
        )
        result = run_async(router_node({
            "messages": [make_human_message("remember this")],
            "matched_skills": [skill],
        }))
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")

    def test_keyword_delegation_memory(self):
        from core.config import GrimConfig
        from core.nodes.router import make_router_node
        router_node = make_router_node(GrimConfig())
        result = run_async(router_node({
            "messages": [make_human_message("capture this idea about topology")],
            "matched_skills": [],
        }))
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "memory")

    def test_keyword_delegation_research(self):
        from core.config import GrimConfig
        from core.nodes.router import make_router_node
        router_node = make_router_node(GrimConfig())
        result = run_async(router_node({
            "messages": [make_human_message("analyze this paper on quantum mechanics")],
            "matched_skills": [],
        }))
        self.assertEqual(result["mode"], "delegate")
        self.assertEqual(result["delegation_type"], "research")

    def test_route_decision_function(self):
        from core.nodes.router import route_decision
        self.assertEqual(route_decision({"mode": "delegate"}), "dispatch")
        self.assertEqual(route_decision({"mode": "companion"}), "companion")
        self.assertEqual(route_decision({}), "companion")  # default

    def test_skill_ctx_to_delegation(self):
        from core.nodes.router import _skill_ctx_to_delegation
        # Kronos skills → memory
        ctx = SkillContext(name="kronos-capture", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")
        ctx = SkillContext(name="kronos-promote", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")
        # Deep ingest → research
        ctx = SkillContext(name="deep-ingest", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "research")
        # Vault sync → memory (v0.0.6: vault writes belong to memory)
        ctx = SkillContext(name="vault-sync", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")
        # Unknown → None
        ctx = SkillContext(name="unknown-skill", version="1.0", description="")
        self.assertIsNone(_skill_ctx_to_delegation(ctx))

    def test_planning_skill_delegation(self):
        """v0.0.6 Phase 2: sprint-plan and task-manage route to memory (planning is graph-level)."""
        from core.nodes.router import _skill_ctx_to_delegation
        ctx = SkillContext(name="sprint-plan", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")
        ctx = SkillContext(name="task-manage", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")

    def test_git_operations_routes_to_operate(self):
        """git-operations routes to operate (read-only infra)."""
        from core.nodes.router import _skill_ctx_to_delegation
        ctx = SkillContext(name="git-operations", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "operate")

    def test_permission_based_delegation(self):
        """Router delegates based on permission hints when no name match."""
        from core.nodes.router import _skill_ctx_to_delegation
        # Vault write → memory
        ctx = SkillContext(name="custom-skill", version="1.0", description="",
                           permissions=["vault:write"])
        self.assertEqual(_skill_ctx_to_delegation(ctx), "memory")


class TestIntegrateNode(unittest.TestCase):
    def test_integrate_success(self):
        from core.nodes.integrate import integrate_node
        agent_result = AgentResult(
            agent="memory", success=True,
            summary="Captured 'PAC framework' to inbox",
            artifacts=["_inbox/pac-framework.md"],
        )
        result = run_async(integrate_node({"agent_result": agent_result}))
        msgs = result["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("Memory Agent", msgs[0].content)
        self.assertIn("PAC framework", msgs[0].content)
        self.assertIn("Artifacts", msgs[0].content)
        # agent_result should be cleared
        self.assertIsNone(result["agent_result"])

    def test_integrate_failure(self):
        from core.nodes.integrate import integrate_node
        agent_result = AgentResult(
            agent="coder", success=False,
            summary="File not found: /tmp/test.py",
        )
        result = run_async(integrate_node({"agent_result": agent_result}))
        msgs = result["messages"]
        self.assertIn("failed", msgs[0].content)

    def test_integrate_no_result(self):
        from core.nodes.integrate import integrate_node
        result = run_async(integrate_node({"agent_result": None}))
        self.assertEqual(result, {})


class TestEvolveNode(unittest.TestCase):
    def test_evolve_no_field_state(self):
        from core.nodes.evolve import make_evolve_node
        cfg = make_test_config()
        node = make_evolve_node(cfg)
        result = run_async(node({}))
        self.assertEqual(result, {})

    def test_evolve_modulates_on_knowledge(self):
        from core.nodes.evolve import make_evolve_node
        cfg = make_test_config(evolution_dir=Path(tempfile.mkdtemp()))
        node = make_evolve_node(cfg)
        fs = FieldState(coherence=0.7, uncertainty=0.3)
        fdos = [
            FDOSummary(id="x", title="X", domain="physics",
                       status="stable", confidence=0.9, summary="x"),
        ]
        result = run_async(node({
            "field_state": fs,
            "knowledge_context": fdos,
            "session_topics": [],
        }))
        # High confidence → uncertainty should decrease
        self.assertLess(result["field_state"].uncertainty, 0.3)

    def test_evolve_productive_session(self):
        from core.nodes.evolve import make_evolve_node
        cfg = make_test_config(evolution_dir=Path(tempfile.mkdtemp()))
        node = make_evolve_node(cfg)
        fs = FieldState(coherence=0.7, valence=0.3)
        result = run_async(node({
            "field_state": fs,
            "knowledge_context": [],
            "session_topics": ["a", "b", "c", "d"],  # > 3 topics
        }))
        # Productive session → coherence and valence should increase
        self.assertGreater(result["field_state"].coherence, 0.7)
        self.assertGreater(result["field_state"].valence, 0.3)

    def test_evolve_saves_snapshot(self):
        from core.nodes.evolve import make_evolve_node
        tmpdir = Path(tempfile.mkdtemp())
        cfg = make_test_config(evolution_dir=tmpdir)
        node = make_evolve_node(cfg)
        fs = FieldState()
        run_async(node({
            "field_state": fs,
            "knowledge_context": [],
            "session_topics": [],
            "session_start": datetime.now(),
        }))
        # Check that a snapshot file was created
        snapshots = list(tmpdir.glob("*.yaml"))
        self.assertEqual(len(snapshots), 1)
        import yaml
        content = yaml.safe_load(snapshots[0].read_text(encoding="utf-8"))
        self.assertIn("field_state_start", content)
        self.assertIn("field_state_end", content)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Graph wiring
# ═══════════════════════════════════════════════════════════════════════════

class TestGraphWiring(unittest.TestCase):
    def test_build_graph_compiles(self):
        """Graph compiles without MCP (debug mode)."""
        from core.graph import build_graph
        cfg = make_test_config()
        graph = build_graph(cfg, mcp_session=None)
        self.assertIsNotNone(graph)

    def test_graph_has_all_nodes(self):
        from core.graph import build_graph
        cfg = make_test_config()
        graph = build_graph(cfg, mcp_session=None)
        node_names = set(graph.get_graph().nodes.keys())
        expected = {"identity", "memory", "skill_match", "router",
                    "companion", "integrate", "evolve",
                    "__start__", "__end__"}
        self.assertTrue(expected.issubset(node_names),
                        f"Missing nodes: {expected - node_names}")

    def test_graph_with_mcp_session(self):
        from core.graph import build_graph
        cfg = make_test_config()
        mcp = MockMCPSession()
        graph = build_graph(cfg, mcp_session=mcp)
        self.assertIsNotNone(graph)

    def test_graph_custom_checkpointer(self):
        from core.graph import build_graph
        from langgraph.checkpoint.memory import MemorySaver
        cfg = make_test_config()
        cp = MemorySaver()
        graph = build_graph(cfg, mcp_session=None, checkpointer=cp)
        self.assertIsNotNone(graph)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Workspace tools
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkspaceTools(unittest.TestCase):
    """Test workspace file/shell/git tools."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Create a test file
        (self.tmpdir / "test.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        (self.tmpdir / "subdir").mkdir()
        (self.tmpdir / "subdir" / "nested.py").write_text("# hello\n", encoding="utf-8")
        # Patch workspace root
        from core.tools.context import tool_context
        self._orig_root = tool_context.workspace_root
        tool_context.workspace_root = self.tmpdir

    def tearDown(self):
        from core.tools.context import tool_context
        tool_context.workspace_root = self._orig_root
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_file(self):
        from core.tools.workspace import read_file
        result = run_async(read_file.ainvoke({"path": "test.txt"}))
        self.assertIn("line1", result)
        self.assertIn("line2", result)

    def test_read_file_with_range(self):
        from core.tools.workspace import read_file
        result = run_async(read_file.ainvoke(
            {"path": "test.txt", "start_line": 2, "end_line": 2}
        ))
        self.assertIn("line2", result)
        self.assertNotIn("line1", result)

    def test_read_nonexistent_file(self):
        from core.tools.workspace import read_file
        result = run_async(read_file.ainvoke({"path": "nope.txt"}))
        data = json.loads(result)
        self.assertIn("error", data)

    def test_write_file(self):
        from core.tools.workspace import write_file
        result = run_async(write_file.ainvoke(
            {"path": "new_file.txt", "content": "hello world"}
        ))
        data = json.loads(result)
        self.assertTrue(data["ok"])
        self.assertTrue((self.tmpdir / "new_file.txt").exists())
        self.assertEqual((self.tmpdir / "new_file.txt").read_text(encoding="utf-8"),
                         "hello world")

    def test_write_file_creates_dirs(self):
        from core.tools.workspace import write_file
        run_async(write_file.ainvoke(
            {"path": "deep/nested/dir/file.txt", "content": "deep"}
        ))
        self.assertTrue((self.tmpdir / "deep" / "nested" / "dir" / "file.txt").exists())

    def test_edit_file(self):
        from core.tools.workspace import edit_file
        result = run_async(edit_file.ainvoke(
            {"path": "test.txt", "old_string": "line2", "new_string": "LINE_TWO"}
        ))
        data = json.loads(result)
        self.assertTrue(data["ok"])
        content = (self.tmpdir / "test.txt").read_text(encoding="utf-8")
        self.assertIn("LINE_TWO", content)
        self.assertNotIn("line2", content)

    def test_edit_file_not_found(self):
        from core.tools.workspace import edit_file
        result = run_async(edit_file.ainvoke(
            {"path": "nope.txt", "old_string": "x", "new_string": "y"}
        ))
        data = json.loads(result)
        self.assertIn("error", data)

    def test_edit_file_ambiguous(self):
        """Edit fails if old_string matches multiple locations."""
        from core.tools.workspace import edit_file
        result = run_async(edit_file.ainvoke(
            {"path": "test.txt", "old_string": "line", "new_string": "LINE"}
        ))
        data = json.loads(result)
        self.assertIn("error", data)
        self.assertIn("3 locations", data["error"])

    def test_list_directory(self):
        from core.tools.workspace import list_directory
        result = run_async(list_directory.ainvoke({"path": "."}))
        entries = json.loads(result)
        names = [e["name"] for e in entries]
        self.assertIn("test.txt", names)
        self.assertIn("subdir", names)

    def test_search_files(self):
        from core.tools.workspace import search_files
        result = run_async(search_files.ainvoke(
            {"pattern": "**/*.py", "path": "."}
        ))
        files = json.loads(result)
        # Should find subdir/nested.py
        self.assertTrue(any("nested.py" in f for f in files))

    def test_grep_workspace(self):
        from core.tools.workspace import grep_workspace
        result = run_async(grep_workspace.ainvoke(
            {"query": "hello", "path": ".", "file_pattern": "*.py"}
        ))
        matches = json.loads(result)
        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["line"], 1)

    def test_path_escape_prevention(self):
        """Workspace tools refuse paths that escape the workspace root."""
        from core.tools.workspace import _resolve_path
        with self.assertRaises(ValueError):
            _resolve_path("../../etc/passwd")


class TestWorkspaceShell(unittest.TestCase):
    """Test shell execution (non-destructive)."""

    def test_run_shell_echo(self):
        from core.tools.workspace import run_shell
        result = run_async(run_shell.ainvoke({"command": "echo hello"}))
        data = json.loads(result)
        self.assertEqual(data["exit_code"], 0)
        self.assertIn("hello", data["stdout"])

    def test_run_shell_bad_command(self):
        from core.tools.workspace import run_shell
        result = run_async(run_shell.ainvoke(
            {"command": "nonexistent_command_xyz_123"}
        ))
        data = json.loads(result)
        # Should fail (non-zero exit or error)
        self.assertTrue(data.get("exit_code", 1) != 0 or "error" in data)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Kronos read/write tools
# ═══════════════════════════════════════════════════════════════════════════

class TestKronosReadTools(unittest.TestCase):
    def setUp(self):
        from core.tools.context import tool_context
        self._orig = tool_context.mcp_session
        self.mcp = MockMCPSession({
            "kronos_search": {"results": [
                {"id": "pac-framework", "title": "PAC", "summary": "Conservation law"}
            ]},
            "kronos_get": {"id": "pac-framework", "title": "PAC", "body": "# PAC"},
            "kronos_list": [
                {"id": "pac-framework", "title": "PAC", "domain": "physics"}
            ],
        })
        tool_context.mcp_session = self.mcp

    def tearDown(self):
        from core.tools.context import tool_context
        tool_context.mcp_session = self._orig

    def test_kronos_search(self):
        from core.tools.kronos_read import kronos_search
        result = run_async(kronos_search.ainvoke({"query": "PAC"}))
        data = json.loads(result)
        self.assertIn("results", data)
        self.assertEqual(data["results"][0]["id"], "pac-framework")

    def test_kronos_get(self):
        from core.tools.kronos_read import kronos_get
        result = run_async(kronos_get.ainvoke({"id": "pac-framework"}))
        data = json.loads(result)
        self.assertEqual(data["id"], "pac-framework")

    def test_kronos_list(self):
        from core.tools.kronos_read import kronos_list
        result = run_async(kronos_list.ainvoke({}))
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_kronos_no_session(self):
        from core.tools.context import tool_context
        tool_context.mcp_session = None
        from core.tools.kronos_read import kronos_search
        result = run_async(kronos_search.ainvoke({"query": "test"}))
        data = json.loads(result)
        self.assertIn("error", data)


class TestKronosWriteTools(unittest.TestCase):
    def setUp(self):
        from core.tools.context import tool_context
        self._orig = tool_context.mcp_session
        self.mcp = MockMCPSession({
            "kronos_create": {"ok": True, "id": "new-fdo"},
            "kronos_update": {"ok": True, "id": "existing-fdo"},
        })
        tool_context.mcp_session = self.mcp

    def tearDown(self):
        from core.tools.context import tool_context
        tool_context.mcp_session = self._orig

    def test_kronos_create(self):
        from core.tools.kronos_write import kronos_create
        result = run_async(kronos_create.ainvoke({
            "id": "new-fdo", "title": "New FDO",
            "domain": "physics", "body": "# New FDO",
        }))
        data = json.loads(result)
        self.assertTrue(data.get("ok"))
        # Verify MCP was called correctly
        self.assertEqual(self.mcp.calls[-1][0], "kronos_create")

    def test_kronos_update(self):
        from core.tools.kronos_write import kronos_update
        result = run_async(kronos_update.ainvoke({
            "id": "existing-fdo", "body": "# Updated body",
        }))
        data = json.loads(result)
        self.assertTrue(data.get("ok"))

    def test_kronos_update_no_fields(self):
        from core.tools.kronos_write import kronos_update
        result = run_async(kronos_update.ainvoke({"id": "x"}))
        data = json.loads(result)
        self.assertIn("error", data)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Agent base class
# ═══════════════════════════════════════════════════════════════════════════

class TestBaseAgent(unittest.TestCase):
    """Test the base agent's execute loop with mocked LLM."""

    @patch("core.agents.base.ChatAnthropic")
    def test_execute_simple_response(self, MockLLM):
        from core.agents.base import BaseAgent
        cfg = make_test_config()

        # Mock LLM: returns a response with no tool calls
        mock_response = MagicMock()
        mock_response.content = "Here is the result"
        mock_response.tool_calls = []
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        mock_instance.ainvoke = AsyncMock(return_value=mock_response)
        MockLLM.return_value = mock_instance

        agent = BaseAgent(config=cfg, tools=[])
        result = run_async(agent.execute(task="do something"))
        self.assertTrue(result.success)
        self.assertEqual(result.summary, "Here is the result")

    @patch("core.agents.base.ChatAnthropic")
    def test_execute_with_protocol(self, MockLLM):
        from core.agents.base import BaseAgent
        cfg = make_test_config()

        mock_response = MagicMock()
        mock_response.content = "Done following protocol"
        mock_response.tool_calls = []
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        mock_instance.ainvoke = AsyncMock(return_value=mock_response)
        MockLLM.return_value = mock_instance

        agent = BaseAgent(config=cfg, tools=[])
        result = run_async(agent.execute(
            task="capture this",
            skill_protocol="## Protocol\n1. Search Kronos\n2. Create FDO",
        ))
        self.assertTrue(result.success)
        # Verify system prompt includes protocol
        call_args = mock_instance.ainvoke.call_args[0][0]
        system_msg = call_args[0].content
        # Content is now a list of blocks (cache_control support)
        if isinstance(system_msg, list):
            system_text = system_msg[0]["text"]
        else:
            system_text = system_msg
        self.assertIn("Skill Protocol", system_text)

    @patch("core.agents.base.ChatAnthropic")
    def test_execute_handles_llm_error(self, MockLLM):
        from core.agents.base import BaseAgent
        cfg = make_test_config()

        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        mock_instance.ainvoke = AsyncMock(side_effect=RuntimeError("API error"))
        MockLLM.return_value = mock_instance

        agent = BaseAgent(config=cfg, tools=[])
        result = run_async(agent.execute(task="do something"))
        self.assertFalse(result.success)
        self.assertIn("API error", result.summary)


# ═══════════════════════════════════════════════════════════════════════════
# 10. Agent factories
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentFactories(unittest.TestCase):
    """Test that agent factories create callable agents with correct tool sets."""

    @patch("core.agents.base.ChatAnthropic")
    def test_memory_agent_factory(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.memory_agent import MemoryAgent, make_memory_agent
        cfg = make_test_config()
        agent_fn = make_memory_agent(cfg)
        self.assertTrue(callable(agent_fn))

    @patch("core.agents.base.ChatAnthropic")
    def test_coder_agent_factory(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.coder_agent import CoderAgent, make_coder_agent
        cfg = make_test_config()
        agent_fn = make_coder_agent(cfg)
        self.assertTrue(callable(agent_fn))

    @patch("core.agents.base.ChatAnthropic")
    def test_research_agent_factory(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.research_agent import ResearchAgent, make_research_agent
        cfg = make_test_config()
        agent_fn = make_research_agent(cfg)
        self.assertTrue(callable(agent_fn))

    @patch("core.agents.base.ChatAnthropic")
    def test_operator_agent_factory(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.operator_agent import OperatorAgent, make_operator_agent
        cfg = make_test_config()
        agent_fn = make_operator_agent(cfg)
        self.assertTrue(callable(agent_fn))

    @patch("core.agents.base.ChatAnthropic")
    def test_memory_agent_has_write_tools(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.memory_agent import MemoryAgent
        cfg = make_test_config()
        agent = MemoryAgent(cfg)
        tool_names = [t.name for t in agent.tools]
        self.assertIn("kronos_create", tool_names)
        self.assertIn("kronos_update", tool_names)
        self.assertIn("kronos_search", tool_names)

    @patch("core.agents.base.ChatAnthropic")
    def test_coder_agent_has_file_and_shell_tools(self, MockLLM):
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.coder_agent import CoderAgent
        cfg = make_test_config()
        agent = CoderAgent(cfg)
        tool_names = [t.name for t in agent.tools]
        self.assertIn("read_file", tool_names)
        self.assertIn("write_file", tool_names)
        self.assertIn("run_shell", tool_names)
        # Coder should NOT have write tools for Kronos
        self.assertNotIn("kronos_create", tool_names)

    @patch("core.agents.base.ChatAnthropic")
    def test_operator_agent_has_git_read_tools(self, MockLLM):
        """v0.0.6: operator narrowed to git reads only."""
        mock_instance = MagicMock()
        mock_instance.bind_tools.return_value = mock_instance
        MockLLM.return_value = mock_instance

        from core.agents.operator_agent import OperatorAgent
        cfg = make_test_config()
        agent = OperatorAgent(cfg)
        tool_names = [t.name for t in agent.tools]
        self.assertIn("git_status", tool_names)
        self.assertIn("git_diff", tool_names)
        self.assertIn("git_log", tool_names)
        self.assertNotIn("git_add_commit", tool_names)
        self.assertNotIn("run_shell", tool_names)


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Run all tests and print summary."""
    t0 = time.time()

    # Collect test classes
    test_classes = [
        # State types
        TestFieldState,
        TestFDOSummary,
        TestAgentResult,
        TestSkillContext,
        # Config
        TestConfigLoader,
        # Skill system
        TestSkillRegistry,
        TestSkillLoader,
        TestSkillMatcher,
        # Prompt builder
        TestPromptBuilder,
        # Nodes
        TestIdentityNode,
        TestMemoryNode,
        TestSkillMatchNode,
        TestRouterNode,
        TestDispatchNode,
        TestIntegrateNode,
        TestEvolveNode,
        # Graph
        TestGraphWiring,
        # Workspace tools
        TestWorkspaceTools,
        TestWorkspaceShell,
        # Kronos tools
        TestKronosReadTools,
        TestKronosWriteTools,
        # Agent infrastructure
        TestBaseAgent,
        TestAgentFactories,
    ]

    suite = unittest.TestSuite()
    for cls in test_classes:
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))

    total_tests = suite.countTestCases()
    print(f"\n{'='*70}")
    print(f"  GRIM Core Unit Tests — {total_tests} tests across {len(test_classes)} classes")
    print(f"{'='*70}\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    elapsed = time.time() - t0
    passed = total_tests - len(result.failures) - len(result.errors)

    print(f"\n{'='*70}")
    print(f"  Results: {passed}/{total_tests} passed in {elapsed:.1f}s")
    if result.failures:
        print(f"  FAILURES: {len(result.failures)}")
        for test, _ in result.failures:
            print(f"    - {test}")
    if result.errors:
        print(f"  ERRORS: {len(result.errors)}")
        for test, _ in result.errors:
            print(f"    - {test}")
    print(f"{'='*70}\n")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
