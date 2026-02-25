"""
All Claude prompts in one place.

Centralized so:
- Easy to tune without touching graph logic
- Can version/A-B test prompts
- Consistent tone and instructions across nodes

Structure: Each USER prompt is assembled from fixed + tunable parts:

  _HEADER  — Fixed input template with {placeholders} + JSON schema (never tuned)
  RULES    — Tunable rules/criteria (optimizer modifies this)
  EXAMPLES — Tunable examples (optimizer modifies this, extract only)
  _FOOTER  — Fixed closing instructions (never tuned)
  USER     — Assembled from parts (used by node imports)

The optimizer only reads/writes RULES and EXAMPLES sections.
Headers, footers, and SYSTEM prompts are fixed.
"""


# =========================================================================
# Extract Node — Lightweight concept extraction (~200 output tokens)
# =========================================================================

EXTRACT_SYSTEM = """You are a knowledge extraction specialist. Extract the most important searchable terms from content - the specific concepts and named entities that someone would use to find this information later. Focus on exact terms from the source, not generic descriptions."""

# Fixed header: input template + JSON schema + what-to-extract description
_EXTRACT_HEADER = """Extract key searchable terms from this content for knowledge base indexing.

SOURCE: {source_id}
PATH: {chunk_path}
TYPE: {source_type}

<content>
{content}
</content>

Return ONLY a JSON object:
{{
    "concepts": ["specific term 1", "specific term 2"],
    "entities": ["Named Entity 1", "Named Entity 2"]
}}

**What to extract:**
- **Concepts**: Technical terms, algorithms, parameter names, domain acronyms used as concepts (e.g. PAC, SEC)
- **Entities**: Proper names, tools, standards, all acronyms/initialisms mentioned in text
- **Note**: Domain acronyms (PAC, SEC, RBF, MED, CIP, FDO, etc.) should appear in BOTH lists

"""

# Tunable: extraction rules (optimizer modifies this)
EXTRACT_RULES = """**Extraction rules (in priority order):**

1. **ACRONYMS → BOTH lists**: Every acronym/initialism in the text goes in concepts AND entities. PAC, SEC, RBF, MED, CIP, FDO, QPL — all of them appear in both lists, no exceptions. This is the #1 rule.
2. **ALL acronyms as entities**: Scan the entire text and list every acronym/initialism as an entity. Don't miss ones mentioned only in passing (e.g. "Uses the FDO schema" → FDO is an entity).
3. **Class/variable names → human-readable concept**: `FieldCenter` → "field center", `RecursiveBalanceField` → "recursive balance field"
4. **Math constants**: `PHI = (1 + math.sqrt(5)) / 2` → concept: "golden ratio"
5. **Function semantics**: `sha256()` → "hash", `slugify()` → "slugify"
6. **File-type keywords**: config → "configuration", utility/helpers → "utility"
7. **Never expand acronyms** — "CIP" stays "CIP", never "Cardano Improvement Proposal"
8. **Never fabricate** — only extract terms actually present in the text

**Volume:** Config/util: 2-6 concepts. Tests: 2-6 concepts (focus on what's being tested, not every helper). Docs: 3-8. Complex code: 4-10. Empty files: []."""

# Tunable: worked examples (optimizer modifies this)
EXTRACT_EXAMPLES = """
**Examples (study these carefully):**

Input: Code with `PHI = (1 + math.sqrt(5)) / 2  # Golden ratio`, `class FieldCenter`, `class RecursiveBalanceField`, docstring mentions PAC, SEC, RBF, entropy
→ concepts: ["recursive balance field", "field center", "golden ratio", "entropy", "PAC", "SEC", "RBF", "Poincaré activation"], entities: ["PAC", "SEC", "RBF"]
(Note: PHI constant definition → "golden ratio" concept; class names → human-readable; acronyms in BOTH lists)

Input: Code titled "DFT-PAC Metrics for SEC Phase Analysis", docstring mentions MED depth, QPL stability, FDO schema, CIP directory, CIMM compatibility
→ concepts: ["metrics", "SEC phase analysis", "PAC", "SEC", "MED"], entities: ["DFT-PAC", "SEC", "RBF", "MED", "QPL", "FDO", "CIP", "CIMM"]
(Note: EVERY acronym mentioned anywhere in text — including docstrings — becomes an entity. PAC/SEC/MED also concepts.)

Input: Architecture doc describing PAC framework, RBF simulation, CIP integration, mentions FastAPI and NumPy
→ concepts: ["architecture", "PAC", "RBF", "far-from-equilibrium dynamics"], entities: ["PAC", "RBF", "CIP", "FastAPI", "NumPy"]

Input: Utility file with `sha256()`, `slugify()`, list helpers
→ concepts: ["utility", "hash", "slugify"], entities: []

Input: Config YAML with simulation parameters
→ concepts: ["configuration", "simulation", "convergence threshold"], entities: []
"""

