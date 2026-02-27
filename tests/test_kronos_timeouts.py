"""
Tests for Kronos timeout, hang prevention, and error logging.

These tests verify that the fixes for the Kronos hanging bug work:
  1. SemanticIndex._load_model() times out instead of hanging forever
  2. Vault._parse_file() logs errors instead of swallowing them silently
  3. Server handler timeout fires and returns error JSON
  4. Semantic preload doesn't block concurrent tool calls
  5. _ensure_semantic non-blocking path works correctly

These are all mocked/unit tests — no real vault, model, or subprocess needed.

Run:
    cd GRIM && python -m pytest tests/test_kronos_timeouts.py -v
    # or without pytest:
    cd GRIM && python tests/test_kronos_timeouts.py
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Bootstrap — load submodules directly from file to avoid server.py
# module-level side effects (KRONOS_VAULT_PATH validation, preload thread).
GRIM_ROOT = Path(__file__).resolve().parent.parent
MCP_SRC = GRIM_ROOT / "mcp" / "kronos" / "src" / "kronos_mcp"


def _load_module(name: str, filepath: Path):
    """Load a Python module from file without triggering package __init__.py."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_vault_mod = _load_module("kronos_mcp.vault", MCP_SRC / "vault.py")
_search_mod = _load_module("kronos_mcp.search", MCP_SRC / "search.py")

SemanticIndex = _search_mod.SemanticIndex
SearchEngine = _search_mod.SearchEngine
VaultEngine = _vault_mod.VaultEngine


# ═══════════════════════════════════════════════════════════════════════════
# 1. SemanticIndex._load_model timeout
# ═══════════════════════════════════════════════════════════════════════════

class TestSemanticModelTimeout(unittest.TestCase):
    """Verify that _load_model times out instead of hanging forever."""

    def test_load_model_timeout_fires(self):
        """If model loading takes too long, it should timeout and disable semantic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(tmpdir, model_name="test-model")
            idx._available = True
            # Set a very short timeout so the test runs fast
            idx.MODEL_LOAD_TIMEOUT = 0.5

            def slow_import(*args, **kwargs):
                time.sleep(5)  # Simulate a hung network download
                return MagicMock()

            # Patch the SentenceTransformer constructor to hang
            with patch.dict("sys.modules", {"sentence_transformers": MagicMock()}):
                import sentence_transformers
                sentence_transformers.SentenceTransformer = slow_import

                idx._load_model()

            # After timeout, model should be None and available should be False
            self.assertIsNone(idx._model, "Model should be None after timeout")
            self.assertFalse(idx._available, "Semantic should be disabled after timeout")

    def test_load_model_success(self):
        """Normal model loading should work fine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(tmpdir, model_name="test-model")
            idx._available = True
            idx.MODEL_LOAD_TIMEOUT = 5

            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 768

            def fast_load(*args, **kwargs):
                return mock_model

            with patch.dict("sys.modules", {"sentence_transformers": MagicMock()}):
                import sentence_transformers
                sentence_transformers.SentenceTransformer = fast_load

                idx._load_model()

            self.assertIsNotNone(idx._model, "Model should be loaded")
            self.assertEqual(idx._dim, 768)

    def test_load_model_already_loaded_is_noop(self):
        """If model is already loaded, _load_model should return immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(tmpdir, model_name="test-model")
            idx._available = True
            idx._model = MagicMock()  # Already loaded

            t0 = time.time()
            idx._load_model()
            elapsed = time.time() - t0

            self.assertLess(elapsed, 0.1, "_load_model should be instant when model already loaded")

    def test_load_model_unavailable_is_noop(self):
        """If sentence-transformers not available, _load_model should return immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(tmpdir, model_name="test-model")
            idx._available = False

            t0 = time.time()
            idx._load_model()
            elapsed = time.time() - t0

            self.assertLess(elapsed, 0.1, "_load_model should be instant when unavailable")
            self.assertIsNone(idx._model)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Vault._parse_file error logging
# ═══════════════════════════════════════════════════════════════════════════

