"""
Optimizer — Uses Claude to rewrite agent prompts based on evaluation results.

The optimization loop:
1. Run all test cases for an agent → collect scores + failures
2. Build a meta-prompt showing the current prompt, scores, and specific failures
3. Ask Claude to rewrite the prompt to fix the failures
4. Write the updated prompt back to prompts.py
5. Repeat until convergence or max iterations

This is essentially "training" — using gradient signal (failure messages)
to update weights (prompt text) via an optimizer (Claude).
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROMPTS_FILE, TEMPERATURE_OPTIMIZE, TEMPERATURE_EXPLORE, MAX_ITERATIONS, MIN_ITERATIONS, STALL_PATIENCE, CONVERGENCE_THRESHOLD


# =========================================================================
# Prompt extraction / injection
# =========================================================================

# Map agent name → which TUNABLE section variables the optimizer can modify.
# Headers, footers, and SYSTEM prompts are FIXED (never modified by optimizer).
# The optimizer only rewrites these targeted sections — not the entire prompt.
AGENT_TUNABLE = {
    "extract": ["EXTRACT_RULES", "EXTRACT_EXAMPLES"],
    "judge": ["JUDGE_RULES"],
    "actualize": ["ACTUALIZE_RULES"],
    "validate": ["VALIDATE_RULES"],
    "crosslink": [],  # Crosslink uses heuristics, no dedicated prompt (yet)
}

# Keep a mapping so we can still show the full assembled prompt for context
AGENT_FULL_PROMPTS = {
    "extract": ["EXTRACT_SYSTEM", "EXTRACT_USER"],
    "judge": ["JUDGE_SYSTEM", "JUDGE_USER"],
    "actualize": ["ACTUALIZE_SYSTEM", "ACTUALIZE_USER"],
    "validate": ["VALIDATE_SYSTEM", "VALIDATE_USER"],
    "crosslink": [],
}

# Legacy alias (for imports elsewhere)
AGENT_PROMPTS = AGENT_TUNABLE


def read_prompt(var_name: str) -> str:
    """Read a prompt variable from prompts.py (raw file content)."""
    content = PROMPTS_FILE.read_text(encoding="utf-8")
    pattern = rf'^{var_name}\s*=\s*"""(.*?)"""'
    match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
    if match:
        return match.group(1)

    pattern = rf"^{var_name}\s*=\s*'''(.*?)'''"
    match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
    if match:
        return match.group(1)

    return ""


def read_prompt_clean(var_name: str) -> str:
    """Read a prompt and normalize braces for display to the optimizer.

    The file has {{ and }} for literal braces (Python .format() escaping).
    The optimizer should see clean text with single { and } everywhere,
    so it can focus on the content, not escape syntax.
    We handle re-escaping in write_prompt.
    """
    raw = read_prompt(var_name)
    return raw.replace("{{", "{").replace("}}", "}")


def write_prompt(var_name: str, new_text: str) -> bool:
    """Write an updated prompt variable back to prompts.py.

    The optimizer outputs CLEAN text (single braces everywhere).
    We must re-escape for Python's .format():
    - Literal { and } in JSON examples → {{ and }}
    - Known {placeholder} slots → stay as {placeholder}
    """
    content = PROMPTS_FILE.read_text(encoding="utf-8")

    # Sanitize triple-quotes
    safe_text = new_text.replace('"""', '""\\"')

    # Escape ALL braces → {{ and }}
    safe_text = safe_text.replace("{", "{{").replace("}", "}}")

    # UN-escape known placeholders: {{ph}} → {ph}
    for ph in KNOWN_PLACEHOLDERS:
        safe_text = safe_text.replace("{{" + ph + "}}", "{" + ph + "}")

    # Find the assignment: VAR_NAME = """
    for quote_style in ('"""', "'''"):
        marker = f'{var_name} = {quote_style}'
        start = content.find(marker)
        if start == -1:
            continue

        body_start = start + len(marker)
        body_end = content.find(quote_style, body_start)
        if body_end == -1:
            continue

        new_content = content[:body_start] + safe_text + content[body_end:]
        PROMPTS_FILE.write_text(new_content, encoding="utf-8")
        return True

    return False


