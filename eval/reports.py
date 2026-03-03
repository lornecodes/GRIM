"""Report generation — markdown summaries of eval runs."""

from __future__ import annotations

from eval.schema import EvalRun


def generate_markdown_report(run: EvalRun) -> str:
    """Generate a markdown report for an eval run."""
    lines = [
        f"# GRIM Eval Report — {run.run_id}",
        "",
        f"- **Timestamp**: {run.timestamp}",
        f"- **Git SHA**: {run.git_sha}",
        f"- **Status**: {run.status.value}",
        f"- **Tier**: {run.tier}",
        f"- **Duration**: {run.duration_ms}ms",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Cases | {run.total_cases} |",
        f"| Passed | {run.total_passed} |",
        f"| Failed | {run.total_cases - run.total_passed} |",
        f"| Pass Rate | {run.pass_rate:.1%} |",
        f"| Overall Score | {run.overall_score:.2f} |",
        "",
    ]

    # Per-suite breakdown
    for suite in run.suites:
        lines.append(f"## Tier {suite.tier}: {suite.category}")
        lines.append("")
        lines.append(f"**{suite.passed}/{suite.total}** passed ({suite.score:.1%})")
        lines.append("")

        if suite.cases:
            lines.append("| Case | Status | Score | Details |")
            lines.append("|------|--------|-------|---------|")

            for case in suite.cases:
                status = "PASS" if case.passed else "FAIL"
                icon = "+" if case.passed else "-"

                detail = ""
                if case.error:
                    detail = case.error[:60]
                elif not case.passed and case.checks:
                    failed = [c for c in case.checks if not c.passed]
                    if failed:
                        detail = f"{failed[0].name}: expected={failed[0].expected}"

                lines.append(f"| {case.case_id} | {icon} {status} | {case.score:.2f} | {detail} |")

            lines.append("")

        # Dimension summary for Tier 2
        if suite.tier == 2:
            dim_scores: dict[str, list[float]] = {}
            for case in suite.cases:
                for dim in case.dimensions:
                    dim_scores.setdefault(dim.name, []).append(dim.score)

            if dim_scores:
                lines.append("### Dimension Averages")
                lines.append("")
                lines.append("| Dimension | Avg Score | Min | Max |")
                lines.append("|-----------|-----------|-----|-----|")
                for name, scores in dim_scores.items():
                    avg = sum(scores) / len(scores)
                    lines.append(
                        f"| {name} | {avg:.2f} | {min(scores):.2f} | {max(scores):.2f} |"
                    )
                lines.append("")

    return "\n".join(lines)
