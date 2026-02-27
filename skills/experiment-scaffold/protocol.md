# Experiment Scaffold — Claude Skill Protocol

> **Skill**: `experiment-scaffold`
> **Version**: 1.0
> **Purpose**: Generate a new experiment directory with correct structure following STANDARDS.md.
> **When to use**: When starting a new experiment to ensure it has proper structure from the beginning.

---

## Prerequisites

Before starting, confirm you have:
- [ ] An experiment **name** (snake_case)
- [ ] A **title** (human-readable)
- [ ] A **hypothesis** (one sentence)
- [ ] The **pillar** it falls under (PAC, SEC, RBF, MED, or cross-domain)
- [ ] Optionally: related FDO IDs

---

## Phase 1: Validate

**Goal**: Ensure the experiment can be created without conflicts.

### Steps

1. **Check name format**: Must be valid snake_case (lowercase, underscores, no spaces)
2. **Check for conflicts**: Ensure `{parent_dir}/{name}/` does not already exist
3. **Verify parent directory**: Ensure `parent_dir` exists
4. **Validate FDO links**: If `related_fdos` provided, verify each FDO ID exists in the vault

If any validation fails, report the issue and stop.

---

## Phase 2: Scaffold

**Goal**: Create the directory structure and template files.

### Directory structure

```
{name}/
├── meta.yaml
├── README.md
└── scripts/
```

Do NOT create `results/` or `journals/` — those get created when there's actual content for them.

### meta.yaml template

```yaml
schema_version: '2.0'
directory_name: {name}
title: {title}
description: {hypothesis}
status: active
pillar: {pillar}
hypothesis: {hypothesis}
validation_type: automated
estimated_context_weight: 0.5
tags: []
```

### README.md template

```markdown
# {title}

## Hypothesis

{hypothesis}

## Status

**Active** — experiment in progress.

## Approach

_Describe the experimental approach here._

## Key Results

_No results yet._

## Related FDOs

{for each fdo_id: "- [[{fdo_id}]]"}
{if no fdos: "- _None linked yet._"}

## Scripts

| Script | Description |
|--------|-------------|
| _None yet_ | |
```

### scripts/ directory

Create the directory. If the experiment name suggests a first script, create a minimal starter:

```python
"""
{title} — Experiment Script 01

Hypothesis: {hypothesis}
"""


def main():
    pass


if __name__ == "__main__":
    main()
```

Name it `exp_01_{name_abbreviated}.py`.

---

## Phase 3: Link

**Goal**: Update references so the new experiment is discoverable.

### Steps

1. **Update experiments README**: Add the new experiment to the appropriate section (Active Research) in `foundational/experiments/README.md`
2. **Update FDO source_paths** (optional): If `related_fdos` were provided, add a source_path entry to each FDO pointing to the new experiment directory
3. **Report**: Confirm what was created

### Report Format

```
## Experiment Scaffolded

**Name**: {name}
**Path**: {parent_dir}/{name}/
**Pillar**: {pillar}

### Files Created
- meta.yaml
- README.md
- scripts/exp_01_{abbrev}.py

### Links Updated
- experiments/README.md: Added to Active Research section
- FDO {fdo_id}: Added source_path entry
```

---

## Notes

- Keep the scaffold minimal — only create what's needed. Empty placeholder files or directories add noise.
- The meta.yaml `status` defaults to `active` — the experiment is being worked on.
- Script naming follows `exp_NN_description.py` convention.
- If the user provides additional context about the experiment, incorporate it into the README approach section.
