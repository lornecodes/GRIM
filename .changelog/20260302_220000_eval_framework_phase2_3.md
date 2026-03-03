# Eval Framework — Phases 2 & 3

**Date**: 2026-03-02
**Version**: 0.0.5.4
**Scope**: eval, server, ui, skills

## Overview

Complete evaluation framework with server API, web UI, and eval-gate skill. Builds on Phase 1's engine, datasets, and 56 tests.

## Server API (12 endpoints)

- `POST /api/eval/run` — Start eval run as background asyncio.Task
- `GET /api/eval/run/{run_id}` — Poll run status + partial results
- `GET /api/eval/runs` — List all saved runs (includes `suite_scores` for history chart)
- `GET /api/eval/results/{run_id}` — Full result JSON
- `GET /api/eval/compare` — Regression comparison between two runs
- `GET /api/eval/datasets` — List datasets with case counts
- `GET /api/eval/datasets/{tier}/{category}` — Get dataset content (YAML to JSON)
- `POST /api/eval/datasets/{tier}/{category}/cases` — Append a case
- `PUT /api/eval/datasets/{tier}/{category}/cases/{case_id}` — Update a case
- `DELETE /api/eval/datasets/{tier}/{category}/cases/{case_id}` — Delete a case
- `WS /ws/eval/{run_id}` — Stream case_start/case_end/complete events
- `GET /api/eval/datasets/{tier}/{category}/export` — Export dataset as YAML

## Web UI — Tabbed Dashboard

4 tabs: **Run** | **Results** | **History** | **Datasets**

- **Run tab**: Tier selector buttons, category filter checkboxes, score cards, live WebSocket progress
- **Results tab**: Run selector, case table with expandable details, compare/regression view with severity badges
- **History tab**: Recharts AreaChart with overall score + per-suite dashed breakdown lines
- **Datasets tab**: Browse datasets, expandable case details, inline JSON editing, add/delete cases

## New Skill: eval-gate

Quality gate protocol (`skills/eval-gate/`) with 5 phases:
1. Setup — determine tier scope, locate baseline
2. Execute — run EvalRunner
3. Compare — regression detection (critical >30%, major >15%, minor >5%)
4. Gate Decision — Tier 1 >= 95%, no critical regressions, Tier 2 >= 70%
5. Report — structured summary

Integrates with ship-it as Gate 0.

## Bug Fix

- **Pass rate showing 0/162**: EvalRunTab.tsx read `passed_cases` but raw EvalRun JSON uses `total_passed`. Fixed field name in stats type.

## Files Changed

- `server/app.py` — 12 eval endpoints + WebSocket
- `eval/engine/runner.py` — progress_callback support
- `ui/src/hooks/useEval.ts` — fetch/mutation hook
- `ui/src/components/pages/EvalDashboard.tsx` — tab shell
- `ui/src/components/eval/EvalRunTab.tsx` — run controls + filters
- `ui/src/components/eval/EvalResultsTab.tsx` — results + compare view
- `ui/src/components/eval/EvalHistoryTab.tsx` — score history chart
- `ui/src/components/eval/EvalDatasetsTab.tsx` — dataset browser + CRUD
- `ui/src/components/NavIcons.tsx` — IconEval
- `ui/src/config/PageRegistry.ts` — eval page registration
- `skills/eval-gate/manifest.yaml` + `protocol.md` — new skill
- `Dockerfile` — COPY eval/ eval/

## Documentation

- ADR: `adr-grim-eval-framework` in kronos-vault/decisions/
- Updated FDOs: grim-architecture, proj-grim, feat-grim-eval, adr-docker-release-pipeline

## Metrics

- 56 eval tests (Phase 1), all passing
- 961 unit tests passed at release gate
- 41 integration tests passed
- 162 Tier 1 cases at 100%, 43 Tier 2 cases ready
