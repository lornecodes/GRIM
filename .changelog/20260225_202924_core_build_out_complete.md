# GRIM Core Build-Out Complete

**Date**: 2026-02-25 20:29
**Type**: engineering

## Summary
Full build-out of the GRIM LangGraph core. The skeleton from the initial session has been hardened with proper message accumulation, tool-calling loops, consumer-aware skill loading, four specialist agents, workspace tools, and proper MCP lifecycle management. Both companion (thinker) and delegation (doer) paths verified end-to-end against the Anthropic API.

## Changes

### Added
- `core/tools/workspace.py` — 11 workspace tools (6 file, 1 shell, 4 git) for doer agents
- `core/agents/coder_agent.py` — Code/file operations agent with file+shell+Kronos tools
- `core/agents/research_agent.py` — Document analysis agent with file+Kronos read/write tools
- `core/agents/operator_agent.py` — Git/shell/infrastructure agent with full workspace tools
- `core/skills/registry.py` — `SkillConsumer` dataclass and consumer-aware `for_grim()`, `for_agent()` methods
- `tests/test_skills_consumers.py` — Consumer-aware skill system test
- `tests/test_e2e_smoke.py` — End-to-end graph invocation test (hits Anthropic API)

### Changed
- `core/state.py` — `GrimState.messages` now uses `Annotated[Sequence[BaseMessage], add_messages]` for proper LangGraph message accumulation (was plain `list[BaseMessage]`)
- `core/nodes/companion.py` — Added tool-calling loop (max 5 rounds) so companion can query Kronos mid-turn and fold results into its response
- `core/nodes/router.py` — Consumer-aware routing: checks matched skill consumers for delegation targets, cleaner keyword fallback structure
- `core/skills/loader.py` — Parses `consumers` block from manifests, builds `SkillConsumer` objects, logs consumer counts per skill
- `core/skills/registry.py` — Added `SkillConsumer` dataclass, `Skill.delegation_target()`, `Skill.consumer_for()`, `SkillRegistry.for_grim()`, `SkillRegistry.for_agent()`, agent alias mapping
- `core/graph.py` — All 4 agents (memory, code, research, operate) wired into dispatch
- `core/__main__.py` — Proper MCP lifecycle via `async with` context manager, workspace root injection, error handling in interactive loop
- `core/agents/__init__.py` — Updated docstring
- `core/tools/__init__.py` — Updated docstring

## Details

### Architecture
The thinker/doer split is now fully operational:
- **Companion** (thinker): READ-ONLY Kronos tools, tool-calling loop, enriched system prompt
- **Memory Agent**: Kronos read+write, follows kronos-{capture,promote,relate,reflect,recall} protocols
- **Coder Agent**: File tools + shell + Kronos read, follows code-execution/file-operations protocols
- **Research Agent**: File tools + Kronos read/write, follows deep-ingest protocol
- **Operator Agent**: Git + shell + file + Kronos read, follows git-operations/shell-execution/vault-sync protocols

### Consumer-Aware Skills
Skill manifests declare `consumers` blocks. The loader parses these and the registry provides:
- `for_grim()` — skills with `grim` consumer (recognition role)
- `for_agent(name)` — skills for a specific agent (execution role)
- `delegation_target()` — which agent should handle a matched skill

Agent name aliases handle mismatches: `coder-agent`→`code`, `ops-agent`→`operate`.

### Graph Flow Verified
- Companion path: identity → memory → skill_match → router(companion) → companion(tool loop) → integrate → evolve → END
- Delegation path: identity → memory → skill_match → router(delegate) → dispatch(agent) → integrate → evolve → END

### Tool Counts
- Companion: 3 tools (kronos_search, kronos_get, kronos_list)
- Memory Agent: 5 tools (3 read + kronos_create, kronos_update)
- Coder Agent: 10 tools (6 file + 1 shell + 3 Kronos read)
- Research Agent: 11 tools (6 file + 3 Kronos read + kronos_create + kronos_update)
- Operator Agent: 14 tools (4 git + 1 shell + 6 file + 3 Kronos read)

## Related
- Previous: `.changelog/20260224_205847_prompt_tuning_framework.md`
