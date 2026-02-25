"""
Extract Node — Lightweight concept extraction.

First Claude call per chunk. Cheap (~200 output tokens).
Pulls key concepts and entities that are used to search the vault
before full actualization.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from ..prompts import EXTRACT_SYSTEM, EXTRACT_USER
from ..state import ActualizationState


def extract(state: ActualizationState) -> Dict[str, Any]:
    """
    Extract key concepts from the current chunk.
    Uses a lightweight Claude call to identify searchable terms.
    """
    content = state["current_content"]
    meta = state["current_meta"]
    source_id = state.get("source_id", "unknown")

    # For very short/empty content, skip Claude and extract from structure
    if len(content.strip()) < 50:
        return {
            "concepts": [],
            "entities": [],
        }

    # Truncate for extraction (we only need a preview)
    preview = content[:4000] if len(content) > 4000 else content

    prompt = EXTRACT_USER.format(
        source_id=source_id,
        chunk_path=meta.get("path", "unknown"),
        source_type=meta.get("source_type", "file"),
        content=preview,
    )

    # The graph runner injects the Claude client
    client = state["_client"]
    try:
        resp = client.messages.create(
            model=state["_model"],
            max_tokens=400,
            temperature=0.1,
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        # Track API usage
        api_calls = state.get("api_calls", 0) + 1
        api_input = state.get("api_input_tokens", 0) + resp.usage.input_tokens
        api_output = state.get("api_output_tokens", 0) + resp.usage.output_tokens

        result = _parse_json(text)
        if result:
            return {
                "concepts": result.get("concepts", [])[:10],
                "entities": result.get("entities", [])[:10],
                "api_calls": api_calls,
                "api_input_tokens": api_input,
                "api_output_tokens": api_output,
            }
    except KeyboardInterrupt:
        pass
    except Exception as e:
        errors = list(state.get("errors", []))
        errors.append(f"Extract failed for {meta.get('path')}: {e}")
        return {"concepts": [], "entities": [], "errors": errors}

    # Fallback: extract from markdown structure
    concepts = []
    for m in re.finditer(r'(?:^#+\s+(.+)$|\*\*(.+?)\*\*)', content[:3000], re.MULTILINE):
        c = m.group(1) or m.group(2)
        if c and len(c) > 3:
            concepts.append(c.strip())
    return {"concepts": concepts[:8], "entities": []}


def _parse_json(text: str) -> Dict | None:
    if not text:
        return None
    cleaned = text
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```\w*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
