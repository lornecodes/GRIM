# Model Routing Protocol

> Multi-tier model selection for Claude-based AI systems.
> Portable across GRIM, IronClaw, and any system using the Anthropic API.

## Overview

Not every request needs the same model. Simple greetings and factual lookups
don't need Sonnet's full reasoning — Haiku is 5x cheaper and faster. Deep
architecture discussions and complex research benefit from Opus's extended
reasoning. This protocol selects the optimal tier per turn.

## Tiers

| Tier | Model | Cost | Use For |
|------|-------|------|---------|
| **haiku** | `claude-haiku-4-5-20251001` | $$ | Greetings, factual Q&A, tool dispatch, summarization, status checks |
| **sonnet** | `claude-sonnet-4-6` | $$$$ | Code generation, analysis, multi-step tasks, debugging — **default** |
| **opus** | `claude-opus-4-6` | $$$$$$ | Architecture design, deep research, complex reasoning, philosophical inquiry |

## Routing Pipeline

Four stages, evaluated in order. First match wins.

### Stage 1 — Explicit Overrides (zero latency)

User commands that force a specific tier:

- `/fast` or `/haiku` → haiku
- `/deep` or `/opus` → opus
- `/sonnet` → sonnet

Confidence: 1.0. Always respected.

### Stage 2 — Feature Scoring (zero latency)

Heuristic scoring based on message features. Each signal adds points
to a tier's score. The tier with the highest score wins if confidence
exceeds the threshold (default: 0.6).

**Message features:**

| Signal | Tier | Points |
|--------|------|--------|
| Short prompt (<80 chars, no code) | haiku | +3 |
| Long prompt (>500 chars) | sonnet | +1 |
| Very long prompt (>1500 chars) | opus | +1 |
| Greeting/factual keywords | haiku | +4 |
| Code intent keywords | sonnet | +4 |
| Deep analysis keywords | opus | +4 |
| Code blocks or >10 lines | sonnet | +2 |

**Haiku keywords:** hello, hi, hey, thanks, thank you, good morning, good night,
what is, who is, define, list, summarize, tldr, status, what time

**Sonnet keywords:** implement, write code, refactor, create file, add test,
fix bug, debug, code review, write a function, ```, def, class, import, function

**Opus keywords:** architecture, design system, deep analysis, compare approaches,
trade-off, complex reasoning, philosophical, emergent, recursive, first principles,
derive, proof, theorem, research question, long-term strategy

**System-specific signals (GRIM):**

| Signal | Tier | Points | Rationale |
|--------|------|--------|-----------|
| Active objectives exist | sonnet | +1 | Objective-aware responses need reasoning |
| Context was compressed | sonnet | +1 | Long session = complex conversation |
| Write-permission skill matched | sonnet | +2 | Actions need reliable model |
| >5 FDOs in knowledge context | sonnet | +1 | Rich context needs reasoning capacity |

**Confidence formula:**

```
confidence = (top_score - runner_up_score) / (top_score + 1)
```

If confidence < threshold (default 0.6), fall through to next stage.

### Stage 3 — LLM Classifier (optional, disabled by default)

For ambiguous cases where feature scoring isn't confident enough, use Haiku
itself to classify the message. Timeout: 1500ms.

Enable via config: `routing.classifier_enabled: true`

This adds ~200ms latency per routed message. Enable after calibrating
the feature scoring on production traffic.

### Stage 4 — Default Fallback

If no stage produced a confident decision, use the default tier (sonnet).
This ensures the system always works, even for novel message types.

## Configuration

```yaml
routing:
  enabled: true               # master switch
  default_tier: "sonnet"      # fallback tier
  classifier_enabled: false   # stage 3 LLM classifier
  confidence_threshold: 0.6   # minimum confidence for stage 2
```

## Integration Points

### GRIM

The router node (`core/nodes/router.py`) calls `route_model()` after
deciding companion vs delegate mode. The selected model is stored in
`state["selected_model"]` and consumed by:

- **Companion node** — creates ChatAnthropic with the selected model
- **Base agent** — accepts `model_override` parameter

### IronClaw

Import `core/model_router.py` directly — it's self-contained with no
GRIM-specific dependencies. The GRIM signals (objectives, compression,
skills, FDOs) are optional keyword arguments.

```python
from core.model_router import route_model

decision = await route_model(
    message,
    enabled=True,
    default_tier="sonnet",
)
# decision.model → "claude-sonnet-4-6"
```

## Evaluation

Track these metrics to calibrate:

- **Tier distribution**: Target ~40% haiku, ~50% sonnet, ~10% opus
- **Override rate**: How often users use /fast or /deep
- **Classifier agreement**: When enabled, how often stage 3 agrees with stage 2
- **Cost savings**: Compare against all-sonnet baseline

## Design Decisions

1. **Haiku replaces local models** — no local inference, Haiku fills the
   cheap/fast role with better quality than any local model
2. **Feature scoring is the primary path** — zero latency, deterministic,
   easy to debug and extend
3. **Per-turn routing** — a simple follow-up after a deep discussion uses
   haiku, not opus. Each turn is independent.
4. **Classifier disabled by default** — adds latency and cost. Enable after
   measuring feature scoring accuracy on real traffic.
5. **Default to sonnet** — when in doubt, use the balanced option. Better
   to slightly over-spend than to under-deliver.