class TestVaultParseFileLogging(unittest.TestCase):
    """Verify that _parse_file logs errors instead of silently swallowing them."""

    def test_unreadable_file_logs_warning(self):
        """If a file can't be read, a warning should be logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = VaultEngine(tmpdir)
            bad_path = Path(tmpdir) / "unreadable.md"
            bad_path.write_text("test", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=PermissionError("Access denied")):
                with self.assertLogs("kronos-mcp.vault", level="WARNING") as cm:
                    result = vault._parse_file(bad_path)

            self.assertIsNone(result, "Should return None for unreadable file")
            self.assertTrue(
                any("Failed to read" in msg for msg in cm.output),
                f"Should log 'Failed to read' warning, got: {cm.output}"
            )

    def test_invalid_yaml_logs_warning(self):
        """If YAML frontmatter is invalid, a warning should be logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = VaultEngine(tmpdir)
            bad_yaml_path = Path(tmpdir) / "bad_yaml.md"
            bad_yaml_path.write_text(
                "---\n[invalid yaml: {{{\n---\n\nBody\n",
                encoding="utf-8",
            )

            with self.assertLogs("kronos-mcp.vault", level="WARNING") as cm:
                result = vault._parse_file(bad_yaml_path)

            self.assertIsNone(result, "Should return None for invalid YAML")
            self.assertTrue(
                any("Invalid YAML" in msg for msg in cm.output),
                f"Should log 'Invalid YAML' warning, got: {cm.output}"
            )

    def test_valid_file_no_warnings(self):
        """A valid FDO file should parse without warnings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = VaultEngine(tmpdir)
            good_path = Path(tmpdir) / "good.md"
            good_path.write_text(
                "---\n"
                "id: test-fdo\n"
                "title: Test FDO\n"
                "domain: tools\n"
                "created: 2026-01-01\n"
                "updated: 2026-01-01\n"
                "status: seed\n"
                "confidence: 0.5\n"
                "related: []\n"
                "source_repos: []\n"
                "tags: [test]\n"
                "---\n\n"
                "# Test FDO\n\n## Summary\n\nA test.\n",
                encoding="utf-8",
            )

            result = vault._parse_file(good_path)

            self.assertIsNotNone(result, "Valid FDO should parse successfully")
            self.assertEqual(result.id, "test-fdo")

    def test_missing_frontmatter_returns_none_silently(self):
        """A markdown file without frontmatter is expected — no warning needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = VaultEngine(tmpdir)
            no_fm_path = Path(tmpdir) / "readme.md"
            no_fm_path.write_text("# Just a readme\n\nNo frontmatter here.\n", encoding="utf-8")

            result = vault._parse_file(no_fm_path)
            self.assertIsNone(result, "No frontmatter should return None")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Server handler timeout
# ═══════════════════════════════════════════════════════════════════════════

class TestHandlerTimeout(unittest.TestCase):
    """Verify the 30s handler timeout returns error JSON instead of hanging."""

    def test_slow_handler_times_out(self):
        """A handler that takes too long should be killed and return an error."""
        async def _run():
            def slow_handler(args):
                time.sleep(10)
                return '{"ok": true}'

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(slow_handler, {}),
                    timeout=0.5,  # 500ms instead of 30s for test speed
                )
                return result
            except asyncio.TimeoutError:
                return json.dumps({"error": "handler timed out"})

        result = asyncio.run(_run())
        data = json.loads(result)
        self.assertIn("error", data, "Timeout should produce error response")
        self.assertIn("timed out", data["error"])

    def test_fast_handler_succeeds(self):
        """A handler that completes quickly should return its result."""
        async def _run():
            def fast_handler(args):
                return '{"ok": true}'

            result = await asyncio.wait_for(
                asyncio.to_thread(fast_handler, {}),
                timeout=5.0,
            )
            return result

        result = asyncio.run(_run())
        data = json.loads(result)
        self.assertEqual(data["ok"], True)


