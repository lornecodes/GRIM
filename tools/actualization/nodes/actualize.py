"""
Actualize Node — Full FDO generation from content + vault context.

The main creative step. Claude receives the file content plus
existing vault matches and produces a complete FDO.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from ..prompts import ACTUALIZE_SYSTEM, ACTUALIZE_USER, ACTUALIZE_DIRECTORY_USER, EXTEND_USER
from ..state import ActualizationState

MAX_CONTENT_CHARS = 12_000


def slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')[:80]


def make_fdo_id(source_id: str, chunk_path: str) -> str:
    """
    Generate a unique, path-aware FDO ID.

    Fixes the ID collision bug: meta.yaml in different dirs
    now gets different IDs.

    Examples:
        ("myrepo", "src/main.py")       → "myrepo-src-main"
        ("myrepo", "docs/meta.yaml")    → "myrepo-docs-meta"
        ("myrepo", "meta.yaml")         → "myrepo-meta"
        ("myrepo", ".cip/core.yaml")    → "myrepo-cip-core"
    """
    p = Path(chunk_path)
    # Include parent dir(s) in the slug to disambiguate
    parts = list(p.parts)
    # Strip leading dots from dir names (e.g. ".cip" → "cip")
    parts = [part.lstrip('.') for part in parts]
    # Remove extension from filename
    parts[-1] = p.stem.lstrip('.')

    # Build slug from source + path parts
    slug_parts = [source_id] + [p for p in parts if p]
    raw = "-".join(slug_parts)
    return slugify(raw)


def make_pac_parent_id(source_id: str, chunk_path: str) -> str:
    """Compute the PAC parent ID (the directory containing this file)."""
    p = Path(chunk_path)
    parent = p.parent
    if str(parent) == ".":
        return slugify(source_id)
    parts = [part.lstrip('.') for part in parent.parts]
    slug_parts = [source_id] + [p for p in parts if p]
    return slugify("-".join(slug_parts))


def estimate_confidence(path: str, content: str) -> float:
    """Estimate confidence level based on file type."""
    name = Path(path).name.lower()
    suffix = Path(path).suffix.lower()
    if "preprint" in path:
        return 0.7
    if ".spec.md" in name or "SPEC" in Path(path).name:
        return 0.6
    if name in ("readme.md", "architecture.md", "design.md"):
        return 0.6
    if name.startswith("test_") or name.startswith("exp_"):
        return 0.5
    if suffix in (".yaml", ".yml", ".toml", ".json"):
        return 0.4
    if suffix in (".py", ".rs", ".ts", ".js"):
        return 0.4
    return 0.3


def actualize(state: ActualizationState) -> Dict[str, Any]:
    """Produce a full FDO from the current chunk."""
    content = state["current_content"]
    meta = state["current_meta"]
    path = meta.get("path", "")
    source_id = state.get("source_id", "unknown")
    domain = state.get("domain", "tools")
    vault_context = state.get("vault_context", "No matches.")

    fdo_id = make_fdo_id(source_id, path)
    pac_parent = make_pac_parent_id(source_id, path)

    # Truncate content for Claude
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n\n... [truncated]"

    siblings = meta.get("siblings", [])
    siblings_str = ", ".join(siblings[:15]) if siblings else "none"

    prompt = ACTUALIZE_USER.format(
        source_id=source_id,
        chunk_path=path,
        domain=domain,
        fdo_id=fdo_id,
        pac_parent=pac_parent,
        content=content,
        vault_context=vault_context,
        siblings=siblings_str,
    )

    client = state["_client"]
    try:
        resp = client.messages.create(
            model=state["_model"],
            max_tokens=2000,
            temperature=0.3,
            system=ACTUALIZE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        api_calls = state.get("api_calls", 0) + 1
        api_input = state.get("api_input_tokens", 0) + resp.usage.input_tokens
        api_output = state.get("api_output_tokens", 0) + resp.usage.output_tokens

        result = _parse_json(text)
        if result:
            confidence = estimate_confidence(path, content)
            existing_links = result.get("related_ids", [])

            # Build related list from concepts + existing vault links
            vault_index = state["_vault_index"]
            related = list(set(
                [slugify(c) for c in result.get("tags", [])[:5]] +
                [eid for eid in existing_links if vault_index.has(eid)]
            ))

            fdo_draft = {
                "id": fdo_id,
                "title": result.get("title", Path(path).stem.replace("-", " ").title()),
                "domain": domain,
                "summary": result.get("summary", ""),
                "details": result.get("details", ""),
                "connections": result.get("connections", ""),
                "open_questions": result.get("open_questions", ""),
                "references": f"- Source: `{path}` in [{source_id}]",
                "status": "seed",
                "confidence": confidence,
                "related": related,
                "tags": result.get("tags", []),
                "pac_parent": pac_parent,
                "pac_children": [],
                "source_path": path,
                "source_repos": [source_id],
            }

            return {
                "fdo_draft": fdo_draft,
                "fdo_id": fdo_id,
                "api_calls": api_calls,
                "api_input_tokens": api_input,
                "api_output_tokens": api_output,
            }

    except KeyboardInterrupt:
        pass
    except Exception as e:
        errors = list(state.get("errors", []))
        errors.append(f"Actualize failed for {path}: {e}")
        return {"fdo_draft": None, "fdo_id": fdo_id, "errors": errors}

    # Fallback FDO
    fdo_draft = {
        "id": fdo_id,
        "title": Path(path).stem.replace("-", " ").replace("_", " ").title(),
        "domain": domain,
        "summary": f"Auto-ingested from {path}. Claude call failed.",
        "details": content[:500],
        "connections": "",
        "open_questions": "",
        "references": f"- Source: `{path}` in [{source_id}]",
        "status": "seed",
        "confidence": 0.2,
        "related": [],
        "tags": ["needs-review"],
        "pac_parent": pac_parent,
        "pac_children": [],
        "source_path": path,
        "source_repos": [source_id],
    }
    return {"fdo_draft": fdo_draft, "fdo_id": fdo_id}


def actualize_directory(
    source_id: str,
    dir_path: str,
    domain: str,
    child_summaries: List[Dict[str, str]],
    vault_context: str,
    client,
    model: str,
) -> Dict[str, Any]:
    """
    Synthesize a PAC parent FDO from its children.
    Called outside the main graph loop after all files are processed.
    """
    fdo_id = make_fdo_id(source_id, dir_path + "/_index") if dir_path != "." else slugify(source_id)
    children_desc = "\n".join(
        f"- **{c['name']}**: {c['summary'][:200]}"
        for c in child_summaries[:20]
    )

    prompt = ACTUALIZE_DIRECTORY_USER.format(
        dir_path=dir_path,
        source_id=source_id,
        domain=domain,
        fdo_id=fdo_id,
        children_desc=children_desc,
        vault_context=vault_context or "N/A",
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            temperature=0.3,
            system=ACTUALIZE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        result = _parse_json(text)
        if result:
            return {
                "title": result.get("title", ""),
                "summary": result.get("summary", ""),
                "details": result.get("details", ""),
                "connections": result.get("connections", ""),
                "tags": result.get("tags", []),
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
    except Exception:
        pass

    dir_name = Path(dir_path).name if dir_path != "." else source_id
    return {
        "title": dir_name.replace("-", " ").replace("_", " ").title(),
        "summary": f"Directory containing {len(child_summaries)} items.",
        "details": children_desc,
        "connections": "",
        "tags": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }


def extend_existing(state: ActualizationState) -> Dict[str, Any]:
    """
    Extend an existing FDO with new information from this chunk.
    Used when judge decides 'extend'.
    """
    content = state["current_content"]
    meta = state["current_meta"]
    path = meta.get("path", "")
    extend_target = state.get("extend_target")
    vault_index = state["_vault_index"]

    if not extend_target or not vault_index.has(extend_target):
        return {"decision": "new"}  # Fallback

    existing = vault_index.get(extend_target)
    existing_json = json.dumps(existing, indent=2)

    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n\n... [truncated]"

    prompt = EXTEND_USER.format(
        existing_fdo=existing_json,
        source_path=path,
        content=content,
    )

    client = state["_client"]
    try:
        resp = client.messages.create(
            model=state["_model"],
            max_tokens=1000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        api_calls = state.get("api_calls", 0) + 1
        api_input = state.get("api_input_tokens", 0) + resp.usage.input_tokens
        api_output = state.get("api_output_tokens", 0) + resp.usage.output_tokens

        result = _parse_json(text)
        if result:
            # Apply the extension via CrossLinker
            writer = state["_crosslinker"]
            target_path = existing.get("path", "")
            writer.extend_fdo(target_path, result, f"`{path}` in [{state.get('source_id')}]")

            fdos_linked = list(state.get("fdos_linked", []))
            fdos_linked.append(extend_target)

            return {
                "decision": "extend",
                "fdos_linked": fdos_linked,
                "api_calls": api_calls,
                "api_input_tokens": api_input,
                "api_output_tokens": api_output,
            }
    except Exception as e:
        errors = list(state.get("errors", []))
        errors.append(f"Extend failed for {path}: {e}")
        return {"errors": errors}

    return {}


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
