"""Smoke tests for the execution pool — imports, wiring, config defaults.

No external dependencies or real API calls. Fast, no-setup.
"""
from __future__ import annotations

import pytest


# ── Package imports ──────────────────────────────────────────────

class TestPoolImports:
    """Verify all pool modules import cleanly."""

    def test_import_models(self):
        from core.pool.models import Job, JobResult, JobType, JobStatus, JobPriority

    def test_import_queue(self):
        from core.pool.queue import JobQueue

    def test_import_slot(self):
        from core.pool.slot import AgentSlot, AGENT_CONFIGS

    def test_import_pool(self):
        from core.pool.pool import ExecutionPool

    def test_import_package(self):
        from core.pool import ExecutionPool, JobQueue, AgentSlot, Job, JobResult

    def test_import_pool_tools(self):
        from core.tools.pool_tools import POOL_TOOLS, POOL_READ_TOOLS, POOL_WRITE_TOOLS


# ── Model construction ──────────────────────────────────────────

class TestModelConstruction:
    """Verify models can be created with defaults."""

    def test_job_defaults(self):
        from core.pool.models import Job, JobType, JobStatus, JobPriority

        job = Job(job_type=JobType.CODE, instructions="write hello world")
        assert job.job_type == JobType.CODE
        assert job.status == JobStatus.QUEUED
        assert job.priority == JobPriority.NORMAL
        assert job.instructions == "write hello world"
        assert job.id.startswith("job-")
        assert len(job.id) == 12  # "job-" + 8 hex chars

    def test_job_all_types(self):
        from core.pool.models import Job, JobType

        for jt in JobType:
            job = Job(job_type=jt, instructions="test")
            assert job.job_type == jt

    def test_job_all_priorities(self):
        from core.pool.models import Job, JobType, JobPriority

        for jp in JobPriority:
            job = Job(job_type=JobType.CODE, instructions="test", priority=jp)
            assert job.priority == jp

    def test_job_result_defaults(self):
        from core.pool.models import JobResult

        result = JobResult(job_id="job-test1234", success=True)
        assert result.success is True
        assert result.result is None
        assert result.transcript == []

    def test_clarification_needed(self):
        from core.pool.models import ClarificationNeeded

        exc = ClarificationNeeded("What branch?")
        assert exc.question == "What branch?"
        assert str(exc) == "What branch?"

    def test_terminal_statuses(self):
        from core.pool.models import JobStatus, TERMINAL_STATUSES

        assert JobStatus.COMPLETE in TERMINAL_STATUSES
        assert JobStatus.FAILED in TERMINAL_STATUSES
        assert JobStatus.CANCELLED in TERMINAL_STATUSES
        assert JobStatus.RUNNING not in TERMINAL_STATUSES
        assert JobStatus.QUEUED not in TERMINAL_STATUSES

    def test_priority_order(self):
        from core.pool.models import JobPriority, PRIORITY_ORDER

        assert PRIORITY_ORDER[JobPriority.CRITICAL] < PRIORITY_ORDER[JobPriority.HIGH]
        assert PRIORITY_ORDER[JobPriority.HIGH] < PRIORITY_ORDER[JobPriority.NORMAL]
        assert PRIORITY_ORDER[JobPriority.NORMAL] < PRIORITY_ORDER[JobPriority.LOW]
        assert PRIORITY_ORDER[JobPriority.LOW] < PRIORITY_ORDER[JobPriority.BACKGROUND]


# ── AgentSlot config ─────────────────────────────────────────────

class TestAgentSlotConfig:
    """Verify AGENT_CONFIGS maps all job types."""

    def test_all_job_types_have_config(self):
        from core.pool.models import JobType
        from core.pool.slot import AGENT_CONFIGS

        for jt in JobType:
            assert jt in AGENT_CONFIGS, f"Missing config for {jt}"
            assert "allowed_tools" in AGENT_CONFIGS[jt]
            assert "system_prompt" in AGENT_CONFIGS[jt]

    def test_code_has_file_tools(self):
        from core.pool.models import JobType
        from core.pool.slot import AGENT_CONFIGS

        tools = AGENT_CONFIGS[JobType.CODE]["allowed_tools"]
        assert "Read" in tools
        assert "Write" in tools
        assert "Edit" in tools
        assert "Bash" in tools

    def test_research_has_kronos_tools(self):
        from core.pool.models import JobType
        from core.pool.slot import AGENT_CONFIGS

        tools = AGENT_CONFIGS[JobType.RESEARCH]["allowed_tools"]
        assert "mcp__kronos__kronos_search" in tools
        assert "mcp__kronos__kronos_get" in tools
        assert "mcp__kronos__kronos_graph" in tools

    def test_audit_has_read_tools(self):
        from core.pool.models import JobType
        from core.pool.slot import AGENT_CONFIGS

        tools = AGENT_CONFIGS[JobType.AUDIT]["allowed_tools"]
        assert "Read" in tools
        assert "Grep" in tools
        assert "Write" not in tools  # auditors shouldn't write

    def test_slot_construction(self):
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0")
        assert slot.slot_id == "slot-0"
        assert slot.busy is False
        assert slot.current_job_id is None


# ── Config defaults ──────────────────────────────────────────────

class TestConfigDefaults:
    """Verify pool config fields exist with correct defaults."""

    def test_config_pool_fields(self):
        from core.config import GrimConfig

        cfg = GrimConfig()
        assert cfg.pool_enabled is False
        assert cfg.pool_num_slots == 2
        assert cfg.pool_poll_interval == 2.0
        assert cfg.pool_max_turns_per_job == 20
        assert cfg.pool_job_timeout_secs == 900

    def test_config_pool_db_path(self):
        from core.config import GrimConfig
        from pathlib import Path

        cfg = GrimConfig()
        assert cfg.pool_db_path == Path("local/pool.db")


# ── ToolContext ──────────────────────────────────────────────────

class TestToolContext:
    """Verify execution_pool field in ToolContext."""

    def test_tool_context_has_pool_field(self):
        from core.tools.context import ToolContext

        ctx = ToolContext()
        assert ctx.execution_pool is None

    def test_tool_context_configure_pool(self):
        from core.tools.context import ToolContext

        ctx = ToolContext()
        ctx.configure(execution_pool="mock_pool")
        assert ctx.execution_pool == "mock_pool"


# ── Tool registration ────────────────────────────────────────────

class TestToolRegistration:
    """Verify pool tools register with the tool registry."""

    def test_pool_tools_registered(self):
        from core.tools.registry import tool_registry
        import core.tools.pool_tools  # trigger registration

        pool_tools = tool_registry.get_group("pool")
        assert len(pool_tools) == 5

    def test_pool_read_tools_registered(self):
        from core.tools.registry import tool_registry
        import core.tools.pool_tools

        read_tools = tool_registry.get_group("pool_read")
        assert len(read_tools) == 3

    def test_pool_write_tools_registered(self):
        from core.tools.registry import tool_registry
        import core.tools.pool_tools

        write_tools = tool_registry.get_group("pool_write")
        assert len(write_tools) == 2

    def test_pool_tool_names(self):
        from core.tools.pool_tools import POOL_TOOLS

        names = {t.name for t in POOL_TOOLS}
        assert names == {"pool_submit", "pool_status", "pool_job_status", "pool_cancel", "pool_list_jobs"}