# Fixed footer: closing instruction
_EXTRACT_FOOTER = """Prioritize distinctive, searchable terms. Match extraction volume to content complexity."""

# Assembled prompt (nodes import this)
EXTRACT_USER = _EXTRACT_HEADER + EXTRACT_RULES + EXTRACT_EXAMPLES + _EXTRACT_FOOTER


# =========================================================================
# Judge Node — Decide what to do with this chunk
# =========================================================================

JUDGE_SYSTEM = """You are a knowledge graph curator. Given a piece of content and existing knowledge base entries that match it, you decide: should this become a NEW node, is it a DUPLICATE of something existing, should it EXTEND an existing node, or should it be SKIPPED?"""

# Fixed header: input template + decision options + JSON schema
_JUDGE_HEADER = """Decide what to do with this content.

SOURCE: {source_id}
PATH: {chunk_path}

<content_preview>
{content_preview}
</content_preview>

EXISTING VAULT MATCHES (from searching the knowledge base):
{vault_context}

Decide ONE of:
- "new" — This content contains genuinely new knowledge not covered by existing entries
- "duplicate" — This covers the same ground as an existing entry (specify which one)
- "extend" — This adds meaningful new information to an existing entry (specify which one)
- "skip" — This content is trivial (empty files, boilerplate, lock files, auto-generated)

Return ONLY a JSON object:
{{
    "decision": "new|duplicate|extend|skip",
    "target_id": null,
    "reasoning": "Brief explanation of why this decision",
    "confidence": 0.0-1.0
}}

"""

# Tunable: decision rules (optimizer modifies this)
JUDGE_RULES = """Rules:
- "target_id" is required for "duplicate" and "extend", null for "new" and "skip"
- Empty or near-empty files → "skip"
- LICENSE/boilerplate → "skip" unless it's unusual
- Config files that just set versions → "skip"
- If vault_context shows a very close match (same concept, same repo), lean toward "duplicate"
- If vault_context shows a related but different angle, lean toward "new" with connections
- "extend" is for when the new content adds substantial detail to an existing sparse entry"""

# Assembled prompt (nodes import this)
JUDGE_USER = _JUDGE_HEADER + JUDGE_RULES


# =========================================================================
# Actualize Node — Full FDO generation
# =========================================================================

ACTUALIZE_SYSTEM = """You are a knowledge graph builder following PAC (Potential-Actualization Conservation) theory. You transform raw source content into structured knowledge objects called FDOs (Field Data Objects). Each FDO has a clear summary, technical details, and connections to other knowledge.

Key principles:
- Preserve specifics: equations, constants, thresholds, exact names
- Focus on WHAT and WHY, not line-by-line walkthrough
- Use [[wikilink]] notation for connections
- Be concise but complete — quality over quantity
- Never fabricate or expand acronyms you're unsure of
- If content is sparse, produce a proportionally brief FDO"""

# Fixed header: input template + JSON schema
_ACTUALIZE_HEADER = """Transform this content into a knowledge object (FDO).

SOURCE: {source_id}
PATH: {chunk_path}
DOMAIN: {domain}
FDO ID: {fdo_id}
PAC PARENT: {pac_parent}

<content>
{content}
</content>

EXISTING RELATED KNOWLEDGE (use [[id]] wikilinks to reference these):
{vault_context}

SIBLING FILES (for context): {siblings}

Return ONLY a JSON object:
{{
    "title": "Clear, descriptive title",
    "summary": "1-2 paragraph overview",
    "details": "Technical details in markdown (2-6 paragraphs, proportional to content depth)",
    "connections": "How this relates to other knowledge. Use [[existing-id]] for vault entries listed above. Use [[new-slug]] for concepts not yet in vault.",
    "open_questions": "Unresolved issues or areas for exploration (or empty string if none)",
    "tags": ["specific", "searchable", "tags"],
    "related_ids": ["existing-vault-ids-that-are-relevant"],
    "confidence_note": "How well-established is this content"
}}

"""

