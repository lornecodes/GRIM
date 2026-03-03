"""EvalRunner — orchestrates evaluation execution across both tiers."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from eval.config import EvalConfig
from eval.engine.comparator import find_latest_run, save_run
from eval.engine.grading import grade_tier1_suite, grade_tier2_suite
from eval.schema import (
    CaseResult,
    EvalRun,
    EvalRunStatus,
    SuiteResult,
    Tier1Case,
    Tier1Dataset,
    Tier2Case,
    Tier2Dataset,
)

logger = logging.getLogger(__name__)


class EvalRunner:
    """Orchestrates evaluation runs across Tier 1 and Tier 2."""

    def __init__(
        self,
        config: EvalConfig | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or EvalConfig()
        self._progress = progress_callback

    def _emit(self, event: dict[str, Any]) -> None:
        """Emit a progress event if a callback is registered."""
        if self._progress:
            try:
                self._progress(event)
            except Exception:
                pass

    # ── Dataset loading ──

    def load_tier1_datasets(
        self, categories: list[str] | None = None,
    ) -> dict[str, Tier1Dataset]:
        """Load Tier 1 YAML datasets from disk."""
        datasets: dict[str, Tier1Dataset] = {}
        tier1_dir = self.config.datasets_dir / "tier1"

        if not tier1_dir.exists():
            logger.warning("Tier 1 dataset directory not found: %s", tier1_dir)
            return datasets

        for path in sorted(tier1_dir.glob("*_cases.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                dataset = Tier1Dataset(**data)
                if categories and dataset.category not in categories:
                    continue
                datasets[dataset.category] = dataset
            except Exception as exc:
                logger.error("Failed to load %s: %s", path, exc)

        return datasets

    def load_tier2_datasets(
        self, categories: list[str] | None = None,
    ) -> dict[str, Tier2Dataset]:
        """Load Tier 2 YAML datasets from disk."""
        datasets: dict[str, Tier2Dataset] = {}
        tier2_dir = self.config.datasets_dir / "tier2"

        if not tier2_dir.exists():
            logger.warning("Tier 2 dataset directory not found: %s", tier2_dir)
            return datasets

        for path in sorted(tier2_dir.glob("*_cases.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                dataset = Tier2Dataset(**data)
                if categories and dataset.category not in categories:
                    continue
                datasets[dataset.category] = dataset
            except Exception as exc:
                logger.error("Failed to load %s: %s", path, exc)

        return datasets

    def list_datasets(self) -> list[dict[str, Any]]:
        """List all available datasets with metadata."""
        result = []

        for tier_dir, tier in [
            (self.config.datasets_dir / "tier1", 1),
            (self.config.datasets_dir / "tier2", 2),
        ]:
            if not tier_dir.exists():
                continue
            for path in sorted(tier_dir.glob("*_cases.yaml")):
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    result.append({
                        "tier": tier,
                        "category": data.get("category", path.stem),
                        "description": data.get("description", ""),
                        "case_count": len(data.get("cases", [])),
                        "path": str(path),
                    })
                except Exception:
                    pass

        return result

    # ── Execution ──

    async def run_tier1(
        self,
        categories: list[str] | None = None,
    ) -> list[SuiteResult]:
        """Run all Tier 1 evaluations."""
        from eval.engine.tier1.keyword_routing import evaluate_keyword_suite
        from eval.engine.tier1.knowledge_context import evaluate_knowledge_suite
        from eval.engine.tier1.routing import evaluate_routing_suite
        from eval.engine.tier1.skill_matching import evaluate_skill_suite
        from eval.engine.tier1.tool_resolution import evaluate_tool_suite

        datasets = self.load_tier1_datasets(categories)
        suites: list[SuiteResult] = []

        evaluators = {
            "routing": evaluate_routing_suite,
            "skill_matching": evaluate_skill_suite,
            "tool_groups": evaluate_tool_suite,
            "keyword_routing": evaluate_keyword_suite,
            "knowledge_context": evaluate_knowledge_suite,
        }

        for category, dataset in datasets.items():
            evaluator = evaluators.get(category)
            if not evaluator:
                logger.warning("No evaluator for category: %s", category)
                continue

            logger.info("Running Tier 1: %s (%d cases)", category, len(dataset.cases))
            self._emit({"type": "suite_start", "tier": 1, "category": category, "total": len(dataset.cases)})

            if category == "skill_matching":
                results = await evaluator(dataset.cases, self.config.skills_path)
            else:
                results = await evaluator(dataset.cases)

            suite = grade_tier1_suite(results, category)
            suites.append(suite)
            self._emit({
                "type": "suite_end", "tier": 1, "category": category,
                "passed": suite.passed, "total": suite.total, "score": suite.score,
            })

            logger.info(
                "  %s: %d/%d passed (%.1f%%)",
                category, suite.passed, suite.total, suite.score * 100,
            )

        return suites

    async def run_tier2(
        self,
        categories: list[str] | None = None,
        use_judge: bool = False,
    ) -> list[SuiteResult]:
        """Run all Tier 2 evaluations."""
        from eval.engine.tier2.judge import make_judge_fn
        from eval.engine.tier2.multi_turn import evaluate_multi_turn_suite
        from eval.engine.tier2.single_turn import evaluate_single_turn_suite

        datasets = self.load_tier2_datasets(categories)
        suites: list[SuiteResult] = []

        judge_fn = None
        if use_judge:
            judge_fn = make_judge_fn(
                model=self.config.judge_model,
                temperature=self.config.judge_temperature,
                max_tokens=self.config.judge_max_tokens,
            )

        for category, dataset in datasets.items():
            logger.info("Running Tier 2: %s (%d cases)", category, len(dataset.cases))
            self._emit({"type": "suite_start", "tier": 2, "category": category, "total": len(dataset.cases)})

            single_cases = [c for c in dataset.cases if c.turn_type == "single"]
            multi_cases = [c for c in dataset.cases if c.turn_type == "multi"]

            results: list[CaseResult] = []

            if single_cases:
                single_results = await evaluate_single_turn_suite(
                    single_cases, judge_fn, self.config,
                )
                results.extend(single_results)

            if multi_cases:
                multi_results = await evaluate_multi_turn_suite(
                    multi_cases, judge_fn, self.config,
                )
                results.extend(multi_results)

            suite = grade_tier2_suite(results, category)
            suites.append(suite)
            self._emit({
                "type": "suite_end", "tier": 2, "category": category,
                "passed": suite.passed, "total": suite.total, "score": suite.score,
            })

            logger.info(
                "  %s: %d/%d passed, avg score %.2f",
                category, suite.passed, suite.total, suite.score,
            )

        return suites

    async def run(
        self,
        tier: int | str = "all",
        categories: list[str] | None = None,
        use_judge: bool = False,
    ) -> EvalRun:
        """Run a complete evaluation and return results."""
        run_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now(timezone.utc).isoformat()
        git_sha = self._get_git_sha()

        run = EvalRun(
            run_id=run_id,
            timestamp=timestamp,
            git_sha=git_sha,
            tier=tier,
            status=EvalRunStatus.RUNNING,
            config_snapshot={
                "judge_model": self.config.judge_model,
                "tier2_mode": self.config.tier2_mode,
                "use_judge": use_judge,
            },
        )

        start = time.monotonic()

        try:
            if tier in (1, "1", "all"):
                tier1_suites = await self.run_tier1(categories)
                run.suites.extend(tier1_suites)

            if tier in (2, "2", "all"):
                tier2_suites = await self.run_tier2(categories, use_judge)
                run.suites.extend(tier2_suites)

            run.status = EvalRunStatus.COMPLETED

        except Exception as exc:
            logger.error("Eval run failed: %s", exc)
            run.status = EvalRunStatus.FAILED

        run.duration_ms = int((time.monotonic() - start) * 1000)
        run.compute_stats()

        # Save results
        save_run(run, self.config.results_dir)

        return run

    def _get_git_sha(self) -> str:
        """Get current git SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""
