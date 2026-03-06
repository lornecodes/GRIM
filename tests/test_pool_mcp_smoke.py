"""
Smoke tests for Pool MCP server — tool registration, schemas, error handling.

Run:
    PYTHONPATH=mcp/pool/src python -m pytest tests/test_pool_mcp_smoke.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Bootstrap
grim_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(grim_root / "mcp" / "pool" / "src"))

from pool_mcp.server import TOOLS, HANDLERS, TOOL_GROUPS, TOOL_TIMEOUTS


# ── Tool registration ────────────────────────────────────────────────────────

EXPECTED_TOOLS = [
    "pool_status",
    "pool_list_jobs",
    "pool_job_detail",
    "pool_job_logs",
    "pool_list_workspaces",
    "pool_workspace_diff",
    "pool_metrics",
    "pool_submit",
    "pool_cancel",
    "pool_clarify",
    "pool_retry",
    "pool_review",
    "pool_skills",
    "pool_skill_load",
]


class TestToolRegistration:
    def test_tool_count(self):
        assert len(TOOLS) == 14

    def test_handler_count(self):
        assert len(HANDLERS) == 14

    def test_tools_match_handlers(self):
        tool_names = {t.name for t in TOOLS}
        handler_names = set(HANDLERS.keys())
        assert tool_names == handler_names

    @pytest.mark.parametrize("name", EXPECTED_TOOLS)
    def test_tool_registered(self, name):
        tool_names = {t.name for t in TOOLS}
        assert name in tool_names

    @pytest.mark.parametrize("name", EXPECTED_TOOLS)
    def test_handler_registered(self, name):
        assert name in HANDLERS


class TestToolSchemas:
    @pytest.mark.parametrize("name", EXPECTED_TOOLS)
    def test_schema_is_object(self, name):
        tool_map = {t.name: t for t in TOOLS}
        schema = tool_map[name].inputSchema
        assert schema.get("type") == "object"

    @pytest.mark.parametrize("name", EXPECTED_TOOLS)
    def test_schema_has_properties(self, name):
        tool_map = {t.name: t for t in TOOLS}
        schema = tool_map[name].inputSchema
        assert "properties" in schema

    def test_submit_requires_job_type_and_instructions(self):
        tool_map = {t.name: t for t in TOOLS}
        schema = tool_map["pool_submit"].inputSchema
        assert "job_type" in schema["required"]
        assert "instructions" in schema["required"]

    def test_job_detail_requires_job_id(self):
        tool_map = {t.name: t for t in TOOLS}
        schema = tool_map["pool_job_detail"].inputSchema
        assert "job_id" in schema["required"]

    def test_review_requires_job_id_and_action(self):
        tool_map = {t.name: t for t in TOOLS}
        schema = tool_map["pool_review"].inputSchema
        assert "job_id" in schema["required"]
        assert "action" in schema["required"]


class TestToolGroups:
    def test_read_group_count(self):
        assert len(TOOL_GROUPS["pool:read"]) == 7

    def test_write_group_count(self):
        assert len(TOOL_GROUPS["pool:write"]) == 5

    def test_system_group_count(self):
        assert len(TOOL_GROUPS["system"]) == 2

    def test_all_tools_in_groups(self):
        all_grouped = (set(TOOL_GROUPS["pool:read"])
                       | set(TOOL_GROUPS["pool:write"])
                       | set(TOOL_GROUPS["system"]))
        all_tools = {t.name for t in TOOLS}
        assert all_grouped == all_tools

    def test_read_tools(self):
        expected = {"pool_status", "pool_list_jobs", "pool_job_detail",
                    "pool_job_logs", "pool_list_workspaces", "pool_workspace_diff",
                    "pool_metrics"}
        assert set(TOOL_GROUPS["pool:read"]) == expected

    def test_write_tools(self):
        expected = {"pool_submit", "pool_cancel", "pool_clarify",
                    "pool_retry", "pool_review"}
        assert set(TOOL_GROUPS["pool:write"]) == expected

    def test_system_tools(self):
        expected = {"pool_skills", "pool_skill_load"}
        assert set(TOOL_GROUPS["system"]) == expected


class TestTimeouts:
    @pytest.mark.parametrize("name", EXPECTED_TOOLS)
    def test_all_tools_have_timeouts(self, name):
        assert name in TOOL_TIMEOUTS

    def test_read_timeouts_are_fast(self):
        for name in TOOL_GROUPS["pool:read"]:
            assert TOOL_TIMEOUTS[name] <= 15

    def test_write_timeouts_are_longer(self):
        for name in TOOL_GROUPS["pool:write"]:
            assert TOOL_TIMEOUTS[name] >= 15
