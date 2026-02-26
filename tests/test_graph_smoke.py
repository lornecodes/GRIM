"""Smoke test for GRIM graph compilation."""
from core.config import load_config
from core.graph import build_graph

cfg = load_config()
# Build without MCP (debug mode)
graph = build_graph(cfg, mcp_session=None)
print(f"Graph compiled: {type(graph).__name__}")
print(f"Graph nodes: {list(graph.get_graph().nodes.keys())}")
print("OK")