# Tunable: quality rules (optimizer modifies this)
ACTUALIZE_RULES = """CRITICAL RULES:
- Title should be descriptive and specific, not just the filename
- Summary should explain significance, not just describe structure
- Tags must be specific (not "code", "file", "data")  
- Do NOT expand acronyms unless you're 100% certain of their meaning in THIS context
- If content is trivial/empty, keep the FDO proportionally minimal
- Preserve exact equations, constants, and technical terms
- [[wikilinks]] only — no bare URLs in connections"""

# Assembled prompt (nodes import this)
ACTUALIZE_USER = _ACTUALIZE_HEADER + ACTUALIZE_RULES


# =========================================================================
# Actualize Node — Directory synthesis (not tuned)
# =========================================================================

ACTUALIZE_DIRECTORY_USER = """Synthesize a parent knowledge object from its children.
PAC principle: f(Parent) = Σ f(Children) — the parent summarizes all children.

DIRECTORY: {dir_path}
SOURCE: {source_id}  
DOMAIN: {domain}
FDO ID: {fdo_id}

CHILDREN:
{children_desc}

EXISTING VAULT CONTEXT:
{vault_context}

Return ONLY a JSON object:
{{
    "title": "Title for this knowledge cluster",
    "summary": "2-3 paragraph synthesis of what this directory represents",
    "details": "How children relate to each other, overall architecture",
    "connections": "Broader connections using [[wikilinks]]",
    "tags": ["relevant", "tags"]
}}

Rules:
- Synthesize, don't just list
- The parent should convey the gestalt that no single child captures
- Use [[child-id]] wikilinks to reference children"""


# =========================================================================
# Validate Node — Quality checks
# =========================================================================

VALIDATE_SYSTEM = """You are a quality assurance reviewer for a knowledge graph. Check FDOs for accuracy, consistency, and common errors. Be strict but fair."""

# Fixed header: FDO template + intro
_VALIDATE_HEADER = """Review this FDO for quality issues.

FDO ID: {fdo_id}
ORIGINAL SOURCE PATH: {source_path}
SOURCE TYPE: {source_type}

<fdo>
{fdo_json}
</fdo>

Check for these specific issues:
"""

# Tunable: validation criteria (optimizer modifies this)
VALIDATE_RULES = """1. ACRONYM HALLUCINATION: Are any acronyms expanded incorrectly? (e.g., CIP might be "Cognition Index Protocol" not "Cardano Improvement Proposal" — check context)
2. OVER-ANALYSIS: Is a trivial/empty file getting paragraphs of speculation?
3. TITLE QUALITY: Is the title descriptive and accurate?
4. TAG QUALITY: Are tags specific enough? (reject generic: "code", "file", "data", "content")
5. SUMMARY ACCURACY: Does the summary match what the content actually says?
6. WIKILINK FORMAT: Are [[wikilinks]] properly formatted?
"""

# Fixed footer: JSON output schema + strictness instructions
_VALIDATE_FOOTER = """
Return ONLY a JSON object:
{{
    "passed": true/false,
    "errors": ["Critical issues that must be fixed"],
    "warnings": ["Minor issues worth noting"],
    "suggested_fixes": {{
        "title": "corrected title or null",
        "summary": "corrected summary or null",
        "tags": ["corrected", "tags"] or null
    }}
}}

Be STRICT on acronym hallucination — this is the most common and damaging error.
Pass FDOs that are reasonable even if imperfect. Only fail on actual errors."""

# Assembled prompt (nodes import this)
VALIDATE_USER = _VALIDATE_HEADER + VALIDATE_RULES + _VALIDATE_FOOTER


# =========================================================================
# Extend Node — Update existing FDO with new information (not tuned)
# =========================================================================

EXTEND_USER = """An existing knowledge object needs to be updated with new information from a different source.

EXISTING FDO:
{existing_fdo}

NEW CONTENT (from {source_path}):
<content>
{content}
</content>

What new information does this content add? Return ONLY a JSON object:
{{
    "additions_to_details": "New paragraphs to append to Details section",
    "additions_to_connections": "New [[wikilinks]] connections to add",
    "new_tags": ["any", "new", "tags"],
    "new_source_note": "Brief note about this additional source"
}}

Only include genuinely new information, not duplicates of what's already there."""
