## IronClaw Tool Execution Pipeline (v0.0.5.7)

### Bugs Fixed
- **handle_tool_execute placeholder**: Gateway returned `success: true` without executing tools — LLM thought files were written but staging stayed empty. Now delegates to real `ToolRegistry` with `FileReadTool`, `FileWriteTool`, `ShellTool`.
- **Empty tool registry**: `build_tool_registry()` logged tool names but never called `registry.register()`. Now registers tools from config permissions.
- **Stuck manifest**: Staging manifest status never updated past `"in_progress"`. Added `_update_manifest()` helper; dispatch sets `"agent_done"`, integrate sets `"completed"`/`"failed"`.

### Rust Changes (engine/)
- `engine/src/core/tools/file_read.rs` — FileReadTool with deny-first glob path policy, 1MB truncation
- `engine/src/core/tools/file_write.rs` — FileWriteTool with deny-first glob write policy, creates parent dirs
- `engine/src/core/tools/shell.rs` — ShellTool via SandboxBackend, timeout handling
- `engine/src/core/engine.rs` — `build_tool_registry()` wired, `tool_registry()` accessor
- `engine/src/gateway/mod.rs` — `build_gateway_tool_registry()`, real `handle_tool_execute`, dynamic `handle_list_tools`
- `engine/Cargo.toml` — Added `glob = "0.3"` dependency

### Python Changes
- `core/nodes/dispatch.py` — `_update_manifest()` helper, sets `"agent_done"` after agent completes
- `core/nodes/integrate.py` — Sets `"completed"`/`"failed"` based on audit verdict

### Tests
- 29 Rust unit tests (file_read, file_write, shell — path policy, execution, validation)
- 10 Python manifest update tests (test_staging_pipeline.py: 39 → 49)
- 16 E2E staging pipeline tests (new test_ironclaw_e2e.py)
- 10 new Tier 1 eval cases (routing + keyword routing for tool-execution intent)
- 6 new Tier 2 eval cases (tool write/verify, shell build, error handling, read-summarize, multi-file, denied write recovery)
- Tier 1: 196/196 (100%), Tier 2: 18 IronClaw cases, Total: 250 eval cases

### Documentation
- ADR: `adr-ironclaw-tool-pipeline` in kronos-vault/decisions/
- Updated FDOs: engine-ironclaw, grim-architecture, proj-grim (log entries + source_paths)
