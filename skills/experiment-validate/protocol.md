# Experiment Validation — Claude Skill Protocol

> **Skill**: `experiment-validate`
> **Version**: 1.0
> **Purpose**: Validate experiment folder structure against STANDARDS.md conventions.
> **When to use**: After creating or reorganizing experiments, before committing, or as a periodic health check.

---

## Prerequisites

Before starting, confirm you have:
- [ ] Access to the experiments directory (default: `dawn-field-theory/foundational/experiments/`)
- [ ] Familiarity with the experiment standard (see STANDARDS.md § Experiment Structure)

---

## Phase 1: Discover

**Goal**: Identify which experiments to validate.

### Steps

1. If `experiment_path` is provided, validate only that single experiment
2. Otherwise, list all subdirectories in `experiments_root`
3. Exclude shared directories: `journals/`, `results/`, `legacy/`
4. Identify container experiments (directories with sub-experiments that have their own meta.yaml)

### Output

A list of experiment directories to check, with a note on which are containers.

---

## Phase 2: Check

**Goal**: Run all compliance checks against each experiment.

### Scoring (1-5 scale)

| Score | Criteria |
|-------|----------|
| 5 | meta.yaml + README.md + scripts/ + results/ + journals/ |
| 4 | meta.yaml + README.md + scripts/ + (results/ OR journals/) |
| 3 | meta.yaml + README.md + scripts/ |
| 2 | meta.yaml + README.md |
| 1 | meta.yaml only |
| 0 | No meta.yaml (critical failure) |

### Checks per experiment

Run these checks and record pass/fail for each:

#### Required (affects score)
- [ ] **meta.yaml exists** — Must be present and parse as valid YAML
- [ ] **meta.yaml schema** — Must have `schema_version` field (should be '2.0')
- [ ] **README.md exists** — Must be present and non-empty (>10 bytes)
- [ ] **scripts/ directory** — Should exist if the experiment has .py files
- [ ] **results/ directory** — Should exist if the experiment has output files
- [ ] **journals/ directory** — Should exist if the experiment has journal entries

#### Warnings (reported but don't affect score)
- [ ] **No loose .py files** — Python files should be inside scripts/, not at experiment root
- [ ] **Script naming** — Files in scripts/ should follow `exp_NN_description.py` pattern (warn on non-matching names, don't fail)
- [ ] **meta.yaml completeness** — Check for recommended fields: title, status, hypothesis, pillar, tags
- [ ] **README completeness** — Check for recommended sections: hypothesis/description, status, key results

#### Container experiment rules
For container experiments (e.g., biology_experiments, quantum_validation):
- The container itself needs meta.yaml + README.md (score 2 minimum)
- Sub-experiments inside follow the same rules independently
- The container's score is based on its own files, not sub-experiments

---

## Phase 3: Report

**Goal**: Produce a clear compliance report.

### Report Format

```
## Experiment Validation Report

**Date**: [date]
**Scope**: [single experiment or "all N experiments"]
**Standard**: STANDARDS.md v1.0

### Summary
- Total experiments: N
- Score 5 (fully compliant): N
- Score 4: N
- Score 3: N
- Score 2: N
- Score 1: N
- Score 0 (critical): N
- Average score: X.X/5

### Issues

#### Critical (score 0)
- [experiment]: Missing meta.yaml

#### Warnings
- [experiment]: Loose .py files at root (should be in scripts/)
- [experiment]: Script 'foo.py' doesn't follow exp_NN naming convention
- [experiment]: meta.yaml missing recommended field 'hypothesis'

### Per-Experiment Scores
| Experiment | Score | meta.yaml | README | scripts/ | results/ | journals/ | Warnings |
|------------|-------|-----------|--------|----------|----------|-----------|----------|
| name       | 4/5   | ✓         | ✓      | ✓        | ✓        | -         | 0        |
```

### Quality gate check

After producing the report, verify:
- [ ] No experiment scores below 2/5
- [ ] Average score >= 3.5/5
- [ ] No critical issues (score 0)

If quality gates fail, list the specific experiments that need attention.

---

## Notes

- Container experiments (biology_experiments, quantum_validation, entropy_information_polarity_field) are scored on their own root-level files only
- The `legacy/` directory is excluded — those are archived pre-structured simulations
- Empty directories (created as placeholders) should NOT be penalized — only check for directories when there's content that belongs in them
- This skill is read-only — it reports issues but does not fix them

## Vault Sync

If validation findings led to fixes or structural changes, run the `vault-sync` skill to update affected FDOs (especially project trackers and experiment-related FDOs).