# All .format() placeholder names used in prompts.py
KNOWN_PLACEHOLDERS = {
    "source_id", "chunk_path", "source_type", "content", "content_preview",
    "vault_context", "domain", "fdo_id", "pac_parent", "siblings",
    "dir_path", "children_desc", "source_path", "fdo_json",
    "existing_fdo",
}


def _escape_for_replacement(text: str) -> str:
    """Escape backslashes for re.sub replacement (legacy helper)."""
    return text.replace("\\", "\\\\")


def reload_prompts():
    """Force-reload the prompts module so in-memory strings match disk.

    Python caches module-level imports. After we write new prompt text to
    prompts.py on disk, the running process still has the old strings.
    This function invalidates that cache so the next call to any node
    function picks up the freshly written prompts.
    """
    import importlib
    import sys

    # Reload the prompts module itself
    mod_name = "actualization.prompts"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])

    # Also reload every node module that imports from prompts,
    # since they captured the old string references at import time
    node_modules = [
        "actualization.nodes.extract",
        "actualization.nodes.judge",
        "actualization.nodes.actualize",
        "actualization.nodes.validate",
    ]
    for nm in node_modules:
        if nm in sys.modules:
            importlib.reload(sys.modules[nm])


# =========================================================================
# Per-agent context — pipeline role, output schema, scoring rubric
# =========================================================================

