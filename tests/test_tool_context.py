"""Tests for ToolContext dependency injection."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from core.tools.context import ToolContext, tool_context


class TestToolContext:
    """Test ToolContext operations."""

    def test_defaults_are_none(self):
        ctx = ToolContext()
        assert ctx.mcp_session is None
        assert ctx.ironclaw_bridge is None
        assert ctx.workspace_root is None

    def test_configure_sets_fields(self):
        ctx = ToolContext()
        session = MagicMock()
        bridge = MagicMock()
        ctx.configure(mcp_session=session, ironclaw_bridge=bridge)
        assert ctx.mcp_session is session
        assert ctx.ironclaw_bridge is bridge

    def test_configure_skips_none(self):
        """configure() only sets non-None values, so passing None does not reset."""
        ctx = ToolContext()
        session = MagicMock()
        ctx.configure(mcp_session=session)
        ctx.configure(mcp_session=None)  # should NOT reset
        assert ctx.mcp_session is session

    def test_configure_ignores_unknown_fields(self):
        """Unknown kwargs should be silently ignored (hasattr check)."""
        ctx = ToolContext()
        ctx.configure(nonexistent_field="value")  # should not raise

    def test_mcp_available_false_by_default(self):
        ctx = ToolContext()
        assert ctx.mcp_available is False

    def test_mcp_available_true_when_set(self):
        ctx = ToolContext()
        ctx.mcp_session = MagicMock()
        assert ctx.mcp_available is True

    def test_ironclaw_available_false_by_default(self):
        ctx = ToolContext()
        assert ctx.ironclaw_available is False

    def test_ironclaw_available_true_when_set(self):
        ctx = ToolContext()
        ctx.ironclaw_bridge = MagicMock()
        assert ctx.ironclaw_available is True

    def test_workspace_root_via_configure(self):
        ctx = ToolContext()
        ctx.configure(workspace_root=Path("/tmp/test"))
        assert ctx.workspace_root == Path("/tmp/test")

    def test_configure_multiple_calls_accumulate(self):
        """Multiple configure calls set different fields independently."""
        ctx = ToolContext()
        session = MagicMock()
        bridge = MagicMock()
        ctx.configure(mcp_session=session)
        ctx.configure(ironclaw_bridge=bridge)
        assert ctx.mcp_session is session
        assert ctx.ironclaw_bridge is bridge

    def test_direct_attribute_assignment(self):
        """Fields can be set directly (it's a dataclass)."""
        ctx = ToolContext()
        session = MagicMock()
        ctx.mcp_session = session
        assert ctx.mcp_session is session


class TestToolContextSingleton:
    """Test the module-level singleton."""

    def test_singleton_is_toolcontext(self):
        assert isinstance(tool_context, ToolContext)


class TestBackwardCompatShims:
    """Test that old set_*/get_* functions still work via tool_context."""

    def setup_method(self):
        """Save original state so we can restore after each test."""
        self._orig_mcp = tool_context.mcp_session
        self._orig_bridge = tool_context.ironclaw_bridge
        self._orig_root = tool_context.workspace_root

    def teardown_method(self):
        """Restore original tool_context state."""
        tool_context.mcp_session = self._orig_mcp
        tool_context.ironclaw_bridge = self._orig_bridge
        tool_context.workspace_root = self._orig_root

    def test_set_mcp_session_shim(self):
        from core.tools.kronos_read import set_mcp_session, get_mcp_session
        session = MagicMock()
        set_mcp_session(session)
        assert get_mcp_session() is session
        assert tool_context.mcp_session is session

    def test_set_bridge_shim(self):
        from core.tools.ironclaw_tools import set_bridge
        bridge = MagicMock()
        set_bridge(bridge)
        assert tool_context.ironclaw_bridge is bridge

    def test_set_workspace_root_shim(self):
        from core.tools.workspace import set_workspace_root
        root = Path("/tmp/ws")
        set_workspace_root(root)
        assert tool_context.workspace_root == root
