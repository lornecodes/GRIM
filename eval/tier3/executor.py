"""Tier3Executor — runs Tier 3 live integration test cases.

Loads Tier3Cases from YAML datasets, executes them via GrimLiveClient,
parses traces, invokes judges, and produces Tier3CaseResults.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import yaml

from eval.config import EvalConfig
from eval.schema import (
    Tier3Case,
    Tier3CaseResult,
    Tier3Dataset,
    Tier3Judgment,
    Tier3TurnResult,
)
from eval.tier3.client import GrimLiveClient, SessionTrace
from eval.tier3.trace import TraceParser

logger = logging.getLogger(__name__)


class Tier3Executor:
    """Execute Tier 3 live integration test cases against a running GRIM server.

    Usage:
        executor = Tier3Executor(config)
        results = await executor.run(categories=["conversation", "research"])
    """

    def __init__(
        self,
        config: EvalConfig | None = None,
        judges: list[Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or EvalConfig()
        self.judges = judges or []
        self._progress = progress_callback
        self.client = GrimLiveClient(
            ws_base_url=self.config.tier3_ws_url,
            timeout_ms=self.config.tier3_timeout_ms,
            sandbox=self.config.tier3_sandbox,
        )

    def _emit(self, event: dict[str, Any]) -> None:
        if self._progress:
            try:
                self._progress(event)
            except Exception:
                pass

    # ── Dataset loading ──

    def load_datasets(
        self, categories: list[str] | None = None,
    ) -> dict[str, Tier3Dataset]:
        """Load Tier 3 YAML datasets from disk."""
        datasets: dict[str, Tier3Dataset] = {}
        tier3_dir = self.config.tier3_datasets_dir
        if tier3_dir is None:
            tier3_dir = self.config.datasets_dir / "tier3"

        if not tier3_dir.exists():
            logger.warning("Tier 3 dataset directory not found: %s", tier3_dir)
            return datasets

        for path in sorted(tier3_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                dataset = Tier3Dataset(**data)
                if categories and dataset.category not in categories:
                    continue
                datasets[dataset.category] = dataset
            except Exception as exc:
                logger.error("Failed to load %s: %s", path, exc)

        return datasets

    # ── Execution ──

    async def run(
        self,
        categories: list[str] | None = None,
        case_ids: list[str] | None = None,
    ) -> list[Tier3CaseResult]:
        """Run Tier 3 cases and return results."""
        datasets = self.load_datasets(categories)
        if not datasets:
            logger.warning("No Tier 3 datasets found")
            return []

        # Collect cases to run
        cases: list[Tier3Case] = []
        for dataset in datasets.values():
            for case in dataset.cases:
                if case_ids and case.id not in case_ids:
                    continue
                cases.append(case)

        if not cases:
            logger.warning("No matching Tier 3 cases found")
            return []

        # Health check
        healthy = await self.client.health_check()
        if not healthy:
            logger.error("GRIM server not reachable at %s", self.config.tier3_docker_url)
            return [
                Tier3CaseResult(
                    case_id=c.id,
                    category=c.category.value,
                    error="Server not reachable",
                )
                for c in cases
            ]

        self._emit({
            "type": "tier3_start",
            "total_cases": len(cases),
            "categories": list(datasets.keys()),
        })

        # Execute sequentially (live server, avoid overload)
        results: list[Tier3CaseResult] = []
        for i, case in enumerate(cases):
            self._emit({
                "type": "tier3_case_start",
                "case_id": case.id,
                "index": i,
                "total": len(cases),
            })

            t0 = time.monotonic()
            result = await self._run_case(case)
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(result)

            self._emit({
                "type": "tier3_case_end",
                "case_id": case.id,
                "passed": result.passed,
                "score": result.overall_score,
                "duration_ms": result.duration_ms,
            })

        self._emit({
            "type": "tier3_end",
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
        })

        return results

    async def _run_case(self, case: Tier3Case) -> Tier3CaseResult:
        """Execute a single Tier 3 case."""
        messages = [turn.message for turn in case.turns]

        # Send all turns and collect traces
        session_trace: SessionTrace = await self.client.send_turns(messages)

        # Parse each turn's trace
        turn_results: list[Tier3TurnResult] = []
        all_routing: list[str] = []
        all_tools: list[str] = []
        subgraph_history: list[str] = []
        total_loops = 0

        for idx, turn_trace in enumerate(session_trace.turns):
            parsed = TraceParser.parse(turn_trace.events)

            turn_result = Tier3TurnResult(
                turn_index=idx,
                response_text=turn_trace.response_text,
                routing_path=parsed.routing_path,
                subgraph=parsed.subgraph,
                tools_called=parsed.tools_called,
                metrics=parsed.metrics,
                trace_events=turn_trace.events,
            )
            turn_results.append(turn_result)

            all_routing.extend(parsed.routing_path)
            all_tools.extend(parsed.tools_called)
            if parsed.subgraph:
                subgraph_history.append(parsed.subgraph)
            total_loops += parsed.loop_count

        # Aggregate metrics from last turn (or sum across all)
        agg_metrics = turn_results[-1].metrics if turn_results else None

        # Build result
        result = Tier3CaseResult(
            case_id=case.id,
            category=case.category.value,
            tags=case.tags,
            turn_results=turn_results,
            routing_path=all_routing,
            tools_called=all_tools,
            loop_count=total_loops,
            subgraph_history=subgraph_history,
            metrics=agg_metrics or Tier3CaseResult().metrics,
        )

        # Check for errors
        if session_trace.turns and session_trace.turns[-1].error:
            result.error = session_trace.turns[-1].error

        # Run judges
        judgments = await self._judge(case, result)
        result.judgments = judgments

        # Compute overall score and pass/fail
        if judgments:
            result.overall_score = sum(j.score for j in judgments) / len(judgments)
            result.passed = all(j.passed for j in judgments) and result.error is None
        else:
            result.passed = result.error is None

        return result

    async def _judge(
        self, case: Tier3Case, result: Tier3CaseResult,
    ) -> list[Tier3Judgment]:
        """Invoke all registered judges on a case result."""
        judgments: list[Tier3Judgment] = []
        for judge in self.judges:
            try:
                judgment = await judge.judge(case, result)
                judgments.append(judgment)
            except Exception as exc:
                logger.error("Judge %s failed: %s", judge.__class__.__name__, exc)
                judgments.append(Tier3Judgment(
                    judge=judge.__class__.__name__,
                    score=0.0,
                    passed=False,
                    rationale=f"Judge error: {exc}",
                ))
        return judgments