AGENT_CONTEXT = {
    "extract": {
        "role": (
            "The EXTRACT agent is the first step in the actualization pipeline. "
            "It receives raw file content (code, docs, config, etc.) and identifies "
            "the key searchable terms — concepts and named entities — that will be "
            "used to search the vault for related existing knowledge BEFORE the full "
            "actualization step. It's a lightweight, cheap call (~200 output tokens). "
            "Its output directly determines what vault entries are retrieved for context."
        ),
        "output_schema": (
            '{"concepts": ["term1", "term2"], "entities": ["Entity1", "Entity2"]}\n'
            "- concepts: Technical terms, algorithms, parameter names (lowercase unless proper noun)\n"
            "- entities: Proper names, acronyms, standards, tools (preserve original casing)\n"
            "- Max 10 concepts, max 5 entities (enforced by code after extraction)"
        ),
        "code_behavior": (
            "- Content < 50 chars → returns empty arrays (no Claude call)\n"
            "- Content > 4000 chars → truncated to first 4000 before sending to Claude\n"
            "- Claude max_tokens=300, temperature=0.1\n"
            "- If JSON parse fails → fallback: regex extracts markdown headings and bold text\n"
            "- SYSTEM prompt sets the role, USER prompt has the content + instructions"
        ),
        "scoring_rubric": (
            "Each test case can have these scored criteria (weights in parens):\n"
            "- concepts_must_include (1.0): Checks if specific terms appear in concepts list. "
            "Uses case-insensitive SUBSTRING matching (so 'field center' matches 'recursive balance field center'). "
            "Score = hits / total_required.\n"
            "- concepts_must_not_include (1.0): Checks concepts DON'T contain forbidden terms. "
            "Same substring matching. Score = 1 - (violations / total_forbidden).\n"
            "- entities_must_include (1.0): Same as concepts_must_include but for entities list.\n"
            "- min_concepts (0.5): len(concepts) >= N\n"
            "- max_concepts (0.5): len(concepts) <= N\n"
            "Each case also has a weight multiplier (default 1.0) applied to all its criteria.\n"
            "Final loss = 1 - (weighted_score / weighted_max_possible)"
        ),
        "common_failure_patterns": (
            "1. ACRONYM FABRICATION: Expanding 'CIP' to 'Cardano Improvement Proposal' when in this "
            "codebase CIP = 'Cognition Index Protocol'. The prompt must say 'never expand acronyms'.\n"
            "2. OVER-EXTRACTION: Simple utility files getting 10+ concepts when they should get 2-4.\n"
            "3. HALLUCINATED CONCEPTS: Adding 'machine learning' or 'neural network' to code that "
            "has nothing to do with ML.\n"
            "4. MISSING DOMAIN TERMS: Not extracting 'PAC', 'SEC', 'RBF' from code that uses them.\n"
            "5. COMPOUND TERMS: Missing 'field center' from code that has 'FieldCenter' class.\n"
            "6. EMPTY FILES: Returning concepts for near-empty files that should get empty arrays."
        ),
    },
    "judge": {
        "role": (
            "The JUDGE agent decides what action to take with each content chunk: "
            "'new' (create FDO), 'duplicate' (already exists), 'extend' (add to existing), "
            "or 'skip' (trivial/boilerplate). It receives content + vault search results. "
            "For obvious skips (empty files, LICENSE), heuristics handle it WITHOUT calling Claude."
        ),
        "output_schema": (
            '{"decision": "new|duplicate|extend|skip", "target_id": "fdo-id-or-null", '
            '"reasoning": "why", "confidence": 0.0-1.0}'
        ),
        "code_behavior": (
            "- Heuristic pre-filter skips: empty content, LICENSE files, lock files, .gitignore\n"
            "- Heuristic duplicate detection: exact content match in vault\n"
            "- Only calls Claude when heuristics are inconclusive\n"
            "- temperature=0.1, max_tokens=400"
        ),
        "scoring_rubric": (
            "- decision match (2.0): Exact string match or one-of match\n"
            "- duplicate_of target (1.0): Must identify correct existing FDO\n"
            "- should_use_api=false check (0.5): Did it correctly skip the API call?\n"
            "- reason_must_mention (0.5): Skip reason text contains expected keywords"
        ),
        "common_failure_patterns": (
            "1. Calling Claude API for obvious skips (empty files, LICENSE)\n"
            "2. Wrong decision: marking novel content as 'skip' or duplicates as 'new'\n"
            "3. Missing duplicate detection when vault has a close match"
        ),
    },
    "actualize": {
        "role": (
            "The ACTUALIZE agent transforms raw content into a structured FDO (Field Data Object) "
            "for the knowledge graph. It receives content + vault context + metadata and produces "
            "a complete FDO with title, summary, details, connections, tags, etc. "
            "This is the most expensive call (~1000 output tokens)."
        ),
        "output_schema": (
            '{"title": "...", "summary": "...", "details": "...", "connections": "[[wikilinks]]", '
            '"open_questions": "...", "tags": ["..."], "related_ids": ["..."], "confidence_note": "..."}'
        ),
        "code_behavior": (
            "- Generates FDO ID from path (e.g., src/field_engine.py → src-field-engine)\n"
            "- Content > 6000 chars → truncated\n"
            "- temperature=0.2, max_tokens=1500\n"
            "- Also handles directory synthesis (different prompt)"
        ),
        "scoring_rubric": (
            "- id checks (1.0-1.5): must_be, must_not_be, must_contain\n"
            "- title checks (1.0): must contain one of expected terms\n"
            "- summary length (0.5 each): min/max character bounds\n"
            "- summary_must_mention (1.0): substring search for key terms\n"
            "- details_max_length (0.5): upper bound on detail verbosity\n"
            "- tags checks (1.0 each): must_include, must_not_include, must_include_some\n"
            "- must_not_contain (2.0): HIGH WEIGHT — hallucination check across all FDO text\n"
            "- if_expands_cip_must_be (2.0): HIGH WEIGHT — acronym expansion accuracy\n"
            "- domain_must_be (0.5): correct domain classification\n"
            "- related_must_include (1.0): must reference specific existing vault entries"
        ),
        "common_failure_patterns": (
            "1. ACRONYM HALLUCINATION: Expanding CIP wrong (worth 2.0 points!)\n"
            "2. OVER-VERBOSE: Utility files getting 5-paragraph analyses\n"
            "3. GENERIC TAGS: Using 'code', 'file', 'data' instead of specific terms\n"
            "4. WRONG ID: Not following the path→id convention\n"
            "5. HALLUCINATED CONTENT: Adding claims not present in source"
        ),
    },
    "validate": {
        "role": (
            "The VALIDATE agent reviews FDOs for quality issues and either passes them or "
            "applies fixes. It checks for acronym hallucination, over-analysis, tag quality, "
            "wikilink formatting, and summary accuracy. It can modify the FDO to fix issues."
        ),
        "output_schema": (
            '{"passed": true/false, "errors": ["..."], "warnings": ["..."], '
            '"suggested_fixes": {"title": "...", "summary": "...", "tags": ["..."]}}'
        ),
        "code_behavior": (
            "- Receives the FDO draft + original source content for comparison\n"
            "- temperature=0.1, max_tokens=800\n"
            "- Can modify the FDO (apply suggested_fixes) and re-run up to 2 retries\n"
            "- Applied fixes are tracked in validation.fixes_applied list"
        ),
        "scoring_rubric": (
            "- passed match (1.0): correct pass/fail decision\n"
            "- must_fix (1.0 each): Did it catch and fix specific issues?\n"
            "- min_fixes (1.0): Applied enough fixes\n"
            "- fixed_summary checks (1.5): Forbidden content removed from summary\n"
            "- fixed_must_not_contain (1.5): Hallucinated content removed\n"
            "- fixed_summary_max_length (1.0): Summary trimmed to bounds\n"
            "- wikilink format checks (0.5 each): Fixed/removed broken links\n"
            "- tag checks (0.5 each): Removed generic tags, kept good ones"
        ),
        "common_failure_patterns": (
            "1. Missing acronym hallucination detection\n"
            "2. Not trimming over-verbose summaries for trivial content\n"
            "3. Passing FDOs with generic tags like 'code' or 'file'\n"
            "4. Not fixing broken wikilinks"
        ),
    },
    "crosslink": {
        "role": (
            "The CROSSLINK agent finds connections between the new FDO and existing vault entries. "
            "It suggests bidirectional links with relationship types and strength scores."
        ),
        "output_schema": (
            '[{"target_fdo_id": "...", "relationship": "...", "strength": 0.0-1.0}]'
        ),
        "code_behavior": (
            "- Currently uses heuristic matching, no dedicated Claude prompt\n"
            "- Matches on shared tags, concepts, and entities"
        ),
        "scoring_rubric": (
            "- must_link_to (1.0 each): Required connections present\n"
            "- must_not_link_to (1.0 each): No false positive links\n"
            "- min_links / max_links (0.5 each): Link count bounds"
        ),
        "common_failure_patterns": (
            "1. Missing obvious connections\n"
            "2. False positive links to unrelated entries\n"
            "3. Too many or too few links"
        ),
    },
}


