# v0.0.8 — Session Knowledge Graph

**Date**: 2026-03-03
**Commit**: 43b2653
**Tag**: v0.0.8

## Summary

Session-level FDO accumulation across conversation turns with knowledge graph
UI for both per-conversation and overall memory visualization.

## Changes

### Backend (core/)
- **KnowledgeEntry dataclass** (`core/state.py`) — tracks FDO provenance: fetched_turn, fetched_by, query, hit_count, last_referenced_turn
- **`_merge_session_knowledge` reducer** — dedup by FDO ID, bump hit_count on re-encounter, cap at 50 entries
- **Memory node** — emits `session_knowledge` entries alongside `knowledge_context`, dedup-aware Kronos search
- **Compress node** — `session_knowledge` survives compression (separate state field), FDO refs enriched in compression prompt
- **Agent context** — `_merge_knowledge_sources()` merges per-turn + session knowledge, cap 10
- **Prompt builder** — accepts `session_knowledge` param, merges into knowledge section
- **All 3 companion nodes** updated to pass session_knowledge to prompt builder

### Server (server/)
- Evolve node LLM tokens filtered from WebSocket stream (no more memory content dump)
- Compact `memory_notification` WebSocket event replaces raw streaming
- `GET /api/session/knowledge` — list accumulated FDOs
- `GET /api/session/knowledge/graph` — force-graph data from session FDOs
- `GET /api/memory/graph` — force-graph data from memory.md wikilinks

### UI (ui/)
- `SessionKnowledgePanel` — collapsible mini force-graph in chat sidebar
- `MemoryKnowledgeGraph` — full graph on Memory page with section cluster filter
- `KnowledgeTurnSlider` — replay knowledge accumulation turn-by-turn
- `useSessionKnowledge` / `useMemoryGraph` hooks
- Memory page gets 3rd tab: "Knowledge Graph"
- Chat header gets graph toggle button
- Compact green "memory updated" pill for evolve node

### Eval
- 14 new tier 1 cases: `knowledge_context` category (KnowledgeEntry, reducer, merge, prompt)
- 8 new tier 2 cases: `knowledge_accumulation` category (multi-turn accumulation scenarios)
- New evaluator: `eval/engine/tier1/knowledge_context.py` (14 named check functions)

### Tests
- 53 new unit tests in `tests/test_session_knowledge.py`
- Total: 1916 tests passing, 210/210 tier 1 eval (100%)

## Files Changed
- 16 modified + 9 new = 25 files total (+2370 lines)
