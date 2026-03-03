# Eval Gate Protocol

Run the GRIM evaluation suite as a quality gate with regression detection.

## Phase 1: Setup

1. Determine tier scope from user input (default: Tier 1 only for speed)
2. Check that eval datasets exist at `eval/datasets/`
3. If `compare=true`, locate the most recent baseline run in `eval/results/`

## Phase 2: Execute

1. Run the evaluation:
   ```bash
   cd GRIM && python -m eval.cli run --tier {tier}
   ```
   Or via Python:
   ```python
   from eval.engine.runner import EvalRunner
   from eval.config import EvalConfig
   runner = EvalRunner(EvalConfig())
   result = await runner.run(tier=tier, categories=categories)
   ```

2. Capture the run result including:
   - `overall_score`, `pass_rate`, `total_cases`, `total_passed`
   - Per-suite breakdown (category, score, passed/total)
   - Any errors or failures

## Phase 3: Compare (if enabled)

1. Load the most recent previous run from `eval/results/`
2. Run regression detection:
   ```python
   from eval.engine.comparator import compare_runs, find_latest_run
   baseline = find_latest_run(config.results_dir)
   if baseline:
       comparison = compare_runs(baseline, result)
   ```
3. Classify regressions:
   - **Critical**: score drop > 30%
   - **Major**: score drop > 15%
   - **Minor**: score drop > 5%

## Phase 4: Gate Decision

Apply quality gates in order:

1. **Gate 1**: Tier 1 score >= 95%
   - If Tier 1 was run and score < 95%, gate FAILS
2. **Gate 2**: No critical regressions
   - Any regression with delta > -0.30 causes immediate FAIL
3. **Gate 3**: No major regressions without acknowledgment
   - Regressions with delta > -0.15 cause FAIL (user can override)
4. **Gate 4**: Tier 2 score >= 70% (if Tier 2 was included)
   - If Tier 2 was run and score < 70%, gate FAILS

## Phase 5: Report

Present results in a clear summary:

```
EVAL GATE: {PASSED|FAILED}
─────────────────────────
Overall Score: {score}%
Pass Rate:     {passed}/{total}
Duration:      {duration}s
Run ID:        {run_id}

Suites:
  routing:         {score}% ({passed}/{total})
  skill_matching:  {score}% ({passed}/{total})
  tool_groups:     {score}% ({passed}/{total})
  keyword_routing: {score}% ({passed}/{total})

{if regressions}
Regressions ({count}):
  [{severity}] {case_id}: {base}% → {target}% ({delta}%)
{endif}

{if improvements}
Improvements ({count}):
  {case_id}: {base}% → {target}% (+{delta}%)
{endif}
```

## Integration with ship-it

When called from the ship-it skill:
- Run as Gate 0 (before unit tests)
- Use `tier=1` for speed (Tier 1 is deterministic, no LLM needed)
- If eval gate fails, abort the ship-it pipeline
- Report which cases regressed so the developer can fix them
