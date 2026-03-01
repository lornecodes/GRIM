# Source Navigate — Concept to Code Navigation

> **Skill**: `source-navigate`
> **Version**: 1.0
> **Purpose**: Navigate from vault concepts (FDOs) to actual source code, experiments, and documentation using the full MCP tool chain.

---

## When This Applies

Activate this skill when:
- User asks "find the code for X", "where is Y implemented", "show me the source for Z"
- User asks "search the experiments for X", "grep for Y in the physics code"
- User wants to understand how a concept is implemented in the codebase
- You need to inspect source material referenced by an FDO

---

## Phase 1: Discover the Concept

**Goal**: Find the FDO that represents the concept.

### Steps

1. Use `kronos_search` to find the FDO:
   ```
   kronos_search(query="PAC conservation", semantic=true)
   ```

2. If the user gave an exact FDO ID, skip search — you can use it directly in Phase 2.

3. If multiple results, pick the most relevant by checking `confidence`, `status`, and `tags`.

### Output

- FDO ID (e.g., `pac-comprehensive`)
- FDO title and summary for context

---

## Phase 2: Map Source Paths

**Goal**: Gather all source material paths for the concept, enriched with meta.yaml context.

### Steps

1. Use `kronos_deep_dive` to collect source_paths:
   ```
   kronos_deep_dive(query="pac-comprehensive", depth=1)
   ```

2. Review the `sources_by_repo` output — this groups paths by repository:
   - `dawn-field-theory`: experiments, docs, preprints
   - `fracton`: library modules
   - `reality-engine`: simulator code, POCs
   - `dawn-models`: ML models

3. Note the `type` of each source_path:
   - `experiment` — experiment directory with scripts/results
   - `module` — Python module or package
   - `script` — standalone script
   - `doc` — documentation file or directory
   - `config` — configuration file
   - `data` — data file

4. If the source_paths have `meta` enrichment (description, status, semantic_scope), use it to prioritize which sources to explore.

### Output

- List of source_paths grouped by repo
- Priority ordering based on meta.yaml enrichment

---

## Phase 3: Browse and Read

**Goal**: Navigate directory structure and read specific files.

### For directories — use `kronos_navigate`:
```
kronos_navigate(path="dawn-field-theory/foundational/experiments/milestone1")
```
This returns the meta.yaml description, file listing, and child directories.

### For files — use `kronos_read_source`:
```
kronos_read_source(repo="fracton", path="fracton/core/pac_regulation.py")
```

### Pagination for large files:
```
kronos_read_source(repo="fracton", path="fracton/core/pac_regulation.py", offset=0, max_lines=200)
kronos_read_source(repo="fracton", path="fracton/core/pac_regulation.py", offset=200, max_lines=200)
```

### Common file targets:
- `README.md` — experiment overview
- `SYNTHESIS.md` — experiment results summary
- `meta.yaml` — directory metadata
- `scripts/exp_*.py` — experiment scripts
- `results/*.json` — experiment results

---

## Phase 4: Search Source Content

**Goal**: Find specific patterns, functions, or constants within source files.

### Basic search — grep across an FDO's sources:
```
kronos_search_source(query="pac-comprehensive", pattern="def ")
```
This finds all function definitions in the PAC comprehensive source files.

### Search with depth — include related FDOs' sources:
```
kronos_search_source(query="pac-comprehensive", pattern="phi", depth=1)
```

### Filter by source type:
```
kronos_search_source(query="sec-topological-dynamics", pattern="entropy", type_filter="module")
```

### Adjust context:
```
kronos_search_source(query="milestone1-sm-derivation", pattern="alpha", context_lines=5)
```

---

## MCP Tools Reference

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `kronos_search` | Find FDOs by concept | Starting point — discover the right FDO |
| `kronos_deep_dive` | Gather source_paths with meta.yaml | Map all source material for a concept |
| `kronos_navigate` | Read directory meta.yaml | Understand what a directory contains |
| `kronos_read_source` | Read file content | Inspect specific source files |
| `kronos_search_source` | Grep across FDO sources | Find patterns/functions in source material |

### Typical Flow

```
kronos_search → kronos_deep_dive → kronos_read_source / kronos_search_source
     |                |                      |
  Find concept    Map sources         Read/search files
```

---

## Rules

1. Always start with `kronos_search` or `kronos_deep_dive` — don't guess file paths
2. Use `kronos_navigate` before `kronos_read_source` if you're exploring a directory
3. Keep `depth` low (0-1) for `kronos_search_source` unless you need broad coverage
4. Use `type_filter` to narrow search when you know you want modules vs experiments
5. For large files, use `offset` and `max_lines` to paginate — don't try to read everything at once
6. Source paths use forward slashes even on Windows

---

## Examples

### "Where is PAC conservation implemented?"

```
1. kronos_search(query="PAC conservation")
   → pac-comprehensive, pac-framework-unified

2. kronos_deep_dive(query="pac-comprehensive")
   → fracton/fracton/core/pac_regulation.py (module)
   → fracton/fracton/storage/pac_engine.py (module)
   → dawn-field-theory/foundational/experiments/milestone1 (experiment)

3. kronos_read_source(repo="fracton", path="fracton/core/pac_regulation.py")
   → [actual file content]
```

### "Find all references to golden ratio in SEC experiments"

```
1. kronos_search_source(query="sec-topological-dynamics", pattern="phi", depth=1, type_filter="experiment")
   → Matches in symbolic_entropy_collapse scripts, milestone3 experiments
```

### "What does the herniation detector do?"

```
1. kronos_deep_dive(query="herniation-hypothesis")
   → reality-engine/emergence/herniation_detector.py (module)

2. kronos_read_source(repo="reality-engine", path="emergence/herniation_detector.py")
   → [actual implementation code]
```
