# Vault Sync — Claude Skill Protocol

> **Skill**: `vault-sync`  
> **Version**: 1.0  
> **Purpose**: Keep Kronos vault FDOs current as code evolves in source repositories.  
> **When to use**: After meaningful code changes — new features, refactors, architecture shifts, experiment results. Not for trivial fixes.

---

## Prerequisites

Before starting, confirm you have:
- [ ] Access to the **repository** that changed
- [ ] Access to the **Kronos vault** (default: `kronos-vault/`)
- [ ] The **FDO schema** loaded (see `kronos-vault/ai-systems/kronos/kronos-fdo-schema.md`)
- [ ] Knowledge of **what changed** (git diff, conversation context, or user description)

---

## When to Trigger

Run this skill when:
- A meaningful feature or module was added/changed
- Architecture or interfaces shifted
- Experiment results are in
- A new tool, service, or integration was built
- Status of a project changed significantly

Do NOT trigger for:
- Typo fixes, formatting changes
- Dependency bumps with no behavioral change
- Work-in-progress that isn't committed

---

## Phase 1: Change Detection

**Goal**: Understand what changed and what it means.

### Steps

1. **Identify changed files** — Use `git diff --stat`, conversation context, or user description
2. **Categorise the change**:
   - `new_concept` — Something that didn't exist before (new module, feature, experiment)
   - `evolution` — Existing concept changed significantly (refactor, new capability)
   - `status_change` — Project status shifted (seed→developing, experiment passed/failed)
   - `deprecation` — Something was removed or superseded
3. **Map to FDO impact** — Which existing FDOs are affected? Are new ones needed?

### Output: Change Impact Assessment

```
## Change Impact: [brief description]

**Repo**: [repo name]
**Change type**: new_concept | evolution | status_change | deprecation

### Affected FDOs
| FDO ID | Impact | Action |
|--------|--------|--------|
| existing-fdo-id | what changed | UPDATE field X / UPDATE section Y |

### New FDOs Needed
| Proposed ID | Title | Why |
|-------------|-------|-----|
| new-fdo-id | Title | What new concept emerged |

### No Action Needed
- [file/change] — too minor / already covered by [fdo-id]
```

> **CHECKPOINT**: Present the Change Impact Assessment. Proceed only after user confirms.

---

## Phase 2: Vault Scan

**Goal**: Read current state of affected FDOs before modifying.

### Steps

1. **Read each affected FDO** — Full content, not just frontmatter
2. **Check for stale information** — Does the FDO say something now contradicted by the code change?
3. **Check confidence scores** — Should confidence change? (e.g., experiment validated → confidence up; refactor broke something → confidence down)
4. **Check status** — Should status advance? (seed→developing if first implementation; developing→stable if validated)
5. **Check related links** — Should new connections be added?

### Staleness Markers

Look for these red flags in existing FDOs:
- **Version mismatch**: FDO says "v1" but code is now "v2"
- **Feature claims**: FDO claims a feature that was removed/changed
- **Status lag**: Code has been validated but FDO still says "seed"
- **Missing connections**: New module relates to existing FDOs but isn't linked
- **Outdated counts/metrics**: FDO cites old numbers (e.g., "12 tools" but now there are 15)

---

## Phase 3: Apply Updates

**Goal**: Make minimal, precise edits to bring FDOs current.

### Update Rules

1. **Minimal diff** — Change only what's stale. Don't rewrite prose that's still accurate.
2. **Preserve voice** — Match the existing FDO's tone and detail level.
3. **Update `updated:` date** — Always bump this in frontmatter.
4. **Adjust confidence** — Only when evidence changed:
   - Experiment passed → +0.1 to +0.2
   - Experiment failed → -0.1 to -0.2
   - New cross-domain validation → +0.1
   - Code deleted/deprecated → set to 0.0 and status→archived
5. **Adjust status** — Only when lifecycle stage changed:
   - First implementation → `developing`
   - Validated with tests → `stable`
   - Replaced by new approach → `archived` + set `superseded_by`
6. **Add connections** — If new concepts relate to existing FDOs:
   - Add to `related:` in frontmatter (both directions)
   - Add `[[wikilink]]` in Connections section
7. **Update source reference** — If file paths changed, update References section

### For New FDOs

Follow the same quality gates as `deep-ingest` Phase 4:

- [ ] **Standalone summary** — someone reading only this FDO understands the concept
- [ ] **Core claim is precise** — not vague, includes what's novel
- [ ] **Evidence with bounds** — quantitative where possible, with uncertainty
- [ ] **2+ wikilinks** — connected to the graph
- [ ] **Justified confidence** — `confidence_basis` explains the score
- [ ] **Established vs speculative** — clearly separated if applicable

