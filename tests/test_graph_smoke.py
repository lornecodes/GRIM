"""Smoke tests for GRIM graph compilation."""
import pytest

from core.config import GrimConfig
from core.graph import build_graph


@pytest.mark.smoke
class TestGraphSmoke:
    """Verify graph compiles and has expected structure."""

    def test_graph_compiles_without_mcp(self, grim_config):
        """Graph should compile in debug mode without MCP."""
        graph = build_graph(grim_config, mcp_session=None)
        assert graph is not None

    def test_graph_has_expected_nodes(self, grim_config):
        """Compiled graph should have all 12 nodes."""
        graph = build_graph(grim_config, mcp_session=None)
        nodes = list(graph.get_graph().nodes.keys())
        expected = [
            "identity", "compress", "memory", "skill_match", "router",
            "companion", "dispatch", "audit_gate", "audit",
            "re_dispatch", "integrate", "evolve",
        ]
        for node in expected:
            assert node in nodes, f"Missing node: {node}"

    def test_graph_entry_point(self, grim_config):
        """Graph entry point should be 'identity'."""
        graph = build_graph(grim_config, mcp_session=None)
        g = graph.get_graph()
        # __start__ should connect to identity
        start_edges = [e for e in g.edges if e[0] == "__start__"]
        assert any(e[1] == "identity" for e in start_edges), f"Start edges: {start_edges}"
