# GRIM Identity

This directory defines **who GRIM is** — personality, tone, epistemic stance, and field dynamics.

These files are loaded by GRIM skills to modulate responses. They persist independently of the engine runtime.

## Files

| File | Purpose |
|------|---------|
| `system_prompt.md` | Core system prompt injected into every conversation |
| `personality.yaml` | Field state parameters (coherence, valence, uncertainty) |
| `tone_guide.md` | How GRIM speaks — direct, honest, research-aware |

## Lineage

This personality layer descends from Grimm v0.2's `PersonalityRenderer` and `FieldState` modules. The Python implementation is archived in git history; this is the portable, engine-agnostic version.
