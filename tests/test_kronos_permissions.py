"""Tests for Kronos FDO domain permissions + caller-based ACL."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure kronos_mcp is importable
kronos_pkg = Path(__file__).resolve().parent.parent / "mcp" / "kronos" / "src"
if str(kronos_pkg) not in sys.path:
    sys.path.insert(0, str(kronos_pkg))


# ── Permissions module tests ──────────────────────────────────────


class TestPermissionsModule:
    def test_protected_domains(self):
        from kronos_mcp.permissions import PROTECTED_DOMAINS

        assert "physics" in PROTECTED_DOMAINS
        assert "modelling" in PROTECTED_DOMAINS
        assert "personal" in PROTECTED_DOMAINS
        assert "journal" in PROTECTED_DOMAINS
        assert "decisions" in PROTECTED_DOMAINS
        # Open domains
        assert "projects" not in PROTECTED_DOMAINS
        assert "ai-systems" not in PROTECTED_DOMAINS
        assert "notes" not in PROTECTED_DOMAINS

    def test_owner_can_write_all(self):
        from kronos_mcp.permissions import can_write

        assert can_write("peter", "physics") is True
        assert can_write("peter", "modelling") is True
        assert can_write("peter", "projects") is True
        assert can_write("peter", "notes") is True

    def test_owner_can_read_all(self):
        from kronos_mcp.permissions import can_read

        assert can_read("peter", "physics") is True
        assert can_read("peter", "personal") is True

    def test_pool_can_read_all(self):
        from kronos_mcp.permissions import can_read

        assert can_read("pool", "physics") is True
        assert can_read("pool", "modelling") is True
        assert can_read("pool", "projects") is True

    def test_pool_can_write_open_only(self):
        from kronos_mcp.permissions import can_write

        assert can_write("pool", "projects") is True
        assert can_write("pool", "notes") is True
        assert can_write("pool", "ai-systems") is True
        assert can_write("pool", "computing") is True

    def test_pool_cannot_write_protected(self):
        from kronos_mcp.permissions import can_write

        assert can_write("pool", "physics") is False
        assert can_write("pool", "modelling") is False
        assert can_write("pool", "personal") is False
        assert can_write("pool", "journal") is False
        assert can_write("pool", "decisions") is False

    def test_discord_read_open_only(self):
        from kronos_mcp.permissions import can_read

        assert can_read("discord", "projects") is True
        assert can_read("discord", "ai-systems") is True
        assert can_read("discord", "physics") is False
        assert can_read("discord", "personal") is False

    def test_discord_cannot_write(self):
        from kronos_mcp.permissions import can_write

        assert can_write("discord", "projects") is False
        assert can_write("discord", "physics") is False

    def test_unknown_caller_gets_owner_defaults(self):
        from kronos_mcp.permissions import can_write, can_read

        assert can_write("unknown_caller", "physics") is True
        assert can_read("unknown_caller", "physics") is True

    def test_is_protected(self):
        from kronos_mcp.permissions import is_protected

        assert is_protected("physics") is True
        assert is_protected("projects") is False


# ── Handler guard tests ───────────────────────────────────────────


class TestHandlerGuards:
    """Test that handle_create and handle_update respect domain permissions."""

    def test_handle_create_blocks_pool_on_physics(self):
        """Pool caller trying to create in physics domain gets approval_required."""
        from kronos_mcp import server

        # Mock the caller and vault
        original_caller = server._caller_id
        server._caller_id = "pool"
        try:
            with patch.object(server.vault, "_ensure_index"), \
                 patch.object(server.vault, "get", return_value=None):
                result = json.loads(server.handle_create({
                    "id": "test-fdo",
                    "title": "Test",
                    "domain": "physics",
                    "confidence": 0.5,
                    "body": "# Test\n## Summary\nTest body",
                }))
                assert result["approval_required"] is True
                assert result["domain"] == "physics"
                assert result["proposed"]["action"] == "create"
                assert result["proposed"]["id"] == "test-fdo"
        finally:
            server._caller_id = original_caller

    def test_handle_create_allows_pool_on_projects(self):
        """Pool caller can create in projects domain."""
        from kronos_mcp import server

        original_caller = server._caller_id
        server._caller_id = "pool"
        try:
            mock_fdo = None
            with patch.object(server.vault, "_ensure_index"), \
                 patch.object(server.vault, "get", return_value=None), \
                 patch.object(server.vault, "write_fdo", return_value="/fake/path"), \
                 patch.object(server.search_engine, "index_fdo"):
                result = json.loads(server.handle_create({
                    "id": "test-proj",
                    "title": "Test Project",
                    "domain": "projects",
                    "confidence": 0.5,
                    "body": "# Test\n## Summary\nTest body",
                }))
                assert "created" in result
                assert result["domain"] == "projects"
        finally:
            server._caller_id = original_caller

    def test_handle_update_blocks_pool_on_modelling(self):
        """Pool caller trying to update modelling FDO gets approval_required."""
        from kronos_mcp import server
        from kronos_mcp.vault import FDO

        original_caller = server._caller_id
        server._caller_id = "pool"
        mock_fdo = FDO(
            id="test-model", title="Test", domain="modelling",
            created="2026-01-01", updated="2026-01-01",
            status="stable", confidence=0.9,
            related=[], source_repos=[], tags=[],
            body="# Test\n## Summary\nBody", file_path="/fake",
        )
        try:
            with patch.object(server.vault, "_ensure_index"), \
                 patch.object(server.vault, "get", return_value=mock_fdo):
                result = json.loads(server.handle_update({
                    "id": "test-model",
                    "fields": {"status": "developing"},
                }))
                assert result["approval_required"] is True
                assert result["domain"] == "modelling"
                assert result["proposed"]["fields"]["status"] == "developing"
        finally:
            server._caller_id = original_caller

    def test_handle_update_allows_owner(self):
        """Owner caller can update any domain."""
        from kronos_mcp import server
        from kronos_mcp.vault import FDO

        original_caller = server._caller_id
        server._caller_id = "peter"
        mock_fdo = FDO(
            id="test-physics", title="Test", domain="physics",
            created="2026-01-01", updated="2026-01-01",
            status="stable", confidence=0.9,
            related=[], source_repos=[], tags=[],
            body="# Test\n## Summary\nBody", file_path="/fake",
        )
        try:
            with patch.object(server.vault, "_ensure_index"), \
                 patch.object(server.vault, "get", return_value=mock_fdo), \
                 patch.object(server.vault, "write_fdo", return_value="/fake/path"), \
                 patch.object(server.search_engine, "index_fdo"):
                result = json.loads(server.handle_update({
                    "id": "test-physics",
                    "fields": {"confidence": 0.95},
                }))
                assert "updated" in result
        finally:
            server._caller_id = original_caller


# ── Discord bot pattern tests ─────────────────────────────────────


class TestDiscordApprovalPatterns:
    def test_approve_pattern(self):
        from clients.discord_bot import APPROVE_PATTERN

        match = APPROVE_PATTERN.search("approve job-abc123")
        assert match is not None
        assert match.group(1) == "job-abc123"

    def test_deny_pattern_with_reason(self):
        from clients.discord_bot import DENY_PATTERN

        match = DENY_PATTERN.search("deny job-abc123 not ready yet")
        assert match is not None
        assert match.group(1) == "job-abc123"
        assert match.group(2).strip() == "not ready yet"

    def test_deny_pattern_without_reason(self):
        from clients.discord_bot import DENY_PATTERN

        match = DENY_PATTERN.search("deny job-abc123")
        assert match is not None
        assert match.group(1) == "job-abc123"
        assert match.group(2) is None

    def test_format_pool_event_approval_needed(self):
        from clients.discord_bot import format_pool_event

        proposed = {
            "approval_required": True,
            "domain": "physics",
            "proposed": {
                "action": "update",
                "id": "pac-comprehensive",
                "fields": {"confidence": 0.95},
            },
        }
        event = {
            "type": "job_blocked",
            "job_id": "job-test123",
            "question": json.dumps(proposed),
        }
        result = format_pool_event(event)
        assert "FDO Approval Needed" in result
        assert "physics" in result
        assert "pac-comprehensive" in result
        assert "approve job-test123" in result

    def test_format_pool_event_normal_blocked(self):
        from clients.discord_bot import format_pool_event

        event = {
            "type": "job_blocked",
            "job_id": "job-test456",
            "question": "What framework should I use?",
        }
        result = format_pool_event(event)
        assert "Job Needs Input" in result
        assert "clarify" in result
