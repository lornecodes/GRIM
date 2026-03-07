# Execution-Aware Coding Agent + Domain Skill Injection

**Type:** enhancement
**Scope:** daemon context, pool audit, pool slot

## What Changed

### Execution-Aware Coding Agent
- Enhanced CODE system prompt in `slot.py` to instruct agents to execute scripts/experiments and report results
- Added `python *.py` to `SAFE_BASH_PATTERNS` in `audit.py` so pool agents can run Python scripts
- Added execution detection (`_is_execution_story`) in `context.py` — dual mechanism: tag match (experiment, run, execute, benchmark, validate) + keyword regex on title/description
- When execution is detected, a structured run protocol is injected into agent instructions

### Domain Skill Injection via Tags
- Added `_SKILL_CARDS` registry in `context.py` — 6 compressed domain knowledge cards (~500-900 chars each):
  - `experiment` — folder structure, meta.yaml, results format
  - `dft`/`physics` — four pillars, key constants, epistemic stance
  - `spec` — spec-driven development workflow
  - `changelog` — .changelog/ format and conventions
  - `vault-sync`/`vault` — mandatory vault sync rules
  - `library`/`module` — test patterns, type hints, structure
- Story tags auto-match to skill cards; concatenated up to `MAX_SKILL_CHARS=2500`
- `MAX_CHARS` bumped from 8000 → 12000 to accommodate new sections

### Build Pipeline
- Two new sections in `ContextBuilder.build()`: execution (600 char budget) and skills (2500 char budget)
- Sections ordered: header → boundaries → research → execution → skills → ADR context → orientation → snippets

## Files Modified
- `core/pool/audit.py` — +1 safe bash pattern
- `core/pool/slot.py` — +2 lines to CODE prompt
- `core/daemon/context.py` — +~120 lines (skill cards, execution detection, resolvers, MAX_CHARS bump)
- `tests/test_daemon_context.py` — +~150 lines (27 new tests)
- `tests/test_pool_audit.py` — updated safe/non-safe test categorization

## Tests
- 77 context tests (50 existing + 27 new), 81 audit tests — all passing
- 241 broader daemon/pool tests — no regressions
