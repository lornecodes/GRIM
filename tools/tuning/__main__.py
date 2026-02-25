"""
Tuning CLI — Entry point for prompt optimization.

Usage:
    python -m tuning eval <agent|all>       Run + score current prompts
    python -m tuning run <agent|all>        Full optimization loop
    python -m tuning status                 Show tuning history
    python -m tuning rollback <agent>       Restore best prompt version
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure tools/ is on the import path so both 'tuning' and 'actualization' resolve
_tools_dir = str(Path(__file__).resolve().parents[1])
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import AGENTS, MAX_ITERATIONS, MIN_ITERATIONS, MODEL, RESULTS_DIR
from .runner import run_agent_suite, _make_client, load_cases
from .evaluator import SCORERS
from .optimizer import optimize_step, read_prompt, AGENT_TUNABLE
from .tracker import TuningTracker, get_all_agent_status

console = Console()


# =========================================================================
# Commands
# =========================================================================

def cmd_eval(agent: str):
    """Evaluate current prompts without optimizing."""
    agents = AGENTS if agent == "all" else [agent]
    client = _make_client()

    for ag in agents:
        console.rule(f"[bold cyan]Evaluating: {ag}")
        results = run_agent_suite(ag, client, MODEL)
        _print_results(ag, results)


def cmd_run(agent: str):
    """Full optimization loop."""
    agents = AGENTS if agent == "all" else [agent]
    client = _make_client()

    for ag in agents:
        console.rule(f"[bold green]Optimizing: {ag}")
        tracker = TuningTracker(ag)

        prompt_vars = AGENT_TUNABLE.get(ag, [])
        if not prompt_vars:
            console.print(f"  [dim]No tunable sections for {ag}, skipping[/dim]")
            continue

        for iteration in range(1, MAX_ITERATIONS + 1):
            stall_count = tracker.get_stall_count()
            console.print(f"\n  [bold]Iteration {iteration}/{MAX_ITERATIONS}[/bold]", end="")
            if stall_count > 0:
                console.print(f"  [yellow](stall: {stall_count})[/yellow]", end="")
            if iteration <= MIN_ITERATIONS:
                console.print(f"  [dim](min iteration {iteration}/{MIN_ITERATIONS})[/dim]", end="")
            console.print()

            # 1. Run all test cases with CURRENT prompts
            console.print("  Evaluating current prompts...", end=" ")
            results = run_agent_suite(ag, client, MODEL)
            _print_results_compact(results)

            # 2. Compute current loss
            total_ws, total_wm = 0.0, 0.0
            for r in results:
                if r["score"]:
                    w = r["weight"]
                    total_ws += r["score"].score * w
                    total_wm += r["score"].max_score * w
            loss = 1.0 - (total_ws / total_wm) if total_wm > 0 else 1.0
            accuracy = (1.0 - loss) * 100
            console.print(f"  Loss: [bold]{loss:.4f}[/bold]  Accuracy: [bold]{accuracy:.1f}%[/bold]")

            # 2b. Show per-case deltas if we have history
            per_case_deltas = tracker.get_per_case_deltas()
            if per_case_deltas:
                improved = sum(1 for d in per_case_deltas.values() if d["delta"] > 0)
                regressed = sum(1 for d in per_case_deltas.values() if d["delta"] < 0)
                flat = sum(1 for d in per_case_deltas.values() if d["delta"] == 0)
                console.print(f"  Deltas: [green]{improved}↑[/green] [red]{regressed}↓[/red] [dim]{flat}→[/dim]")

            # 3. Snapshot current prompts (the PRE-optimization state)
            snapshot = {}
            for var in prompt_vars:
                snapshot[var] = read_prompt(var)

            # 4. Optimize — generates new prompts and writes them to disk
            console.print("  Optimizing prompts...", end=" ")
            opt_result = optimize_step(
                agent=ag,
                results=results,
                iteration=iteration,
                history=tracker.history,
                client=client,
                model=MODEL,
                stall_count=stall_count,
                per_case_deltas=per_case_deltas,
            )

            if opt_result.get("converged"):
                console.print(f"[green]CONVERGED[/green] — {opt_result['message']}")
                tracker.record(iteration, loss, accuracy, results, opt_result, snapshot)
                break

            if opt_result.get("skipped"):
                console.print(f"[yellow]SKIPPED[/yellow] — {opt_result.get('reason', 'unknown')}")
                tracker.record(iteration, loss, accuracy, results, opt_result, snapshot)
                break

            updates = opt_result.get("updates", {})
            tokens = opt_result.get("optimizer_tokens", {})
            temp = opt_result.get("temperature_used", 0.7)
            console.print(
                f"[green]OK[/green] — {len(updates)} prompts updated "
                f"({tokens.get('input', 0)}+{tokens.get('output', 0)} tokens, T={temp})"
            )
            for var_name, info in updates.items():
                console.print(
                    f"    {var_name}: {info['old_length']} → {info['new_length']} chars"
                )

            # 5. VALIDATION GATE — re-run suite with new prompts, reject if worse
            if updates:
                console.print("  Validating new prompts...", end=" ")
                val_results = run_agent_suite(ag, client, MODEL)
                val_ws, val_wm = 0.0, 0.0
                for r in val_results:
                    if r["score"]:
                        w = r["weight"]
                        val_ws += r["score"].score * w
                        val_wm += r["score"].max_score * w
                val_loss = 1.0 - (val_ws / val_wm) if val_wm > 0 else 1.0
                val_accuracy = (1.0 - val_loss) * 100
                _print_results_compact(val_results)

                delta_loss = val_loss - loss
                if val_loss < loss - 0.001:
                    # Improved! Keep the new prompts, record the VALIDATED score
                    console.print(
                        f"  [green]✓ IMPROVED[/green] {accuracy:.1f}% → {val_accuracy:.1f}% "
                        f"(Δloss={delta_loss:+.4f})"
                    )
                    loss = val_loss
                    accuracy = val_accuracy
                    results = val_results
                elif val_loss <= loss + 0.005:
                    # Roughly same — keep it (might unlock progress next iteration)
                    console.print(
                        f"  [dim]≈ SIMILAR[/dim] {accuracy:.1f}% → {val_accuracy:.1f}% "
                        f"(Δloss={delta_loss:+.4f}) — keeping"
                    )
                    loss = val_loss
                    accuracy = val_accuracy
                    results = val_results
                else:
                    # Regressed — rollback immediately
                    console.print(
                        f"  [red]✗ REGRESSED[/red] {accuracy:.1f}% → {val_accuracy:.1f}% "
                        f"(Δloss={delta_loss:+.4f}) — rolling back"
                    )
                    # Capture what was tried BEFORE we rollback (so optimizer can learn)
                    from .optimizer import read_prompt_clean
                    rejected_text = {}
                    for var in prompt_vars:
                        rejected_text[var] = read_prompt_clean(var)

                    # Build per-case regression detail
                    rejected_cases = []
                    pre_scores = {r["case_id"]: r for r in results}
                    for vr in val_results:
                        cid = vr["case_id"]
                        pre = pre_scores.get(cid)
                        if pre and pre["score"] and vr["score"]:
                            pre_pct = pre["score"].pct
                            val_pct = vr["score"].pct
                            if val_pct < pre_pct - 1:  # regressed by >1%
                                rejected_cases.append({
                                    "case_id": cid,
                                    "before": round(pre_pct, 1),
                                    "after": round(val_pct, 1),
                                    "failures": vr["score"].failures[:3],  # top 3
                                })

                    opt_result["rejected"] = True
                    opt_result["rejected_changes"] = rejected_text
                    opt_result["rejected_accuracy"] = round(val_accuracy, 2)
                    opt_result["rejected_cases"] = rejected_cases

                    for var_name, text in snapshot.items():
                        from .optimizer import write_prompt
                        write_prompt(var_name, text.replace("{{", "{").replace("}}", "}"))
                    from .optimizer import reload_prompts
                    reload_prompts()

            # 6. Record iteration (with validated scores)
            # Re-snapshot after possible rollback
            post_snapshot = {}
            for var in prompt_vars:
                post_snapshot[var] = read_prompt(var)
            tracker.record(iteration, loss, accuracy, results, opt_result, post_snapshot)

            # 7. Check convergence — only after MIN_ITERATIONS
            if tracker.check_convergence():
                console.print(f"  [green]Converged after {iteration} iterations[/green]")
                break

            # 8. Check hard stall — if we've been flat too long, stop
            if tracker.is_stalled():
                console.print(f"  [yellow]Stalled for {tracker.get_stall_count()} iterations[/yellow]")
                break

        # Always restore the best-performing prompts found during this run
        best = tracker.get_best()
        if best and tracker.last_loss is not None:
            if tracker.last_loss > (tracker.best_loss or 999) + 0.001:
                console.print(f"  [cyan]Rolling back to best (iter {best['iteration']}, "
                              f"loss {tracker.best_loss:.4f} vs current {tracker.last_loss:.4f})[/cyan]")
                tracker.rollback_to_best()
                from .optimizer import reload_prompts
                reload_prompts()

        # Final summary
        console.print()
        console.print(tracker.format_status())
        console.print()


def cmd_status():
    """Show tuning history for all agents."""
    console.print(Panel(get_all_agent_status(), title="Tuning Status", box=box.ROUNDED))


def cmd_rollback(agent: str):
    """Restore prompts to the best-performing version."""
    tracker = TuningTracker(agent)
    best = tracker.get_best()
    if not best:
        console.print(f"[red]No best snapshot found for {agent}[/red]")
        return

    console.print(f"Rolling back {agent} to iteration {best['iteration']}...")
    success = tracker.rollback_to_best()
    if success:
        console.print("[green]Rollback complete[/green]")
    else:
        console.print("[red]Rollback failed[/red]")


# =========================================================================
# Output helpers
# =========================================================================

def _print_results(agent: str, results):
    """Print detailed per-case results."""
    table = Table(title=f"{agent} evaluation", box=box.SIMPLE_HEAVY)
    table.add_column("Case", style="cyan", min_width=20)
    table.add_column("Score", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Status", min_width=8)
    table.add_column("Details", max_width=60)

    total_ws, total_wm = 0.0, 0.0
    for r in results:
        case_id = r["case_id"]
        weight = f"{r['weight']:.1f}"

        if r["error"]:
            table.add_row(case_id, "ERR", weight, "[red]CRASH[/red]", r["error"][:60])
            continue

        s = r["score"]
        total_ws += s.score * r["weight"]
        total_wm += s.max_score * r["weight"]

        score_str = f"{s.score:.1f}/{s.max_score:.1f}"
        status = "[green]PASS[/green]" if s.pct >= 80 else "[yellow]PARTIAL[/yellow]" if s.pct >= 50 else "[red]FAIL[/red]"
        details = "; ".join(s.failures[:2]) if s.failures else "[dim]all checks passed[/dim]"
        table.add_row(case_id, score_str, weight, status, details[:60])

    console.print(table)

    if total_wm > 0:
        loss = 1.0 - (total_ws / total_wm)
        console.print(
            f"  Weighted Loss: [bold]{loss:.4f}[/bold]  "
            f"Accuracy: [bold]{(1.0 - loss) * 100:.1f}%[/bold]"
        )


def _print_results_compact(results):
    """One-line summary of results."""
    passes = sum(1 for r in results if r["score"] and r["score"].pct >= 80)
    partial = sum(1 for r in results if r["score"] and 50 <= r["score"].pct < 80)
    fails = sum(1 for r in results if r["score"] and r["score"].pct < 50)
    crashes = sum(1 for r in results if r["error"])
    console.print(
        f"[green]{passes}[/green] pass / "
        f"[yellow]{partial}[/yellow] partial / "
        f"[red]{fails}[/red] fail / "
        f"[red]{crashes}[/red] crash"
    )


# =========================================================================
# Main
# =========================================================================

def main():
    if len(sys.argv) < 2:
        console.print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "eval":
        agent = sys.argv[2] if len(sys.argv) > 2 else "all"
        if agent not in AGENTS and agent != "all":
            console.print(f"[red]Unknown agent: {agent}. Choose from: {', '.join(AGENTS)}, all[/red]")
            sys.exit(1)
        cmd_eval(agent)

    elif command == "run":
        agent = sys.argv[2] if len(sys.argv) > 2 else "all"
        if agent not in AGENTS and agent != "all":
            console.print(f"[red]Unknown agent: {agent}. Choose from: {', '.join(AGENTS)}, all[/red]")
            sys.exit(1)
        cmd_run(agent)

    elif command == "status":
        cmd_status()

    elif command == "rollback":
        if len(sys.argv) < 3:
            console.print("[red]Usage: python -m tuning rollback <agent>[/red]")
            sys.exit(1)
        agent = sys.argv[2]
        if agent not in AGENTS:
            console.print(f"[red]Unknown agent: {agent}[/red]")
            sys.exit(1)
        cmd_rollback(agent)

    else:
        console.print(f"[red]Unknown command: {command}[/red]")
        console.print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
