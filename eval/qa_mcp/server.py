"""QA MCP Server — evaluation tools for Claude Code and eval framework.

A separate stdio MCP server that provides eval/QA tools.
Accessible to Claude Code and the eval framework, NOT to GRIM itself.

Run: python -m eval.qa_mcp.server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add GRIM root to path for imports
GRIM_ROOT = Path(__file__).parent.parent.parent
if str(GRIM_ROOT) not in sys.path:
    sys.path.insert(0, str(GRIM_ROOT))

from mcp.server import Server
from mcp.types import TextContent, Tool

from eval.config import EvalConfig
from eval.schema import Tier3CaseResult
from eval.tier3.executor import Tier3Executor
from eval.tier3.ground_truth import GroundTruthLoader
from eval.tier3.judges import create_default_judges
from eval.tier3.trace import TraceParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="qa_run_tier3",
        description="Run Tier 3 live integration eval cases against a running GRIM server. "
                    "Optionally filter by category or specific case IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category (conversation, research, planning, code, task_switching, domain_accuracy)",
                },
                "case_id": {
                    "type": "string",
                    "description": "Run a specific case by ID",
                },
            },
        },
    ),
    Tool(
        name="qa_list_cases",
        description="List available Tier 3 test cases. Optionally filter by category.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category",
                },
            },
        },
    ),
    Tool(
        name="qa_load_ground_truth",
        description="Load ground truth facts from Kronos vault FDOs for domain accuracy verification.",
        inputSchema={
            "type": "object",
            "properties": {
                "fdo_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of FDO IDs to load facts from",
                },
            },
            "required": ["fdo_ids"],
        },
    ),
    Tool(
        name="qa_inspect_trace",
        description="Parse and inspect a raw WebSocket trace. Extracts routing path, metrics, tools called.",
        inputSchema={
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "description": "Raw WebSocket events from a GRIM session",
                },
            },
            "required": ["events"],
        },
    ),
    Tool(
        name="qa_results_summary",
        description="Get a summary of Tier 3 eval results from the most recent run.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

_config: EvalConfig | None = None
_executor: Tier3Executor | None = None
_ground_truth: GroundTruthLoader | None = None
_last_results: list[Tier3CaseResult] = []


def _get_config() -> EvalConfig:
    global _config
    if _config is None:
        _config = EvalConfig.from_env()
    return _config


def _get_executor() -> Tier3Executor:
    global _executor
    if _executor is None:
        config = _get_config()
        judges = create_default_judges(
            model=config.tier3_judge_model,
            ground_truth_loader=_get_ground_truth(),
        )
        _executor = Tier3Executor(config=config, judges=judges)
    return _executor


def _get_ground_truth() -> GroundTruthLoader | None:
    global _ground_truth
    if _ground_truth is None:
        config = _get_config()
        vault_path = config.ground_truth_vault_path
        if vault_path is None:
            # Default: look for kronos-vault relative to workspace
            workspace = GRIM_ROOT.parent
            vault_path = workspace / "kronos-vault"
        if vault_path.exists():
            _ground_truth = GroundTruthLoader(vault_path)
        else:
            logger.warning("Vault not found at %s", vault_path)
    return _ground_truth


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


async def handle_qa_run_tier3(args: dict[str, Any]) -> str:
    """Run Tier 3 eval cases."""
    global _last_results
    executor = _get_executor()

    categories = [args["category"]] if args.get("category") else None
    case_ids = [args["case_id"]] if args.get("case_id") else None

    results = await executor.run(categories=categories, case_ids=case_ids)
    _last_results = results

    # Build summary
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    summary = {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": f"{passed/total*100:.1f}%" if total > 0 else "N/A",
        "results": [],
    }

    for r in results:
        result_summary = {
            "case_id": r.case_id,
            "category": r.category,
            "passed": r.passed,
            "score": round(r.overall_score, 3),
            "duration_ms": r.duration_ms,
            "judgments": [
                {"judge": j.judge, "score": round(j.score, 3), "passed": j.passed}
                for j in r.judgments
            ],
        }
        if r.error:
            result_summary["error"] = r.error
        if r.metrics:
            result_summary["metrics"] = {
                "total_tokens": r.metrics.total_tokens,
                "wall_time_ms": r.metrics.wall_time_ms,
                "loops": r.metrics.turns,
                "llm_calls": r.metrics.llm_call_count,
                "tool_calls": r.metrics.tool_call_count,
                "cost_usd": r.metrics.cost_estimate_usd,
            }
        summary["results"].append(result_summary)

    return json.dumps(summary, indent=2)


async def handle_qa_list_cases(args: dict[str, Any]) -> str:
    """List available Tier 3 test cases."""
    executor = _get_executor()
    categories = [args["category"]] if args.get("category") else None
    datasets = executor.load_datasets(categories)

    cases = []
    for ds in datasets.values():
        for case in ds.cases:
            cases.append({
                "id": case.id,
                "category": case.category.value,
                "description": case.description,
                "turns": len(case.turns),
                "tags": case.tags,
            })

    return json.dumps({"total": len(cases), "cases": cases}, indent=2)


async def handle_qa_load_ground_truth(args: dict[str, Any]) -> str:
    """Load ground truth from vault FDOs."""
    loader = _get_ground_truth()
    if loader is None:
        return json.dumps({"error": "Vault not configured"})

    fdo_ids = args.get("fdo_ids", [])
    facts = loader.load_fdos(fdo_ids)

    result = {}
    for fdo_id, fdo_facts in facts.items():
        result[fdo_id] = {
            "title": fdo_facts.title,
            "domain": fdo_facts.domain,
            "confidence": fdo_facts.confidence,
            "summary": fdo_facts.summary[:500],
            "key_facts": fdo_facts.key_facts[:10],
            "tags": fdo_facts.tags,
        }

    return json.dumps(result, indent=2)


async def handle_qa_inspect_trace(args: dict[str, Any]) -> str:
    """Parse and inspect a raw trace."""
    events = args.get("events", [])
    parsed = TraceParser.parse(events)

    return json.dumps({
        "routing_path": parsed.routing_path,
        "subgraph": parsed.subgraph,
        "delegation_type": parsed.delegation_type,
        "loop_count": parsed.loop_count,
        "tools_called": parsed.tools_called,
        "metrics": {
            "total_tokens": parsed.metrics.total_tokens,
            "wall_time_ms": parsed.metrics.wall_time_ms,
            "turns": parsed.metrics.turns,
            "llm_calls": parsed.metrics.llm_call_count,
            "tool_calls": parsed.metrics.tool_call_count,
            "agent_traversal": parsed.metrics.agent_traversal,
            "cost_usd": parsed.metrics.cost_estimate_usd,
        },
    }, indent=2)


async def handle_qa_results_summary(args: dict[str, Any]) -> str:
    """Get summary of last run."""
    if not _last_results:
        return json.dumps({"message": "No results yet. Run qa_run_tier3 first."})

    by_category: dict[str, list[dict]] = {}
    for r in _last_results:
        if r.category not in by_category:
            by_category[r.category] = []
        by_category[r.category].append({
            "case_id": r.case_id,
            "passed": r.passed,
            "score": round(r.overall_score, 3),
        })

    summary = {
        "total": len(_last_results),
        "passed": sum(1 for r in _last_results if r.passed),
        "by_category": {
            cat: {
                "total": len(cases),
                "passed": sum(1 for c in cases if c["passed"]),
                "avg_score": round(sum(c["score"] for c in cases) / len(cases), 3),
            }
            for cat, cases in by_category.items()
        },
    }

    return json.dumps(summary, indent=2)


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------

HANDLERS = {
    "qa_run_tier3": handle_qa_run_tier3,
    "qa_list_cases": handle_qa_list_cases,
    "qa_load_ground_truth": handle_qa_load_ground_truth,
    "qa_inspect_trace": handle_qa_inspect_trace,
    "qa_results_summary": handle_qa_results_summary,
}


def create_server() -> Server:
    """Create and configure the QA MCP server."""
    server = Server("grim-qa")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handler = HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

        try:
            result = await handler(arguments or {})
            return [TextContent(type="text", text=result)]
        except Exception as exc:
            logger.exception("QA tool %s failed", name)
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    return server


async def main() -> None:
    """Run the QA MCP server via stdio."""
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