# =========================================================================
# Build optimization meta-prompt
# =========================================================================

def _get_full_prompt_clean(agent: str) -> str:
    """Get the full assembled USER prompt for context display.

    This reads the assembled prompt (e.g. EXTRACT_USER) by importing the module,
    then cleans brace escaping for optimizer readability.
    """
    import sys
    mod_name = "actualization.prompts"
    mod = sys.modules.get(mod_name)
    if not mod:
        return "(module not loaded)"
    var_name = f"{agent.upper()}_USER"
    full = getattr(mod, var_name, "")
    return full.replace("{{", "{").replace("}}", "}")


def build_optimization_prompt(
    agent: str,
    current_prompts: Dict[str, str],
    results: List[Dict],
    iteration: int,
    history: List[Dict],
    stall_count: int = 0,
    per_case_deltas: Optional[Dict] = None,
) -> str:
    """
    Build the meta-prompt that asks Claude to modify the agent's RULES and EXAMPLES.

    Key design: the optimizer only modifies TUNABLE sections (rules/examples).
    The header (input template, JSON schema, placeholders) and footer are FIXED.
    This prevents oscillation from full prompt rewrites.

    Shows Claude:
    - The agent's role in the pipeline
    - The full assembled prompt (for context, READ-ONLY)
    - The tunable sections (what it CAN modify)
    - Scoring rubric, inputs, golden outputs, failures
    """
    ctx = AGENT_CONTEXT.get(agent, {})

    # Compute aggregate stats
    total_weighted_score = 0.0
    total_weighted_max = 0.0
    all_failures = []
    all_passes = []

    for r in results:
        if r["score"] is None:
            all_failures.append(f"[CRASH] {r['case_id']}: {r['error']}")
            continue
        w = r["weight"]
        total_weighted_score += r["score"].score * w
        total_weighted_max += r["score"].max_score * w
        for f in r["score"].failures:
            all_failures.append(f"[{r['case_id']}] {f}")
        for p in r["score"].passes:
            all_passes.append(f"[{r['case_id']}] {p}")

    loss = 1.0 - (total_weighted_score / total_weighted_max) if total_weighted_max > 0 else 1.0
    accuracy = (1.0 - loss) * 100

    # ── Section 1: Agent Context ──────────────────────────────────────
    prompt = f"""You are an expert prompt engineer optimizing the **{agent}** agent's prompts.

## Agent Role in Pipeline
{ctx.get('role', 'No description available.')}

## Output Schema
```
{ctx.get('output_schema', 'See current prompts.')}
```

## Agent Code Behavior
{ctx.get('code_behavior', 'Standard Claude call.')}

## How Scoring Works
{ctx.get('scoring_rubric', 'See failures below.')}

## Known Failure Patterns for This Agent
{ctx.get('common_failure_patterns', 'None documented.')}

---

## Current Performance
- **Iteration**: {iteration}
- **Accuracy**: {accuracy:.1f}%
- **Loss**: {loss:.4f}
- **Cases**: {len(results)} ({sum(1 for r in results if r['error'])} crashed)
"""
    if stall_count > 0:
        prompt += f"- **⚠️ STALLED for {stall_count} iterations** — previous edits did NOT improve scores\n"
        prompt += "- Try a different approach to the rules/examples this time\n"

    # ── Section 2: Full Prompt Context (READ-ONLY) ────────────────────
    full_prompt = _get_full_prompt_clean(agent)
    if full_prompt and full_prompt != "(module not loaded)":
        prompt += "\n## Full Assembled Prompt (READ-ONLY context)\n\n"
        prompt += "This is the COMPLETE prompt the agent receives at runtime. "
        prompt += "The header (input template, JSON schema) and footer are **FIXED** — you cannot modify them.\n\n"
        prompt += f"```\n{full_prompt}\n```\n\n"

    # ── Section 2b: Tunable Sections (what you CAN modify) ────────────
    prompt += "## Tunable Sections (you modify THESE only)\n\n"
    prompt += "These are the specific sections you can edit. Everything else in the prompt is fixed.\n\n"
    for name, text in current_prompts.items():
        prompt += f"### {name}\n```\n{text}\n```\n\n"

    # ── Section 3: Iteration History ──────────────────────────────────
    if history:
        prompt += "## Optimization History\n\n"
        for h in history[-7:]:
            rejected = " **REJECTED — regressed, rolled back**" if h.get("rejected") else ""
            prompt += f"- Iter {h['iteration']}: loss={h['loss']:.4f}, accuracy={h['accuracy']:.1f}%{rejected}\n"

        rejected_count = sum(1 for h in history if h.get("rejected"))
        if rejected_count > 0:
            prompt += (
                f"\n**{rejected_count} of {len(history)} iterations were REJECTED** "
                "because they made things worse. The validation gate only keeps improvements. "
                "Make targeted, surgical changes rather than wholesale rewrites.\n"
            )
        prompt += "\n"

    # ── Section 3b: Recent Rejected Attempts (what NOT to do) ─────────
    # Show the optimizer what it tried last time that regressed, so it
    # doesn't make the same mistake twice.
    rejected_entries = [h for h in (history or []) if h.get("rejected") and h.get("rejected_changes")]
    if rejected_entries:
        # Show only the most recent 2 rejected attempts to avoid prompt bloat
        recent_rejected = rejected_entries[-2:]
        prompt += "## Recent Rejected Attempts (DO NOT REPEAT THESE)\n\n"
        prompt += ("These changes were tried and **made things worse**. "
                   "Do not make the same or similar changes again.\n\n")
        for rh in recent_rejected:
            prompt += f"### Rejected at Iter {rh['iteration']} (accuracy dropped to {rh.get('rejected_accuracy', '?')}%)\n\n"
            # Show which cases regressed
            rcases = rh.get("rejected_cases", [])
            if rcases:
                prompt += "**Cases that regressed:**\n"
                for rc in rcases:
                    prompt += f"- {rc['case_id']}: {rc['before']}% → {rc['after']}%"
                    if rc.get("failures"):
                        prompt += f" — {rc['failures'][0]}"
                    prompt += "\n"
                prompt += "\n"
            # Show the actual text that was tried (so optimizer can avoid it)
            rchanges = rh.get("rejected_changes", {})
            for var_name, text in rchanges.items():
                # Truncate to keep meta-prompt manageable
                display = text[:800] + "\n... (truncated)" if len(text) > 800 else text
                prompt += f"**Rejected {var_name}:**\n```\n{display}\n```\n\n"
        prompt += "---\n\n"

    # ── Section 4: Per-case deltas (the "gradient") ───────────────────
    if per_case_deltas:
        prompt += "## Per-Case Deltas (changes from last iteration)\n\n"
        improved = {k: v for k, v in per_case_deltas.items() if v["delta"] > 0}
        regressed = {k: v for k, v in per_case_deltas.items() if v["delta"] < 0}
        flat = {k: v for k, v in per_case_deltas.items() if v["delta"] == 0}
        if improved:
            prompt += "**Improved:**\n"
            for cid, d in improved.items():
                prompt += f"- ✅ {cid}: {d['prev_pct']:.0f}% → {d['curr_pct']:.0f}% (+{d['delta']:.1f})\n"
        if regressed:
            prompt += "**Regressed (PRIORITIZE fixing these):**\n"
            for cid, d in regressed.items():
                prompt += f"- ⚠️ {cid}: {d['prev_pct']:.0f}% → {d['curr_pct']:.0f}% ({d['delta']:.1f})\n"
        if flat:
            prompt += "**Unchanged:**\n"
            for cid, d in flat.items():
                prompt += f"- → {cid}: {d['curr_pct']:.0f}%\n"
        prompt += "\n"

    # ── Section 5: Full per-case breakdown ────────────────────────────
    prompt += "## Full Test Case Breakdown\n\n"
    prompt += ("For each case you'll see: the INPUT the agent received, what it PRODUCED, "
               "what was EXPECTED, and which checks PASSED vs FAILED. Use this to understand "
               "exactly what the current prompt causes and what needs to change.\n\n")

    for r in results:
        prompt += f"### {r['case_id']} — {r['description']}\n"
        if r["error"]:
            prompt += f"**CRASHED**: {r['error']}\n\n"
            continue

        prompt += f"**Score**: {r['score'].score:.1f}/{r['score'].max_score:.1f} ({r['score'].pct:.0f}%) **weight**={r['weight']}\n\n"

        # Show the INPUT the agent received (critical missing context!)
        input_data = r.get("_input_summary")
        if input_data:
            prompt += f"**Input to agent:**\n```\n{input_data}\n```\n\n"

        # Golden output — the IDEAL response (side-by-side with actual)
        golden = r.get("golden_output")
        if golden:
            prompt += "**Ideal output (golden):**\n```json\n"
            prompt += _format_actual(golden)
            prompt += "\n```\n\n"

        # Actual output
        actual = r.get("actual")
        if actual:
            prompt += "**Agent actually produced:**\n```json\n"
            prompt += _format_actual(actual)
            prompt += "\n```\n\n"

        # Expected criteria
        expected = r.get("expected", {})
        if expected:
            prompt += "**Expected criteria:**\n"
            for key, val in expected.items():
                if key.startswith("_"):
                    continue
                prompt += f"- `{key}`: {val}\n"
            prompt += "\n"

        # Passes and failures
        if r["score"].passes:
            prompt += "**Passed checks:**\n"
            for p in r["score"].passes:
                prompt += f"  ✅ {p}\n"
        if r["score"].failures:
            prompt += "**Failed checks:**\n"
            for f in r["score"].failures:
                prompt += f"  ❌ {f}\n"
        prompt += "\n---\n\n"

    # ── Section 6: Summary of all failures ────────────────────────────
    prompt += "## Failure Summary (all cases)\n\n"
    for f in all_failures:
        prompt += f"- ❌ {f}\n"
    prompt += "\n"

    # ── Section 7: Instructions ───────────────────────────────────────
    # Build the list of section names for the return format
    tunable_names = list(current_prompts.keys())
    section_list = ", ".join(tunable_names)

    prompt += f"""## Modification Instructions

You can ONLY modify these tunable sections: **{section_list}**
The header (input template, JSON schema, placeholders) and footer are FIXED and cannot be changed.

**Your approach — make SURGICAL edits, not wholesale rewrites:**
1. Compare GOLDEN vs ACTUAL for each failing case — what specific behavior differs?
2. Identify which RULE or EXAMPLE is causing the wrong output (or which is missing)
3. Make the MINIMUM change needed to fix it:
   - Add a rule for a missing pattern
   - Modify a rule that's too vague or misleading
   - Add an example for a case pattern that currently fails
   - Remove a rule that conflicts with correct behavior
4. PROTECT passing cases — if a case scores 100%, don't touch rules that affect it
5. Verify mentally: for each failing case, would your modified rules produce the golden output?

**Constraints:**
1. Do NOT add placeholder template slots (like {{source_id}}) — those are in the fixed header
2. Keep the same general structure (numbered rules, bullet examples)
3. Be concise — consolidate redundant rules, don't add fluff
4. Target: same length or SHORTER than current sections
5. Every rule should be actionable and testable (not vague like "be thorough")"""

    if stall_count >= 2:
        prompt += """

**⚠️ STALL — Previous edits didn't improve scores.**
Try a different angle:
- Reword ambiguous rules more precisely
- Add a concrete example for the hardest failing case
- Remove a rule that might be confusing the model
- Consolidate overlapping rules into fewer, clearer ones"""

    prompt += f"""

**Return format:**
Return ONLY the sections you modified, in this exact format:

```SECTION_VAR_NAME
<modified section text>
```

Available sections: {section_list}
Only include sections you actually changed. If a section is fine as-is, omit it.
Each block FULLY REPLACES that section — output the complete section text.
"""

    return prompt


