"""Base subgraph wrapper — common logic for all subgraph adapters.

Wraps an existing node function and packages its output into SubgraphOutput
for the Response Generator loop. Extracts the AI response text from the
output messages and populates SubgraphOutput fields.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from core.state import GrimState, Objective, SubgraphOutput

logger = logging.getLogger(__name__)


def extract_response_text(messages: list[Any]) -> str:
    """Extract the final AI response text from a list of output messages.

    Walks the message list backward to find the last AIMessage with
    non-empty text content (skipping tool calls and tool results).
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            # Handle multi-part content (list of dicts)
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                combined = "\n".join(t for t in text_parts if t.strip())
                if combined:
                    return combined
    return ""


def extract_artifacts(messages: list[Any]) -> list[str]:
    """Extract artifact references from tool call results.

    Scans ToolMessages for file paths, FDO IDs, and other artifact indicators.
    """
    artifacts: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = str(msg.content)
            # Simple heuristic — look for file-like references
            if any(ext in content for ext in [".py", ".yaml", ".md", ".json", ".ts"]):
                # Extract paths (very basic — subgraphs can override)
                for word in content.split():
                    if "/" in word and any(ext in word for ext in [".py", ".yaml", ".md"]):
                        artifacts.append(word.strip("\"'`,;"))
    return artifacts[:20]  # cap to prevent bloat


def make_subgraph_wrapper(
    *,
    name: str,
    node_fn: Callable[[GrimState], Coroutine[Any, Any, dict]],
    source_subgraph: str,
    extract_continuation: Callable[[dict, GrimState], dict | None] | None = None,
    extract_objectives: Callable[[dict, GrimState], list[Objective]] | None = None,
) -> Callable[[GrimState], Coroutine[Any, Any, dict]]:
    """Create a subgraph wrapper around an existing node function.

    The wrapper:
      1. Calls the existing node function
      2. Extracts the AI response text from output messages
      3. Packages everything into SubgraphOutput
      4. Returns {"subgraph_output": output.model_dump(), ...original_output}

    Args:
        name: Display name for logging.
        node_fn: The existing node function to wrap.
        source_subgraph: Identifier stored in SubgraphOutput.source_subgraph.
        extract_continuation: Optional function to detect continuation signals.
        extract_objectives: Optional function to extract objective updates.
    """

    async def wrapper(state: GrimState) -> dict:
        # Call the existing node
        result = await node_fn(state)

        # Extract response from output messages
        output_messages = result.get("messages", [])
        response_text = extract_response_text(output_messages)
        artifacts = extract_artifacts(output_messages)

        # Build SubgraphOutput
        continuation = None
        if extract_continuation:
            continuation = extract_continuation(result, state)

        objective_updates: list[Objective] = []
        if extract_objectives:
            objective_updates = extract_objectives(result, state)

        output = SubgraphOutput(
            response=response_text,
            artifacts=artifacts,
            memory_updates={},  # subgraphs can populate later
            objective_updates=objective_updates,
            continuation=continuation,
            source_subgraph=source_subgraph,
        )

        # Merge: keep original result fields, add subgraph_output
        merged = dict(result)
        merged["subgraph_output"] = output.model_dump()

        logger.info(
            "%s subgraph: response=%d chars, artifacts=%d, "
            "continuation=%s, objectives=%d",
            name,
            len(response_text),
            len(artifacts),
            bool(continuation),
            len(objective_updates),
        )

        return merged

    return wrapper
