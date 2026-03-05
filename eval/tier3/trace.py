"""TraceParser — extract structured data from WebSocket trace events.

Parses the raw event stream from GrimLiveClient into structured
routing paths, metrics, tool usage, and timing breakdowns.

Event format (from GRIM WebSocket handler):
  Node: {"type":"trace", "cat":"node", "node":"identity", "action":"start|end", "ms":N, "duration_ms":N}
  LLM:  {"type":"trace", "cat":"llm", "action":"start|end", "ms":N, "detail":{"tokens":{...}}}
  Tool: {"type":"trace", "cat":"tool", "action":"start|end", "text":"Tool: kronos_search", "ms":N}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from eval.schema import Tier3Metrics

logger = logging.getLogger(__name__)

# Cost per 1M tokens (approximate, for estimation)
_COST_PER_1M = {
    "input": 3.00,   # sonnet default
    "output": 15.00,
}

# Maps v0.0.6 node names → subgraph categories
_NODE_TO_SUBGRAPH = {
    # v0.10 subgraph names
    "conversation": "conversation",
    "planning": "planning",
    "research": "research",
    "code": "code",
    # v0.0.6 node names
    "personal_companion": "conversation",
    "companion": "conversation",
    "planning_companion": "planning",
    "dispatch": "research",
}


@dataclass
class ParsedTrace:
    """Structured data extracted from a turn's WS events."""

    routing_path: list[str] = field(default_factory=list)
    subgraph: str | None = None
    delegation_type: str | None = None
    selected_model: str | None = None
    loop_count: int = 0
    tools_called: list[str] = field(default_factory=list)
    objectives_created: int = 0
    objectives_completed: int = 0
    metrics: Tier3Metrics = field(default_factory=Tier3Metrics)


class TraceParser:
    """Parse WebSocket trace events into structured trace data."""

    @staticmethod
    def parse(events: list[dict[str, Any]]) -> ParsedTrace:
        """Parse a list of WS events from one turn into a ParsedTrace."""
        result = ParsedTrace()

        total_input = 0
        total_output = 0
        token_by_node: dict[str, int] = {}
        node_times: dict[str, int] = {}
        current_node: str | None = None
        llm_calls = 0
        tool_calls = 0
        all_tools: list[str] = []
        traversal: list[str] = []
        max_ms = 0

        for event in events:
            evt_type = event.get("type", "")
            ms = event.get("ms", 0)
            if isinstance(ms, (int, float)):
                max_ms = max(max_ms, int(ms))

            if evt_type == "trace":
                cat = event.get("cat", "")
                action = event.get("action", "")
                detail = event.get("detail") or {}

                # Node lifecycle — use the "node" field directly
                if cat == "node":
                    node_name = event.get("node") or _extract_node_name(event.get("text", ""))

                    if action == "start" and node_name:
                        current_node = node_name
                        traversal.append(node_name)
                        result.routing_path.append(node_name)

                    elif action == "end" and node_name:
                        # Duration from the event
                        duration = event.get("duration_ms", 0)
                        if duration:
                            node_times[node_name] = node_times.get(node_name, 0) + duration

                        # Detect subgraph from node name
                        if node_name in _NODE_TO_SUBGRAPH:
                            result.subgraph = _NODE_TO_SUBGRAPH[node_name]

                        # Detect response_generator for loop counting
                        if node_name == "response_generator":
                            result.loop_count += 1

                # LLM lifecycle — tokens in detail.tokens
                elif cat == "llm":
                    if action == "start":
                        llm_calls += 1
                    elif action == "end":
                        # Tokens are in detail.tokens
                        tokens = detail.get("tokens") if isinstance(detail, dict) else None
                        if tokens and isinstance(tokens, dict):
                            inp = tokens.get("input_tokens", 0)
                            out = tokens.get("output_tokens", 0)
                            total_input += inp
                            total_output += out
                            if current_node:
                                token_by_node[current_node] = (
                                    token_by_node.get(current_node, 0) + inp + out
                                )

                        # Tool calls from detail
                        tool_names = detail.get("tool_calls", []) if isinstance(detail, dict) else []
                        if not tool_names:
                            # Also check top-level (some event formats)
                            tool_names = event.get("tool_calls", [])
                        if tool_names:
                            tool_calls += len(tool_names)
                            all_tools.extend(tool_names)

                # Tool lifecycle
                elif cat == "tool":
                    if action == "start":
                        tool_name = _extract_tool_name(event.get("text", ""))
                        if tool_name:
                            all_tools.append(tool_name)
                            tool_calls += 1

                # Routing info
                elif cat == "routing":
                    text = event.get("text", "").lower()
                    if "delegation" in text:
                        result.delegation_type = event.get("delegation_type")
                    if "model" in text:
                        result.selected_model = event.get("model")

                # Graph lifecycle
                elif cat == "graph":
                    duration = event.get("duration_ms")
                    if duration and action != "start":
                        max_ms = max(max_ms, duration)

            elif evt_type == "response":
                meta = event.get("meta", {})
                if isinstance(meta, dict):
                    if meta.get("subgraph"):
                        result.subgraph = meta["subgraph"]
                    if meta.get("mode"):
                        # v0.0.6 mode field — map to subgraph
                        mode = meta["mode"]
                        if mode == "companion" and not result.subgraph:
                            result.subgraph = "conversation"

        # Build metrics
        total_tokens = total_input + total_output
        cost = (total_input * _COST_PER_1M["input"] + total_output * _COST_PER_1M["output"]) / 1_000_000

        result.tools_called = all_tools
        result.metrics = Tier3Metrics(
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            token_breakdown=token_by_node,
            wall_time_ms=max_ms,
            node_times=node_times,
            turns=result.loop_count,
            agent_traversal=traversal,
            tool_call_count=tool_calls,
            llm_call_count=llm_calls,
            cost_estimate_usd=round(cost, 6),
        )

        return result


def _extract_node_name(text: str) -> str | None:
    """Extract node name from trace text.

    Handles formats:
      "→ identity"       (start)
      "✓ identity (7ms)" (end)
      "Node started: identity"
    """
    if not text:
        return None
    # Strip unicode arrows/checks
    cleaned = text.lstrip("→✓ ").strip()
    # Remove duration suffix like "(7ms)"
    if "(" in cleaned:
        cleaned = cleaned[:cleaned.index("(")].strip()
    # Handle "Node started: identity" format
    if ":" in cleaned:
        cleaned = cleaned.split(":")[-1].strip()
    return cleaned if cleaned else None


def _extract_tool_name(text: str) -> str | None:
    """Extract tool name from trace text like 'Tool: kronos_search'."""
    if not text:
        return None
    if ":" in text:
        return text.split(":")[-1].strip()
    return None