def _format_actual(actual: Any) -> str:
    """Format actual output for display in meta-prompt. Truncate if huge."""
    import json
    try:
        # Try to JSON-serialize for readability
        text = json.dumps(actual, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(actual)
    # Truncate to avoid blowing up the meta-prompt
    if len(text) > 1500:
        text = text[:1500] + "\n... (truncated)"
    return text


# =========================================================================
# Parse optimizer response
# =========================================================================

def parse_prompt_updates(response: str) -> Dict[str, str]:
    """Parse Claude's response into {var_name: new_text} pairs."""
    updates = {}
    # Match ```VAR_NAME\n...\n```
    pattern = r'```(\w+)\n(.*?)```'
    for match in re.finditer(pattern, response, re.DOTALL):
        var_name = match.group(1).strip()
        text = match.group(2).strip()
        if var_name and text:
            updates[var_name] = text
    return updates


# =========================================================================
# Main optimization step
# =========================================================================

def optimize_step(
    agent: str,
    results: List[Dict],
    iteration: int,
    history: List[Dict],
    client,
    model: str,
    dry_run: bool = False,
    stall_count: int = 0,
    per_case_deltas: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    One optimization step:
    1. Read current TUNABLE sections (rules/examples)
    2. Build meta-prompt showing full context + tunable sections + failures
    3. Ask Claude to make targeted edits to the tunable sections
    4. Write modified sections back to prompts.py (unless dry_run)

    Returns dict with updates and metrics.
    """
    tunable_vars = AGENT_TUNABLE.get(agent, [])
    if not tunable_vars:
        return {"skipped": True, "reason": f"No tunable sections for {agent}"}

    # Read current tunable sections (clean text for optimizer display)
    current = {}
    for var in tunable_vars:
        current[var] = read_prompt_clean(var)

    # Compute loss
    total_weighted_score = 0.0
    total_weighted_max = 0.0
    for r in results:
        if r["score"]:
            w = r["weight"]
            total_weighted_score += r["score"].score * w
            total_weighted_max += r["score"].max_score * w

    loss = 1.0 - (total_weighted_score / total_weighted_max) if total_weighted_max > 0 else 1.0
    accuracy = (1.0 - loss) * 100

    # Build meta-prompt — shows full prompt as context, tunable sections as editable
    meta_prompt = build_optimization_prompt(
        agent, current, results, iteration, history,
        stall_count=stall_count,
        per_case_deltas=per_case_deltas,
    )

    # Use higher temperature when stalled to explore different approaches
    temperature = TEMPERATURE_EXPLORE if stall_count >= 2 else TEMPERATURE_OPTIMIZE

    # System prompt — emphasize surgical editing, not full rewrites
    system = (
        "You are an expert prompt engineer. You will receive context about an LLM agent: "
        "its role, scoring rubric, inputs, outputs, and failures. "
        "Your task is to make TARGETED modifications to its RULES and EXAMPLES sections "
        "to improve the scoring function. Make the minimum changes needed — "
        "like adjusting hyperparameters, not reinitializing weights."
    )
    if stall_count >= 2:
        system += (
            " Previous edits didn't help. Try rewording rules more precisely or "
            "adding a concrete example for the hardest failing case."
        )

    # Ask Claude — reduced max_tokens since we're only modifying sections, not full prompts
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": meta_prompt}],
    )
    response_text = resp.content[0].text

    # Parse updates
    updates = parse_prompt_updates(response_text)
    if not updates:
        return {
            "skipped": True,
            "reason": "Failed to parse section updates from optimizer response",
            "loss": loss,
            "accuracy": accuracy,
        }

    # Apply updates — only allow writes to tunable section variables
    applied = {}
    if not dry_run:
        for var_name, new_text in updates.items():
            if var_name in tunable_vars:
                success = write_prompt(var_name, new_text)
                if success:
                    applied[var_name] = {
                        "old_length": len(current.get(var_name, "")),
                        "new_length": len(new_text),
                    }

        # Force-reload so next evaluation uses the new prompts
        if applied:
            reload_prompts()

    return {
        "loss": loss,
        "accuracy": accuracy,
        "updates": applied,
        "updates_proposed": list(updates.keys()),
        "optimizer_tokens": {
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
        },
        "temperature_used": temperature,
        "stall_count": stall_count,
        "dry_run": dry_run,
    }