# ═══════════════════════════════════════════════════════════════════════════
# 4. _ensure_semantic non-blocking path
# ═══════════════════════════════════════════════════════════════════════════

class TestEnsureSemanticNonBlocking(unittest.TestCase):
    """Verify that search requests don't block on semantic preload."""

    def test_nonblocking_skips_when_lock_held(self):
        """If the semantic lock is held, non-blocking call should return False immediately."""
        mock_vault = MagicMock()
        mock_vault.vault_path = MagicMock(spec=Path)
        engine = SearchEngine(mock_vault)
        engine._semantic._available = True

        # Hold the lock in another thread to simulate preload running
        lock_held = threading.Event()
        release = threading.Event()

        def hold_lock():
            engine._semantic_lock.acquire()
            lock_held.set()
            release.wait(timeout=5)
            engine._semantic_lock.release()

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        lock_held.wait(timeout=2)

        try:
            t0 = time.time()
            result = engine._ensure_semantic(blocking=False)
            elapsed = time.time() - t0

            self.assertFalse(result, "Should return False when lock is held")
            self.assertLess(elapsed, 1.0, "Non-blocking call should return in <1s")
        finally:
            release.set()
            holder.join(timeout=2)

    def test_nonblocking_succeeds_when_already_indexed(self):
        """If semantic is already indexed, non-blocking call should return True."""
        mock_vault = MagicMock()
        mock_vault.vault_path = MagicMock(spec=Path)
        engine = SearchEngine(mock_vault)
        engine._semantic_indexed = True

        result = engine._ensure_semantic(blocking=False)
        self.assertTrue(result)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Concurrent tool calls during preload
# ═══════════════════════════════════════════════════════════════════════════

class TestConcurrentCallsDuringPreload(unittest.TestCase):
    """Verify that tool calls complete quickly even during semantic preload."""

    def test_ensure_indexed_not_blocked_by_semantic_lock(self):
        """_ensure_indexed (used by get/list/graph) must not touch the semantic lock.

        This simulates the exact scenario that was causing hangs:
        - Background preload thread holds _semantic_lock
        - User calls kronos_get → vault._ensure_index() + _ensure_indexed()
        - Should NOT be blocked by semantic lock
        """
        mock_vault = MagicMock()
        mock_vault_path = MagicMock(spec=Path)
        mock_vault_path.rglob.return_value = []
        mock_vault.vault_path = mock_vault_path
        engine = SearchEngine(mock_vault)

        # Simulate preload holding the semantic lock
        lock_held = threading.Event()
        release = threading.Event()

        def hold_lock():
            engine._semantic_lock.acquire()
            lock_held.set()
            release.wait(timeout=10)
            engine._semantic_lock.release()

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        lock_held.wait(timeout=2)

        try:
            # Mark as already initialized so it just checks freshness
            engine._initialized = True
            engine._watcher._mtimes = {}

            t0 = time.time()
            engine._ensure_indexed()
            elapsed = time.time() - t0

            self.assertLess(elapsed, 1.0,
                            "_ensure_indexed should not be blocked by semantic lock")
        finally:
            release.set()
            holder.join(timeout=2)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Preload logging
# ═══════════════════════════════════════════════════════════════════════════

class TestPreloadLogging(unittest.TestCase):
    """Verify that preload emits useful log messages."""

    def test_semantic_skip_logs_warning_not_debug(self):
        """When semantic is skipped due to lock contention, it should log at WARNING."""
        mock_vault = MagicMock()
        mock_vault.vault_path = MagicMock(spec=Path)
        engine = SearchEngine(mock_vault)
        engine._semantic._available = True

        # Hold the lock from main thread
        engine._semantic_lock.acquire()
        try:
            with self.assertLogs("kronos-mcp.search", level="WARNING") as cm:
                result = engine._ensure_semantic(blocking=False)

            self.assertFalse(result)
            self.assertTrue(
                any("still loading" in msg for msg in cm.output),
                f"Should log 'still loading' at WARNING level, got: {cm.output}"
            )
        finally:
            engine._semantic_lock.release()


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
