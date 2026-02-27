# FDO Source Path Validation — Claude Skill Protocol

> **Skill**: `fdo-source-validate`
> **Version**: 1.0
> **Purpose**: Validate that all `source_paths` in FDO frontmatter resolve to real files or directories.
> **When to use**: After adding source_paths to FDOs, after reorganizing repos, or as a periodic integrity check.

---

## Prerequisites

Before starting, confirm you have:
- [ ] Access to the Kronos vault (default: `kronos-vault/`)
- [ ] Access to the workspace root containing all repos referenced in source_paths
- [ ] Understanding of the source_paths schema (see FDO Schema v1.1)

---

## Phase 1: Scan

**Goal**: Load all FDOs and extract source_paths.

### Steps

1. Use `kronos_list` (optionally filtered by `domain`) to get all FDOs
2. For each FDO, extract:
   - `source_paths` (list of `{repo, path, type}` objects)
   - `source_repos` (for cross-reference)
3. Categorize FDOs into:
   - **Has source_paths**: Non-empty list
   - **Empty source_paths**: Explicit `[]`
   - **Missing source_paths**: Field not present (pre-v1.1 FDOs)

### Output

A structured list of all source_paths to validate, grouped by repo.

---

## Phase 2: Resolve

**Goal**: Check each source_path against the filesystem.

### Path resolution rules

For each source_path entry `{repo, path, type}`:

1. **Resolve repo to filesystem path**: Look for `{workspace_root}/{repo}/` directory
2. **Resolve full path**: `{workspace_root}/{repo}/{path}`
3. **Check existence**:
   - If `type` is `experiment`: expect a directory with meta.yaml inside
   - If `type` is `script`: expect a file (usually .py)
   - If `type` is `module`: expect a directory (may have __init__.py or setup.py)
   - If `type` is `doc`: expect a file (usually .md)
   - If `type` is `config`: expect a file (usually .yaml/.json/.toml)
   - If `type` is `data`: expect a file or directory

### Record for each path

- `status`: valid | broken | warning
- `reason`: why it's broken (e.g., "directory not found", "repo not found", "experiment missing meta.yaml")

---

## Phase 3: Report

**Goal**: Produce a validation report with coverage statistics.

### Report Format

```
## FDO Source Path Validation Report

**Date**: [date]
**Scope**: [domain filter or "all domains"]
**Workspace**: [workspace root]

### Summary
- Total FDOs checked: N
- FDOs with source_paths: N (X%)
- FDOs without source_paths: N (X%)
- Total source_paths: N
- Valid paths: N (X%)
- Broken paths: N (X%)

### Broken Paths

| FDO | Repo | Path | Type | Issue |
|-----|------|------|------|-------|
| fdo-id | repo-name | path/to/thing | experiment | Directory not found |

### Coverage by Repo

| Repo | Total paths | Valid | Broken | Types |
|------|-------------|-------|--------|-------|
| dawn-field-theory | 40 | 39 | 1 | experiment(35), doc(4), script(1) |

### FDOs Missing source_paths

These FDOs have source_repos but no source_paths — candidates for enrichment:

| FDO | Domain | source_repos |
|-----|--------|-------------|
| fdo-id | physics | [dawn-field-theory] |

### Quality Gate Results

- [ ] Zero broken source_paths
- [ ] Every FDO with source_repos also has source_paths
- [ ] All experiment-type paths point to directories with meta.yaml
```

---

## Notes

- Repos are resolved relative to the workspace root (e.g., `dawn-field-theory/` is `{workspace}/dawn-field-theory/`)
- A source_path with `path: .` means the repo root — just check the repo directory exists
- Some FDOs legitimately have no source_paths (pure theory/proof FDOs) — these are reported but not flagged as errors
- This skill is read-only — it reports issues but does not fix them
- Use `kronos_deep_dive` to interactively explore source_paths for a specific concept
