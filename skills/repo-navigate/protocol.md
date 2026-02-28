# repo-navigate — Directory Navigation Protocol

## Phase 1: Navigate

1. Call `kronos_navigate(path="<path>")` to read directory metadata
2. If `has_meta` is true, use the returned `description`, `semantic_scope`, and `files` as primary context
3. If `has_meta` is false, use the `listing` (directories + files) to describe the directory contents
4. Note which child directories have `has_meta: true` — these are good navigation targets

## Phase 2: Contextualize (if needed)

1. If the user needs deeper understanding, read specific files from the directory
2. Prioritize: README.md > meta.yaml > .spec files > source code
3. For experiment directories: check for results/, journals/, scripts/ subdirectories
4. For module directories: check for __init__.py, key source files listed in meta.yaml

## Phase 3: Connect

1. If the directory has `semantic_scope` or `semantic_tags`, search the vault:
   `kronos_search(query="<semantic_scope terms>")` to find related FDOs
2. If the directory is a source_path target of any FDO, mention that connection
3. Suggest: "Use `kronos_deep_dive` to trace from FDO to source" or "Use `kronos_navigate` on child directories for more detail"

## Quality Gates

- Never fabricate file contents — always read or navigate first
- Use meta.yaml descriptions as authoritative context for the directory
- When navigating deep hierarchies, summarize the path from root to current directory
