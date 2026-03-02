"""Tests for ToolRegistry group management."""
import pytest
from unittest.mock import MagicMock
from core.tools.registry import ToolRegistry, tool_registry


class TestToolRegistry:
    """Test ToolRegistry operations."""

    def test_register_and_get(self):
        reg = ToolRegistry()
        t1 = MagicMock(name="tool1")
        t2 = MagicMock(name="tool2")
        reg.register_group("test", [t1, t2])
        result = reg.get_group("test")
        assert len(result) == 2

    def test_get_unknown_group(self):
        reg = ToolRegistry()
        assert reg.get_group("nonexistent") == []

    def test_get_group_returns_copy(self):
        """get_group should return a copy, not the internal list."""
        reg = ToolRegistry()
        t1 = MagicMock(name="tool1")
        reg.register_group("g", [t1])
        result = reg.get_group("g")
        result.append(MagicMock(name="extra"))
        # Internal list should be unaffected
        assert len(reg.get_group("g")) == 1

    def test_for_agent_dedup(self):
        reg = ToolRegistry()
        shared = MagicMock()
        shared.name = "shared_tool"
        t1 = MagicMock()
        t1.name = "tool1"
        t2 = MagicMock()
        t2.name = "tool2"

        reg.register_group("a", [shared, t1])
        reg.register_group("b", [shared, t2])

        resolved = reg.for_agent(["a", "b"])
        names = [t.name for t in resolved]
        assert names == ["shared_tool", "tool1", "tool2"]  # shared appears once

    def test_for_agent_ordering(self):
        """First occurrence wins when deduplicating."""
        reg = ToolRegistry()
        t1 = MagicMock()
        t1.name = "alpha"
        t2 = MagicMock()
        t2.name = "beta"
        t3 = MagicMock()
        t3.name = "alpha"  # duplicate name, different object

        reg.register_group("first", [t1, t2])
        reg.register_group("second", [t3])

        resolved = reg.for_agent(["first", "second"])
        # Should have t1 (from first), not t3 (from second)
        assert len(resolved) == 2
        assert resolved[0] is t1
        assert resolved[1] is t2

    def test_for_agent_empty_groups(self):
        reg = ToolRegistry()
        assert reg.for_agent([]) == []
        assert reg.for_agent(["nonexistent"]) == []

    def test_groups_list(self):
        reg = ToolRegistry()
        reg.register_group("x", [])
        reg.register_group("y", [])
        assert set(reg.groups()) == {"x", "y"}

    def test_repr(self):
        reg = ToolRegistry()
        t1 = MagicMock()
        reg.register_group("g", [t1])
        r = repr(reg)
        assert "1 groups" in r
        assert "1 tools" in r

    def test_register_overwrites(self):
        """Registering same group name overwrites the previous."""
        reg = ToolRegistry()
        reg.register_group("g", [MagicMock()])
        reg.register_group("g", [MagicMock(), MagicMock()])
        assert len(reg.get_group("g")) == 2


class TestToolRegistrySingleton:
    """Test the module-level singleton has expected groups registered."""

    def test_expected_groups_exist(self):
        """All tool modules should have registered their groups."""
        # Force imports to trigger registration
        import core.tools.kronos_read
        import core.tools.kronos_write
        import core.tools.kronos_tasks
        import core.tools.memory_tools
        import core.tools.workspace

        expected = ["kronos_read", "kronos_write", "tasks_read", "tasks_write",
                    "tasks", "memory", "file", "shell", "git"]
        for group in expected:
            tools = tool_registry.get_group(group)
            assert len(tools) > 0, f"Group '{group}' has no tools"

    def test_kronos_read_count(self):
        """kronos_read group: kronos_search, kronos_get, kronos_list."""
        import core.tools.kronos_read
        assert len(tool_registry.get_group("kronos_read")) == 3

    def test_kronos_write_count(self):
        """kronos_write group: kronos_create, kronos_update."""
        import core.tools.kronos_write
        assert len(tool_registry.get_group("kronos_write")) == 2

    def test_tasks_count(self):
        """tasks group: 5 read + 7 write = 12 total."""
        import core.tools.kronos_tasks
        assert len(tool_registry.get_group("tasks")) == 12

    def test_tasks_read_count(self):
        """tasks_read group: board_view, backlog_view, task_get, task_list, calendar_view."""
        import core.tools.kronos_tasks
        assert len(tool_registry.get_group("tasks_read")) == 5

    def test_tasks_write_count(self):
        """tasks_write group: task_create/update/move/archive, calendar_add/update/sync."""
        import core.tools.kronos_tasks
        assert len(tool_registry.get_group("tasks_write")) == 7

    def test_memory_count(self):
        """memory group: read_grim_memory, update_grim_memory."""
        import core.tools.memory_tools
        assert len(tool_registry.get_group("memory")) == 2

    def test_file_count(self):
        """file group: read, write, edit, list_directory, search_files, grep_workspace."""
        import core.tools.workspace
        assert len(tool_registry.get_group("file")) == 6

    def test_shell_count(self):
        """shell group: run_shell."""
        import core.tools.workspace
        assert len(tool_registry.get_group("shell")) == 1

    def test_git_count(self):
        """git group: git_status, git_diff, git_log, git_add_commit."""
        import core.tools.workspace
        assert len(tool_registry.get_group("git")) == 4
