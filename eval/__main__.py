"""CLI entry point for the GRIM evaluation framework.

Usage:
    python -m eval run --tier 1
    python -m eval run --tier 2 --category memory_agent
    python -m eval run --tier all
    python -m eval run --tier all --fail-on-regression
    python -m eval list
    python -m eval report <run_id>
    python -m eval compare <base_run_id> <target_run_id>
    python -m eval history --suite routing --last 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure GRIM root is on path
_grim_root = Path(__file__).parent.parent
if str(_grim_root) not in sys.path:
    sys.path.insert(0, str(_grim_root))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="grim-eval",
        description="GRIM Evaluation Framework",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ──
    run_p = sub.add_parser("run", help="Run evaluations")
    run_p.add_argument(
        "--tier", default="all",
        help="Tier to run: 1, 2, or all (default: all)",
    )
    run_p.add_argument(
        "--category", "-c", action="append",
        help="Filter to specific categories (repeatable)",
    )
    run_p.add_argument(
        "--judge", action="store_true",
        help="Enable LLM judge for Tier 2 (costs API tokens)",
    )
    run_p.add_argument(
        "--fail-on-regression", action="store_true",
        help="Exit with code 1 if regressions detected vs baseline",
    )
    run_p.add_argument(
        "--baseline", default="latest",
        help="Baseline run_id for regression check (default: latest)",
    )

    # ── list ──
    sub.add_parser("list", help="List available datasets")

    # ── report ──
    report_p = sub.add_parser("report", help="Generate report for a run")
    report_p.add_argument("run_id", help="Run ID to report on")

    # ── compare ──
    compare_p = sub.add_parser("compare", help="Compare two runs")
    compare_p.add_argument("base", help="Base run ID")
    compare_p.add_argument("target", help="Target run ID")

    # ── history ──
    history_p = sub.add_parser("history", help="Show score history")
    history_p.add_argument("--suite", help="Filter to a specific suite")
    history_p.add_argument("--last", type=int, default=10, help="Number of runs")

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "run":
        return asyncio.run(_cmd_run(args))
    elif args.command == "list":
        return _cmd_list()
    elif args.command == "report":
        return _cmd_report(args)
    elif args.command == "compare":
        return _cmd_compare(args)
    elif args.command == "history":
        return _cmd_history(args)

    return 0


async def _cmd_run(args) -> int:
    from eval.config import EvalConfig
    from eval.engine.comparator import compare_runs, find_latest_run
    from eval.engine.runner import EvalRunner

    config = EvalConfig.from_env()
    runner = EvalRunner(config)

    tier = args.tier
    if tier not in ("all", "1", "2", "3"):
        try:
            tier = int(tier)
        except ValueError:
            pass

    # Tier 3 has its own execution path
    if str(tier) == "3":
        return await _cmd_run_tier3(args, config)

    print(f"\n  GRIM Evaluation — Tier {tier}")
    print(f"  {'=' * 40}\n")

    run = await runner.run(
        tier=tier,
        categories=args.category,
        use_judge=args.judge,
    )

    # Print summary
    print(f"\n  Run: {run.run_id} ({run.status.value})")
    print(f"  Git: {run.git_sha}")
    print(f"  Duration: {run.duration_ms}ms\n")

    for suite in run.suites:
        icon = "+" if suite.passed == suite.total else "-"
        print(f"  [{icon}] Tier {suite.tier} {suite.category}: "
              f"{suite.passed}/{suite.total} passed ({suite.score:.1%})")

        # Show failures
        for case in suite.cases:
            if not case.passed:
                print(f"      FAIL {case.case_id}", end="")
                if case.error:
                    print(f": {case.error}", end="")
                elif case.checks:
                    failed = [c for c in case.checks if not c.passed]
                    if failed:
                        print(f": {failed[0].name} expected={failed[0].expected} got={failed[0].actual}", end="")
                print()

    print(f"\n  Overall: {run.total_passed}/{run.total_cases} "
          f"({run.pass_rate:.1%}), score={run.overall_score:.2f}")
    print(f"  Duration: {run.duration_ms}ms\n")

    # Regression check
    if args.fail_on_regression:
        baseline = find_latest_run(config.results_dir)
        if baseline and baseline.run_id != run.run_id:
            comparison = compare_runs(baseline, run, config.regression_tolerance)
            if comparison.has_regressions:
                print(f"\n  REGRESSIONS DETECTED ({len(comparison.regressions)}):")
                for r in comparison.regressions:
                    print(f"    {r.severity.upper()} {r.case_id}: "
                          f"{r.base_score:.2f} -> {r.target_score:.2f} ({r.delta:+.2f})")
                return 1
            else:
                print("  No regressions detected.")

    return 0 if run.pass_rate >= 0.99 else 1


async def _cmd_run_tier3(args, config) -> int:
    """Run Tier 3 live integration eval."""
    from eval.tier3.executor import Tier3Executor
    from eval.tier3.ground_truth import GroundTruthLoader
    from eval.tier3.judges import create_default_judges

    vault_path = config.ground_truth_vault_path
    if vault_path is None:
        workspace = Path(__file__).parent.parent.parent
        vault_path = workspace / "kronos-vault"

    gt_loader = GroundTruthLoader(vault_path) if vault_path.exists() else None

    judges = create_default_judges(
        model=config.tier3_judge_model,
        ground_truth_loader=gt_loader,
    )

    def progress(event):
        evt_type = event.get("type", "")
        if evt_type == "tier3_start":
            print(f"\n  Running {event['total_cases']} cases across {event['categories']}")
        elif evt_type == "tier3_case_start":
            print(f"  [{event['index']+1}/{event['total']}] {event['case_id']}...", end="", flush=True)
        elif evt_type == "tier3_case_end":
            icon = "PASS" if event["passed"] else "FAIL"
            print(f" {icon} ({event['score']:.2f}, {event['duration_ms']}ms)")
        elif evt_type == "tier3_end":
            print(f"\n  Done: {event['passed']}/{event['total']} passed")

    executor = Tier3Executor(config=config, judges=judges, progress_callback=progress)

    print(f"\n  GRIM Evaluation — Tier 3 (Live Integration)")
    print(f"  Target: {config.tier3_docker_url}")
    print(f"  Sandbox: {config.tier3_sandbox}")
    print(f"  {'=' * 50}\n")

    results = await executor.run(categories=args.category)

    if not results:
        print("  No results — is the GRIM server running?")
        return 1

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    print(f"\n  {'=' * 50}")
    print(f"  Results: {passed}/{total} passed ({passed/total*100:.1f}%)\n")

    # Per-category breakdown
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    for cat, cat_results in sorted(by_cat.items()):
        cat_passed = sum(1 for r in cat_results if r.passed)
        avg_score = sum(r.overall_score for r in cat_results) / len(cat_results)
        print(f"  {cat:20s}: {cat_passed}/{len(cat_results)} passed, avg score {avg_score:.2f}")

        # Show failures with details
        for r in cat_results:
            if not r.passed:
                print(f"    FAIL {r.case_id}", end="")
                if r.error:
                    print(f": {r.error}", end="")
                else:
                    failed_judges = [j for j in r.judgments if not j.passed]
                    if failed_judges:
                        print(f": {', '.join(j.judge for j in failed_judges)}", end="")
                print()

    # Efficiency summary
    print(f"\n  Efficiency Metrics:")
    total_tokens = sum(r.metrics.total_tokens for r in results)
    total_cost = sum(r.metrics.cost_estimate_usd for r in results)
    total_time = sum(r.duration_ms for r in results)
    print(f"    Total tokens: {total_tokens:,}")
    print(f"    Total cost: ${total_cost:.4f}")
    print(f"    Total time: {total_time/1000:.1f}s")
    print()

    return 0 if passed == total else 1


def _cmd_list() -> int:
    from eval.config import EvalConfig
    from eval.engine.runner import EvalRunner

    config = EvalConfig.from_env()
    runner = EvalRunner(config)
    datasets = runner.list_datasets()

    if not datasets:
        print("No datasets found.")
        return 0

    print(f"\n  GRIM Eval Datasets")
    print(f"  {'=' * 50}\n")

    current_tier = None
    total_cases = 0
    for ds in datasets:
        if ds["tier"] != current_tier:
            current_tier = ds["tier"]
            print(f"  Tier {current_tier}:")

        print(f"    {ds['category']:25s} {ds['case_count']:4d} cases  {ds['description']}")
        total_cases += ds["case_count"]

    print(f"\n  Total: {total_cases} cases across {len(datasets)} datasets\n")
    return 0


def _cmd_report(args) -> int:
    from eval.config import EvalConfig
    from eval.reports import generate_markdown_report

    config = EvalConfig.from_env()
    results_dir = config.results_dir

    # Find result file by run_id prefix
    for path in results_dir.glob("*.json"):
        if args.run_id in path.stem:
            run_data = json.loads(path.read_text())
            from eval.schema import EvalRun
            run = EvalRun(**run_data)
            report = generate_markdown_report(run)
            print(report)
            return 0

    print(f"Run {args.run_id} not found in {results_dir}")
    return 1


def _cmd_compare(args) -> int:
    from eval.config import EvalConfig
    from eval.engine.comparator import compare_runs, load_run

    config = EvalConfig.from_env()
    results_dir = config.results_dir

    base_run = _find_run(results_dir, args.base)
    target_run = _find_run(results_dir, args.target)

    if not base_run or not target_run:
        print("Could not find one or both runs.")
        return 1

    result = compare_runs(base_run, target_run)

    print(f"\n  Comparison: {result.base_run_id} → {result.target_run_id}")
    print(f"  Overall delta: {result.overall_delta:+.2f}")
    print(f"  Regressions: {len(result.regressions)}")
    print(f"  Improvements: {len(result.improvements)}")
    print(f"  Unchanged: {result.unchanged}\n")

    if result.regressions:
        print("  Regressions:")
        for r in result.regressions:
            print(f"    {r.severity:8s} {r.case_id}: {r.base_score:.2f} → {r.target_score:.2f} ({r.delta:+.2f})")

    if result.improvements:
        print("  Improvements:")
        for r in result.improvements:
            print(f"    {r.case_id}: {r.base_score:.2f} → {r.target_score:.2f} ({r.delta:+.2f})")

    return 1 if result.has_regressions else 0


def _cmd_history(args) -> int:
    from eval.config import EvalConfig

    config = EvalConfig.from_env()
    results_dir = config.results_dir

    files = sorted(results_dir.glob("*.json"), reverse=True)[:args.last]

    if not files:
        print("No results found.")
        return 0

    print(f"\n  Score History (last {args.last})")
    print(f"  {'=' * 60}\n")
    print(f"  {'Timestamp':25s} {'Run':10s} {'Score':>8s} {'Pass%':>8s} {'Cases':>6s}")
    print(f"  {'-' * 60}")

    for path in files:
        try:
            data = json.loads(path.read_text())
            ts = data.get("timestamp", "")[:19]
            run_id = data.get("run_id", "")[:8]
            score = data.get("overall_score", 0)
            rate = data.get("pass_rate", 0)
            total = data.get("total_cases", 0)

            if args.suite:
                # Filter to specific suite
                found = False
                for s in data.get("suites", []):
                    if s.get("category") == args.suite:
                        score = s.get("score", 0)
                        total = s.get("total", 0)
                        rate = s.get("passed", 0) / max(total, 1)
                        found = True
                        break
                if not found:
                    continue

            print(f"  {ts:25s} {run_id:10s} {score:8.2f} {rate:7.1%} {total:6d}")
        except Exception:
            continue

    print()
    return 0


def _find_run(results_dir: Path, run_id: str):
    """Find an eval run by ID prefix."""
    from eval.schema import EvalRun

    for path in results_dir.glob("*.json"):
        if run_id in path.stem:
            data = json.loads(path.read_text())
            return EvalRun(**data)
    return None


if __name__ == "__main__":
    sys.exit(main())
