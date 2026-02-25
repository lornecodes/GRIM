# Prompt Tuning Framework & Actualization Pipeline Improvements

**Date**: 2026-02-24 20:58
**Type**: engineering

## Summary

Built a complete prompt tuning framework for the GRIM actualization pipeline, manually tuned the extract agent to 100% accuracy, fixed critical code bugs in the extraction node, and successfully validated the improved pipeline by ingesting the cip-test-repo into the Kronos vault (12 FDOs, 0 errors, $0.21).

## Changes

### Added
- `tools/tuning/` — Complete prompt tuning framework
  - `__main__.py` — CLI with `run`, `eval`, `show` commands, validation gate loop, rejection capture
  - `optimizer.py` — Claude-powered surgical prompt editor with AGENT_TUNABLE mapping, rejection feedback display
  - `evaluator.py` — 5 per-agent scorers with weighted criteria, substring matching
  - `runner.py` — Runs actual agent code against test cases, captures outputs
  - `tracker.py` — Persistent JSON history with loss curves, rejected_changes/accuracy/cases tracking, convergence/stall detection
  - `config.py` — STALL_PATIENCE=4, MIN_ITERATIONS=3, MAX_ITERATIONS=10
  - `cases/extract_cases.py` — 8 golden-output test cases covering: YAML config, Python code, Markdown docs, mixed-content, rich domain code, edge cases (acronyms, minimal input, empty strings)
  - `mock_repo/` — 12+ diverse files spanning domain edge cases for realistic testing
  - `results/` — Persistent tuning run results (JSON)
- `.changelog/` — Session changelog folder

### Changed
- `tools/actualization/prompts.py` — Restructured into templated architecture:
  - Fixed frame: `_HEADER` (input template + JSON schema) + `_FOOTER` (closing instructions)
  - Tunable sections: `RULES` (extraction priorities) + `EXAMPLES` (input/output demonstrations)
  - Assembled `USER` prompts used by node imports
  - EXTRACT_RULES: 8 priority-ordered rules — (1) acronyms in BOTH concepts+entities, (2) scan full text for all acronyms, (3) class/var names → human-readable, (4) math constants, (5) function semantics, (6) file-type keywords, (7) never expand acronyms, (8) never fabricate
  - EXTRACT_EXAMPLES: 5 detailed input→output pairs with explicit notes explaining extraction logic
  - Header updated: domain acronyms explicitly noted for BOTH lists
- `tools/actualization/nodes/extract.py` — Bug fixes:
  - `entities[:5]` → `entities[:10]` — was silently dropping valid entities (test files have 9+ acronyms)
  - `max_tokens=300` → `max_tokens=400` — was truncating responses for rich extraction
- `kronos-vault/repos/cip-test-repo/` — 5 FDO files generated from improved pipeline:
  - Root `_index.md` with PAC hierarchy spanning 7 children directories
  - `.cip/test-repo-cip-core.md` — Detailed YAML schema analysis with directory mapping
  - `.github/_index.md` — Automation infrastructure directory index
  - `.github/workflows/test-repo-github-workflows-cip-metadata-update.md` — Workflow analysis with step-by-step details
  - `cognition/test-repo-cognition-meta.md` — Validation framework metadata

### Fixed
- Module cache stale prompts — `importlib.reload()` on prompts + node modules after writes
- File corruption from regex writes — String splice instead of regex for `write_prompt()`
- Brace sanitization — Double ALL braces → selectively un-double KNOWN_PLACEHOLDERS
- Premature convergence — MIN_ITERATIONS=3, rejection-aware stall checks
- Optimizer flying blind — Added AGENT_CONTEXT, input summaries, golden outputs to meta-prompt
- Full prompt rewrites causing oscillation — Templated prompts with surgical editing
- Missing rejection feedback — Store and display rejected attempts in optimizer meta-prompt

## Details

### Tuning Architecture

The framework uses a generate-evaluate-optimize loop:

```
eval → score cases → optimizer generates surgical edits → validate → accept/reject → repeat
```

Key design decisions:
1. **Templated prompts**: Fixed frame (`_HEADER` + `_FOOTER`) is never modified. Only `RULES` and `EXAMPLES` sections are tunable. This prevents the optimizer from accidentally breaking prompt structure.
2. **Surgical editing**: The optimizer sees the full assembled prompt as READ-ONLY context, but can only modify the designated tunable sections. This constrains changes to meaningful improvements.
3. **Rejection feedback**: When the optimizer produces a regression, the rejected text and specific failing cases are stored and shown in the next iteration's meta-prompt. This prevents repeating the same mistakes.
4. **Validation gate**: Changes must improve or match accuracy. Regressions are rolled back with full context preserved.

### Manual Tuning Results

The automated optimizer converged at 88.4% — making timid, non-meaningful changes. Manual tuning achieved:
- **Run 1**: 100% (all 8 cases perfect)
- **Run 2**: 100%
- **Run 3**: 98.8% (stochastic LLM variance at temperature 0.1 — golden ratio missed once)

The breakthrough was priority-ordered rules with explicit "BOTH lists" instruction for acronyms, plus detailed examples that demonstrated the exact extraction logic for each pattern type.

### Pipeline Validation Results

Scan of cip-test-repo with improved prompts:
- **FDOs Created**: 12 (3 file-level + 9 directory indices)
- **Cross-links**: 11 bidirectional connections
- **Files Skipped**: 6 (empty, boilerplate, LICENSE) — all correct decisions
- **Errors**: 0
- **Cost**: $0.21
- **Quality**: Proper YAML frontmatter, PAC hierarchy, Obsidian `[[wikilinks]]`, detailed content analysis
- **Known issue**: CIP expanded as "Cardano Improvement Proposal" instead of "Cognition Index Protocol" — domain context issue in the actualize agent, not extraction

### Code Bugs Found

The `entities[:5]` hard cap was silently dropping valid entities. Test cases with 9+ acronyms would have 4 truncated. The `max_tokens=300` limit was causing truncation for files with rich extraction content. Both were invisible in simple test files.

## Related
- `tools/actualization/` — The pipeline these prompts power
- `kronos-vault/` — The Obsidian vault receiving FDOs
- Previous session: Initial actualization pipeline build (12 FDOs, $0.20, 0 errors)
