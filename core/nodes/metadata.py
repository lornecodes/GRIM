"""Graph node metadata — UI roster entries for non-agent nodes.

Companion nodes (companion, personal_companion, planning_companion) are
LangGraph nodes, not BaseAgent subclasses. They define NODE_METADATA dicts
with the same schema as BaseAgent.metadata() for the API roster.
"""
from __future__ import annotations

from core.nodes.companion import NODE_METADATA as _COMPANION
from core.nodes.personal_companion import NODE_METADATA as _PERSONAL
from core.nodes.planning_companion import NODE_METADATA as _PLANNING

GRAPH_NODE_METADATA: list[dict] = [_COMPANION, _PERSONAL, _PLANNING]
