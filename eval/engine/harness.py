"""EvalToolHarness — mock tools that record calls and return canned responses.

Used by Tier 2 evaluators to instrument agent tool usage without
hitting real services. Tracks call sequence for assertion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, model_validator


@dataclass
class ToolCall:
    """Record of a single tool invocation."""

    name: str
    args: dict[str, Any]
    result: Any = None


def _make_mock_tool_class(
    tool_name: str, response: Any, harness: EvalToolHarness
) -> type[BaseTool]:
    """Dynamically create a BaseTool subclass for a mock tool."""

    class MockTool(BaseTool):
        model_config = ConfigDict(extra="allow")

        @model_validator(mode="before")
        @classmethod
        def _set_defaults(cls, values: Any) -> Any:
            if isinstance(values, dict):
                values.setdefault("name", tool_name)
                values.setdefault("description", f"Mock tool: {tool_name}")
            return values

        def _run(self, **kwargs: Any) -> str:
            call = ToolCall(name=tool_name, args=kwargs, result=response)
            harness.calls.append(call)
            if isinstance(response, str):
                return response
            return json.dumps(response)

    MockTool.__name__ = f"MockTool_{tool_name}"
    MockTool.__qualname__ = f"MockTool_{tool_name}"
    return MockTool


class EvalToolHarness:
    """Mock tool harness for evaluation.

    Creates LangChain BaseTool instances that record calls
    and return pre-configured responses.

    Usage:
        harness = EvalToolHarness({
            "kronos_search": {"results": []},
            "kronos_create": {"success": True, "id": "test-fdo"},
        })
        tools = harness.make_tools()
        # ... bind to agent ...
        assert harness.was_called("kronos_search")
        assert harness.call_sequence() == ["kronos_search", "kronos_create"]
    """

    def __init__(self, mock_responses: dict[str, Any] | None = None) -> None:
        self.mock_responses = mock_responses or {}
        self.calls: list[ToolCall] = []

    def make_tools(self) -> list[BaseTool]:
        """Create mock LangChain tools from the response map."""
        tools = []
        for name, response in self.mock_responses.items():
            cls = _make_mock_tool_class(name, response, self)
            tools.append(cls())
        return tools

    def was_called(self, tool_name: str) -> bool:
        """Check if a tool was called at least once."""
        return any(c.name == tool_name for c in self.calls)

    def call_count(self, tool_name: str) -> int:
        """Count how many times a tool was called."""
        return sum(1 for c in self.calls if c.name == tool_name)

    def call_sequence(self) -> list[str]:
        """Return the ordered list of tool names called."""
        return [c.name for c in self.calls]

    def calls_for(self, tool_name: str) -> list[ToolCall]:
        """Get all calls for a specific tool."""
        return [c for c in self.calls if c.name == tool_name]

    def last_call(self, tool_name: str) -> ToolCall | None:
        """Get the most recent call for a tool."""
        calls = self.calls_for(tool_name)
        return calls[-1] if calls else None

    def reset(self) -> None:
        """Clear all recorded calls."""
        self.calls.clear()

    def assert_tools_called(self, expected: list[str]) -> list[str]:
        """Check expected tools were called. Returns list of failures."""
        failures = []
        actual = set(self.call_sequence())
        for tool_name in expected:
            if tool_name not in actual:
                failures.append(f"Expected tool '{tool_name}' was not called")
        return failures

    def assert_call_order(self, expected_order: list[str]) -> list[str]:
        """Check tools were called in the expected order. Returns failures."""
        failures = []
        sequence = self.call_sequence()
        last_idx = -1
        for tool_name in expected_order:
            try:
                idx = sequence.index(tool_name, last_idx + 1)
                last_idx = idx
            except ValueError:
                failures.append(
                    f"Expected '{tool_name}' after position {last_idx} "
                    f"but not found in sequence: {sequence}"
                )
        return failures
