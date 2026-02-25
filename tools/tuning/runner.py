"""
Runner — Executes isolated agents against test cases.

For each agent, this module:
1. Loads the test cases
2. Loads any mock file content referenced by _load_from
3. Calls the agent function directly with constructed state
4. Collects the output for scoring

No graph traversal — each agent is tested in pure isolation.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from .config import MOCK_REPO, CASES_DIR, MODEL, TEMPERATURE_EVAL


def _load_mock_content(load_from: str) -> str:
    """Load file content from mock repo."""
    path = MOCK_REPO.parent / load_from
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return f"[ERROR: mock file not found: {load_from}]"


def _resolve_case_content(case: Dict) -> Dict:
    """Fill in content from _load_from references."""
    case = dict(case)  # Shallow copy

    # Top-level input content
    inp = case.get("input", {})
    if inp.get("_load_from") and not inp.get("content"):
        inp = dict(inp)
        inp["content"] = _load_mock_content(inp["_load_from"])
        case["input"] = inp

    return case


def _build_input_summary(case: Dict, max_content_chars: int = 600) -> str:
    """Build a compact but informative summary of what the agent receives as input.

    This is shown to the optimizer so it can understand WHY certain outputs are expected.
    Without seeing the input, the optimizer is flying blind.
    """
    inp = case.get("input", {})
    parts = []

    # File path and type — always useful
    path = inp.get("chunk_path") or inp.get("meta", {}).get("path", "")
    if path:
        parts.append(f"PATH: {path}")

    source_type = inp.get("source_type") or inp.get("meta", {}).get("source_type", "")
    if source_type:
        parts.append(f"TYPE: {source_type}")

    # Content preview — the KEY missing context
    content = inp.get("content", "")
    if content:
        # Show enough to understand what the file is about
        preview = content[:max_content_chars]
        if len(content) > max_content_chars:
            preview += f"\n... ({len(content)} chars total)"
        parts.append(f"CONTENT:\n{preview}")
    elif inp.get("_load_from"):
        parts.append(f"LOADED FROM: {inp['_load_from']}")

    # For judge: vault matches
    vault = inp.get("vault_matches")
    if vault:
        parts.append(f"VAULT MATCHES: {len(vault)} existing entries")
        for vm in vault[:3]:
            if isinstance(vm, dict):
                parts.append(f"  - {vm.get('id', '?')}: {vm.get('title', '?')[:60]}")
            else:
                parts.append(f"  - {vm}")

    # For actualize: domain, vault context
    domain = inp.get("domain")
    if domain:
        parts.append(f"DOMAIN: {domain}")

    vault_ctx = inp.get("vault_context")
    if vault_ctx:
        parts.append(f"VAULT CONTEXT: {vault_ctx[:200]}")

    # For validate: the FDO being validated
    fdo = inp.get("fdo_draft")
    if fdo and isinstance(fdo, dict):
        parts.append(f"FDO BEING VALIDATED:")
        parts.append(f"  title: {fdo.get('title', '?')}")
        summary = fdo.get("summary", "")
        if summary:
            parts.append(f"  summary: {summary[:200]}")
        tags = fdo.get("tags", [])
        if tags:
            parts.append(f"  tags: {tags}")

    return "\n".join(parts)


def load_cases(agent: str) -> List[Dict]:
    """Load test cases for an agent."""
    module_name = f"tuning.cases.{agent}_cases"
    # Add tools dir to path if needed
    tools_dir = str(Path(__file__).resolve().parents[1])
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    mod = importlib.import_module(module_name)
    cases = getattr(mod, "CASES", [])

    resolved = []
    for case in cases:
        c = _resolve_case_content(case)
        c["expected"]["_case_id"] = c["id"]
        resolved.append(c)
    return resolved


# =========================================================================
# Agent executors — call each agent in isolation
# =========================================================================

def _make_client():
    """Create Anthropic client."""
    from anthropic import Anthropic

    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return Anthropic(api_key=api_key)


def run_extract(case: Dict, client, model: str = MODEL) -> Dict[str, Any]:
    """Run the extract agent on one test case."""
    from actualization.nodes.extract import extract

    inp = case["input"]
    state = {
        "source_id": inp.get("source_id", "mock-repo"),
        "current_content": inp["content"],
        "current_meta": {
            "path": inp.get("chunk_path", "unknown"),
            "source_type": inp.get("source_type", "file"),
        },
        "_client": client,
        "_model": model,
        "api_calls": 0,
        "api_input_tokens": 0,
        "api_output_tokens": 0,
    }

    result = extract(state)
    return {
        "concepts": result.get("concepts", []),
        "entities": result.get("entities", []),
        "api_calls": result.get("api_calls", 0),
    }


def run_judge(case: Dict, client, model: str = MODEL) -> Dict[str, Any]:
    """Run the judge agent on one test case."""
    from actualization.nodes.judge import judge

    inp = case["input"]
    content = inp.get("content", "")
    if inp.get("_load_from") and not content:
        content = _load_mock_content(inp["_load_from"])

    state = {
        "current_content": content,
        "current_meta": inp.get("meta", {}),
        "vault_matches": inp.get("vault_matches", []),
        "concepts": inp.get("concepts", []),
        "entities": inp.get("entities", []),
        "_client": client,
        "_model": model,
        "api_calls": 0,
        "api_input_tokens": 0,
        "api_output_tokens": 0,
    }

    result = judge(state)
    return {
        "decision": result.get("decision", ""),
        "duplicate_of": result.get("duplicate_of"),
        "extend_target": result.get("extend_target"),
        "skip_reason": result.get("skip_reason"),
        "used_api": result.get("api_calls", 0) > (state.get("api_calls", 0)),
    }


def run_actualize(case: Dict, client, model: str = MODEL) -> Dict[str, Any]:
    """Run the actualize agent on one test case."""
    from actualization.nodes.actualize import actualize

    inp = case["input"]
    content = inp.get("content", "")
    if inp.get("_load_from") and not content:
        content = _load_mock_content(inp["_load_from"])

    state = {
        "source_id": inp.get("source_id", "mock-repo"),
        "source_type": "repo",
        "domain": inp.get("domain", "tools"),
        "current_content": content,
        "current_meta": inp.get("meta", {}),
        "vault_context": inp.get("vault_context", ""),
        "concepts": inp.get("concepts", []),
        "entities": inp.get("entities", []),
        "vault_matches": [],
        "decision": "new",
        "_client": client,
        "_model": model,
        "api_calls": 0,
        "api_input_tokens": 0,
        "api_output_tokens": 0,
    }

    result = actualize(state)
    return {
        "fdo_draft": result.get("fdo_draft"),
        "fdo_id": result.get("fdo_id", ""),
        "api_calls": result.get("api_calls", 0),
    }


def run_validate(case: Dict, client, model: str = MODEL) -> Dict[str, Any]:
    """Run the validate agent on one test case."""
    from actualization.nodes.validate import validate

    inp = case["input"]
    fdo = inp.get("fdo_draft", {})
    original_tags = list(fdo.get("tags", []))  # Save for scoring

    state = {
        "fdo_draft": dict(fdo),  # Copy so we don't mutate case
        "current_content": inp.get("source_content", ""),
        "current_meta": {
            "path": fdo.get("source_path", ""),
            "size": inp.get("source_size", len(inp.get("source_content", ""))),
        },
        "retry_count": 0,
        "validation": {"passed": False, "errors": [], "warnings": [], "fixes_applied": []},
        "_client": client,
        "_model": model,
        "api_calls": 0,
        "api_input_tokens": 0,
        "api_output_tokens": 0,
    }

    result = validate(state)
    result["_original_tags"] = original_tags
    return result


def run_crosslink(case: Dict, client, model: str = MODEL) -> Dict[str, Any]:
    """Run the crosslink agent on one test case."""
    from actualization.nodes.crosslink import crosslink

    inp = case["input"]
    state = {
        "fdo_draft": inp.get("fdo_draft", {}),
        "fdo_id": inp.get("fdo_draft", {}).get("id", ""),
        "vault_matches": inp.get("vault_matches", []),
        "_client": client,
        "_model": model,
        "api_calls": 0,
        "api_input_tokens": 0,
        "api_output_tokens": 0,
    }

    result = crosslink(state)
    return {
        "cross_links": result.get("cross_links", []),
        "api_calls": result.get("api_calls", 0),
    }


# =========================================================================
# Dispatcher
# =========================================================================

RUNNERS = {
    "extract": run_extract,
    "judge": run_judge,
    "actualize": run_actualize,
    "validate": run_validate,
    "crosslink": run_crosslink,
}


def run_agent_suite(agent: str, client=None, model: str = MODEL) -> List[Dict]:
    """
    Run all test cases for an agent.
    Returns list of {case_id, input, expected, actual, score_result}.
    """
    # Force-reload prompts from disk so we always test the latest version.
    # Without this, Python's module cache serves stale prompt strings
    # even after the optimizer has written new ones to prompts.py.
    from .optimizer import reload_prompts
    reload_prompts()

    if client is None:
        client = _make_client()

    cases = load_cases(agent)
    runner = RUNNERS[agent]
    from .evaluator import SCORERS
    scorer = SCORERS[agent]

    results = []
    for case in cases:
        # Build a human-readable input summary for the optimizer meta-prompt
        input_summary = _build_input_summary(case)
        golden = case.get("golden_output")
        try:
            actual = runner(case, client, model)
            score = scorer(actual, case["expected"])
            results.append({
                "case_id": case["id"],
                "description": case.get("description", ""),
                "weight": case.get("weight", 1.0),
                "actual": actual,
                "expected": case["expected"],
                "golden_output": golden,
                "score": score,
                "error": None,
                "_input_summary": input_summary,
            })
        except Exception as e:
            results.append({
                "case_id": case["id"],
                "description": case.get("description", ""),
                "weight": case.get("weight", 1.0),
                "actual": None,
                "expected": case["expected"],
                "golden_output": golden,
                "score": None,
                "error": str(e),
                "_input_summary": input_summary,
            })

    return results
