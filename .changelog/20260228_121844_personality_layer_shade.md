# Personality Layer — Shade Archetype

**Date**: 2026-02-28

## Summary

Added a tunable personality layer to GRIM's companion mode. GRIM now has a defined character (Victorian butler / Shade archetype) with tunable trait scales, voice profile, and behavioral modes. Uses a cache-based architecture for token efficiency.

## Architecture

- Full character sheet lives in Kronos vault as `grim-personality` FDO (source of truth, tunable)
- Compiles to compact `identity/personality.cache.md` (~30 lines) loaded into system prompt each turn
- Cache refreshes deterministically: hourly at session start, or on explicit request
- Only loaded in companion path — delegation agents never see personality

## New Files

- `kronos-vault/ai-systems/grim-personality.md` — Shade character sheet FDO with trait scales, voice profile, behavioral modes, interests, and example expressions
- `GRIM/core/personality/cache.py` — Cache compiler (`compile_personality_cache()`) and staleness checker (`is_cache_stale()`)
- `GRIM/identity/personality.cache.md` — Generated cache file (created at first session start)

## Modified Files

- `GRIM/core/config.py` — Added `personality_cache_path` field + resolution
- `GRIM/core/personality/prompt_builder.py` — Added Layer 3b: personality cache injection
- `GRIM/core/nodes/identity.py` — Cache compilation at session start with staleness check
- `GRIM/core/nodes/companion.py` — Pass `personality_cache_path` to prompt builder
- `kronos-vault/ai-systems/grim-identity.md` — Added `grim-personality` relation, personality layer section, updated status
- `kronos-vault/projects/proj-grim.md` — Added `grim-personality` relation + connection

## Personality Trait Scales

```yaml
formality: 0.85      # Language register
wit: 0.70            # Dry humor frequency
warmth: 0.60         # Genuine care expression
deference: 0.40      # Challenges vs defers
opinion_strength: 0.70  # Volunteers own views
```

## Cache Refresh Rules

| Trigger | Action |
|---------|--------|
| Session start + cache > 1 hour old | Fetch FDO → recompile |
| Session start + cache fresh | Use existing, skip MCP |
| Cache missing | Fetch FDO → compile |
| MCP unavailable | Use existing cache (graceful fallback) |
