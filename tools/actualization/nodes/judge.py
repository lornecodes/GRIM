"""
Judge Node — Decide what to do with this chunk.

Key decision point in the graph. Determines whether to:
- "new"       → actualize into a new FDO
- "duplicate" → link to existing, don't create
- "extend"    → add to an existing FDO
- "skip"      → ignore (trivial, empty, boilerplate)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from ..prompts import JUDGE_SYSTEM, JUDGE_USER
from ..state import ActualizationState

# Files that are always skipped without asking Claude
AUTO_SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "Cargo.lock", "poetry.lock",
    ".gitignore", ".gitattributes", ".editorconfig", ".prettierrc",
    "Thumbs.db", ".DS_Store",
}

# Extensions that are always skipped
AUTO_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
}


def judge(state: ActualizationState) -> Dict[str, Any]:
    """
    Decide: new, duplicate, extend, or skip.

    Uses heuristics first (cheap), then Claude for ambiguous cases.
    """
    content = state["current_content"]
    meta = state["current_meta"]
    path = meta.get("path", "")
    vault_matches = state.get("vault_matches", [])

    # ---- Fast heuristic skips (no Claude call needed) ----

    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    extension = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""

    if filename in AUTO_SKIP_NAMES:
        return {
            "decision": "skip",
            "skip_reason": f"Auto-skip: {filename} is boilerplate",
        }

    if extension in AUTO_SKIP_EXTENSIONS:
        return {
            "decision": "skip",
            "skip_reason": f"Auto-skip: {extension} binary/asset",
        }

    # Empty or near-empty files
    stripped = content.strip()
    if len(stripped) < 20:
        return {
            "decision": "skip",
            "skip_reason": "Empty or near-empty file",
        }

    # Very high confidence vault match = likely duplicate
    if vault_matches:
        top = vault_matches[0]
        if top.get("match_score", 0) > 2.5:
            return {
                "decision": "duplicate",
                "duplicate_of": top["id"],
                "skip_reason": None,
            }

    # ---- Claude judgment for ambiguous cases ----

    vault_context = state.get("vault_context", "No matches found.")
    preview = content[:2000] if len(content) > 2000 else content

    prompt = JUDGE_USER.format(
        source_id=state.get("source_id", "unknown"),
        chunk_path=path,
        content_preview=preview,
        vault_context=vault_context,
    )

    client = state["_client"]
    try:
        resp = client.messages.create(
            model=state["_model"],
            max_tokens=300,
            temperature=0.1,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        api_calls = state.get("api_calls", 0) + 1
        api_input = state.get("api_input_tokens", 0) + resp.usage.input_tokens
        api_output = state.get("api_output_tokens", 0) + resp.usage.output_tokens

        result = _parse_json(text)
        if result:
            decision = result.get("decision", "new")
            if decision not in ("new", "duplicate", "extend", "skip"):
                decision = "new"

            update: Dict[str, Any] = {
                "decision": decision,
                "api_calls": api_calls,
                "api_input_tokens": api_input,
                "api_output_tokens": api_output,
            }

            if decision == "duplicate":
                target = result.get("target_id")
                # Verify the target actually exists in our index
                vault_index = state["_vault_index"]
                if target and vault_index.has(target):
                    update["duplicate_of"] = target
                else:
                    # Target doesn't exist — fall back to new
                    update["decision"] = "new"
                    update["duplicate_of"] = None

            elif decision == "extend":
                target = result.get("target_id")
                vault_index = state["_vault_index"]
                if target and vault_index.has(target):
                    update["extend_target"] = target
                else:
                    update["decision"] = "new"
                    update["extend_target"] = None

            elif decision == "skip":
                update["skip_reason"] = result.get("reasoning", "Judged as trivial")

            return update

    except KeyboardInterrupt:
        return {"decision": "skip", "skip_reason": "Interrupted"}
    except Exception as e:
        errors = list(state.get("errors", []))
        errors.append(f"Judge failed for {path}: {e}")
        # Default to "new" on failure — better to over-create than miss
        return {"decision": "new", "errors": errors}

    return {"decision": "new"}


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