### For Deprecations

When code is removed or replaced:
1. Set `status: archived` in frontmatter
2. Set `superseded_by: new-fdo-id` if applicable
3. Add a note at top of Summary: `> **Archived**: Superseded by [[new-fdo-id]] on YYYY-MM-DD.`
4. Do NOT delete the FDO — archived knowledge is still valuable

---

## Phase 4: Graph Integrity

**Goal**: Ensure updates didn't break the relationship graph.

### Checks

1. **Bidirectional links** — Every `related:` entry in FDO A must have A in FDO B's `related:`
2. **No dead wikilinks** — Every `[[link]]` in the body resolves to an actual file
3. **PAC hierarchy** — If `pac_parent` or `pac_children` changed, verify the tree is consistent
4. **No orphans** — New FDOs must have at least 2 connections

### Quick Validation Script

```powershell
# Check all wikilinks resolve
$allIds = Get-ChildItem "kronos-vault\**\*.md" -Recurse | ForEach-Object { $_.BaseName }
Get-ChildItem "kronos-vault\**\*.md" -Recurse | ForEach-Object {
    $n = $_.Name; $c = Get-Content $_.FullName -Raw
    $links = [regex]::Matches($c, '\[\[([^\]]+)\]\]') | ForEach-Object { $_.Groups[1].Value }
    $broken = $links | Where-Object { $_ -notin $allIds }
    if($broken) { Write-Output "$n : BROKEN: $($broken -join ', ')" }
}
```

---

## Phase 5: Confirmation

**Goal**: Report what changed and verify with user.

### Output: Sync Report

```
## Vault Sync Report

**Trigger**: [what code change triggered this]
**Date**: YYYY-MM-DD

### Updated FDOs
| FDO | Changes | Confidence Δ | Status Δ |
|-----|---------|--------------|----------|
| id  | what changed | 0.6→0.7 | seed→developing |

### Created FDOs
| FDO | Domain | Status | Confidence |
|-----|--------|--------|------------|
| id  | domain | status | 0.X |

### Archived FDOs
| FDO | Reason | Superseded By |
|-----|--------|---------------|
| id  | why | new-id |

### Graph Changes
- Added N new links
- All wikilinks resolve: ✓/✗
- Bidirectional check: ✓/✗
```

---

## Appendix A: Common Sync Patterns

### New module added to a repo
1. Create one FDO for the module concept
2. Link to the repo's overview FDO (if one exists)
3. Link to related modules
4. Set status: `seed` or `developing` depending on completeness

### Experiment completed
1. Update the experiment FDO with results
2. Adjust confidence based on outcome (up for validation, down for failure)
3. Update any theory FDOs that the experiment tested
4. If falsified, mark clearly with `❌` and update confidence_basis

### Architecture refactor
1. Update the architecture FDO
2. Check all FDOs that reference the old structure
3. Update file paths in References sections
4. If components were renamed, update IDs and all references

### New skill or tool built
1. Create FDO in `tools/` or `ai-systems/` domain
2. Link to the system it's part of
3. Update the parent system's FDO (e.g., add to grim-skills list)
4. Set status: `developing` if functional, `seed` if specced only

### Code deleted
1. Archive the FDO (don't delete)
2. Set `superseded_by` if replacement exists
3. Remove from `pac_children` of parent
4. Keep `related:` links intact (archived nodes are still part of the graph)

## Appendix B: Confidence Adjustment Guide

| Event | Adjustment | Example |
|-------|-----------|---------|
| First implementation works | +0.1 | seed 0.3 → 0.4 |
| Unit tests passing | +0.1 | 0.4 → 0.5 |
| Integration test passing | +0.1 | 0.5 → 0.6 |
| Cross-domain validation | +0.1 to +0.2 | 0.6 → 0.7 |
| Paper published with results | +0.1 | 0.7 → 0.8 |
| Independent replication | +0.1 | 0.8 → 0.9 |
| Test failure | -0.1 to -0.2 | depends on severity |
| Falsified | Set to ≤ 0.2 | honest marking |
| Deprecated (working but superseded) | Keep current | just archive |

## Appendix C: What NOT to Sync

- **Build artifacts**: node_modules, __pycache__, dist/ — not knowledge
- **Config-only changes**: .env, CI/CD tweaks — unless architecture shifted
- **WIP branches**: Only sync from main/stable branches
- **Style changes**: Linting, formatting — doesn't change concepts
- **Dependency updates**: Unless they change capabilities or constraints
