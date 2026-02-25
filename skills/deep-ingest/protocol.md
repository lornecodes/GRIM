# Deep Knowledge Ingestion — Claude Skill Protocol

> **Skill**: `deep-ingest`  
> **Version**: 1.0  
> **Purpose**: Manually ingest source material into the Kronos vault as high-quality, interconnected FDOs.  
> **When to use**: For important source material (papers, repos, research) where quality matters more than speed.

---

## Prerequisites

Before starting, confirm you have:
- [ ] Access to the **source material** (file paths or repository)
- [ ] Access to the **Kronos vault** (default: `kronos-vault/`)
- [ ] The **FDO schema** loaded (see `kronos-vault/ai-systems/kronos/kronos-fdo-schema.md`)
- [ ] The **domain context** (physics, ai-systems, tools, personal)

---

## Phase 1: Survey

**Goal**: Understand the full scope before creating anything.

### Steps

1. **Read the top-level structure** — README, meta.yaml, table of contents, directory listing
2. **Identify logical units** — What are the natural "atoms" of knowledge? (papers, modules, experiments, specs)
3. **Estimate scope** — How many FDOs will this produce? (target: 1 FDO per distinct concept, not per file)
4. **Identify the domain** — physics | ai-systems | tools | personal
5. **Read for cross-references** — Note which concepts reference each other, what the dependency chain looks like

### Output: Ingestion Plan

Before creating any FDOs, produce a plan:

```
## Ingestion Plan: [Source Name]

**Source**: [path or URL]
**Domain**: [domain]
**Estimated FDOs**: [count]

### Planned FDOs
| ID (proposed) | Title | Source File(s) | Type |
|---------------|-------|----------------|------|
| kebab-case-id | Human Title | path/to/source | paper / module / spec / concept |

### Dependency Order
1. [id] — no dependencies (start here)
2. [id] — depends on [id]
...

### Identified Cross-Links
- [id] ↔ [id]: relationship description
- [id] → [existing-vault-id]: relationship description
```

**CHECKPOINT**: Present the plan to the user. Wait for approval before proceeding.

---

## Phase 2: Vault Scan — Deduplication Check

**Goal**: Avoid creating duplicates. Find existing FDOs to extend instead.

### Steps

1. **List existing vault FDOs** in the target domain folder
2. **For each planned FDO**, search the vault for:
   - Exact ID match → **EXTEND** (update the existing FDO)
   - Similar title/concept → **MERGE candidate** (flag for user decision)
   - Related concept → **LINK candidate** (note for Phase 5)
3. **Check `related` fields** of existing FDOs for concept overlap
4. **Search tags** for overlapping terms

### Decision Matrix

| Match Type | Action |
|------------|--------|
| Exact ID exists | Extend existing FDO (update Details, bump `updated` date) |
| Same concept, different ID | Ask user: merge into existing or create new? |
| Overlapping but distinct | Create new + add bidirectional links |
| No match | Create new FDO |

### Output: Updated Plan

Mark each planned FDO as: `NEW`, `EXTEND [existing-id]`, or `MERGE? [existing-id]`

**CHECKPOINT**: If any merges need user decision, ask now.

---

## Phase 3: Deep Read

**Goal**: Extract knowledge at research depth — not keyword extraction, but actual understanding.

### Steps

For each logical unit (in dependency order):

1. **Read the full source** — paper text, code, data, figures description
2. **Extract core claims** — What does this prove, demonstrate, or propose?
3. **Extract key equations** — LaTeX format, with variable definitions
4. **Extract quantitative results** — Numbers with error bounds, p-values, significance
5. **Extract methodology** — How was this validated? What tools/data?
6. **Extract falsification conditions** — What would disprove this?
7. **Identify connections** — What does this depend on? What does it enable?
8. **Note confidence level** — How established is this? (see scale below)

### Confidence Assessment

| Evidence Level | Confidence | Example |
|----------------|------------|---------|
| Derived from established physics/math | 0.8–0.9 | "Landauer's principle requires..." |
| Computational validation with error bounds | 0.6–0.8 | "φ clustering at p < 0.001" |
| Cross-domain convergence (3+ domains) | 0.7–0.9 | "Ξ appears in 5 independent domains" |
| Single-domain observation | 0.4–0.6 | "Found in cellular automata" |
| Speculative extrapolation | 0.2–0.4 | "This might explain gauge couplings" |
| Pure hypothesis | 0.1–0.2 | "We speculate that..." |

### Quality Gate

For each unit, verify before proceeding:
- [ ] Can you state the core claim in one sentence?
- [ ] Do you have at least one quantitative result?
- [ ] Do you know what it depends on and what depends on it?
- [ ] Can you separate established from speculative content?

If any answer is NO → re-read the source material.

---

## Phase 4: FDO Creation

**Goal**: Write high-quality FDOs that a researcher would actually want to read.

### FDO Template

```markdown
---
id: kebab-case-unique-id
title: "Human-Readable Title"
domain: [physics|ai-systems|tools|personal]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: [seed|developing|stable]
confidence: 0.X
related: [list, of, related-fdo-ids]
source_repos: [repo-name]
tags: [specific, searchable, terms]
pac_parent: parent-fdo-id      # if applicable
pac_children: [child-ids]       # if applicable
equations: ["LaTeX"]            # key equations
falsifiable: true|false
confidence_basis: "Why this confidence level"
---

# Title

## Summary

One paragraph: what this is, what it proves/shows, why it matters.
Must be standalone — someone reading only this paragraph should get the key insight.

## Details

### Core Claim
The main result or contribution, stated precisely.

### Evidence
Quantitative results with error bounds. What was measured, how, with what significance.

### Methodology
Brief description of approach. Link to code/data where applicable.

### Established vs Speculative
Clear separation. What follows from known physics? What is new? What is speculation?

## Connections

Prose describing relationships. Use [[wikilinks]] for vault navigation.
- [[dependency-id]] — What this builds on and why
- [[downstream-id]] — What builds on this

## Open Questions

Unresolved issues, areas for future work, known limitations.

## References

- Source: `path/to/source/file` in [repo-name]
- DOI: if published
- Related papers/resources
```

### Writing Rules

1. **Summary is king** — Write it as if the reader will only read this paragraph
2. **No filler** — Every sentence should carry information. Cut "This paper presents..." fluff
3. **Preserve precision** — Keep exact numbers, error bounds, p-values. Don't round or vague-ify
4. **Use the author's language** — Don't rephrase technical terms. If the source says "correlational structure ξ", say that
5. **Separate confidence levels** — If part of a paper is established and part speculative, note which is which
6. **Link aggressively** — Every concept that has its own FDO gets a [[wikilink]]
7. **Tags are for search** — Include the specific terms someone would search for

### Per-FDO Quality Gate

Before writing the file, verify:
- [ ] **Summary**: Standalone, one paragraph, captures the key insight
- [ ] **Core claim**: Precisely stated, not hand-wavy
- [ ] **Evidence**: At least one quantitative result with bounds
- [ ] **Connections**: At least 2 [[wikilinks]] to other FDOs (existing or planned)
- [ ] **Confidence**: Justified with `confidence_basis`
- [ ] **Tags**: 4–8 specific, searchable terms (not generic like "research")
- [ ] **No fabrication**: Every claim traces back to the source material

---

## Phase 5: Cross-Linking

**Goal**: Wire up bidirectional connections across the vault.

### Steps

1. **For each new FDO**, check its `related` list against the vault
2. **For each related FDO**, add the new FDO to its `related` list (if not already present)
3. **Add [[wikilinks]]** in the Connections section of both FDOs
4. **Update `updated` date** on any modified existing FDO
5. **Check for transitive links** — if A→B and B→C, should A→C exist?

### Link Types

| Relationship | How to Express |
|-------------|----------------|
| Depends on | "Builds on [[X]], which establishes..." |
| Enables | "[[Y]] extends this by..." |
| Validates | "Computationally validated in [[Z]]" |
| Contradicts | "Tension with [[W]] — see Open Questions" |
| Supersedes | "Replaces [[V]] — see `superseded_by`" |
| Same concept, different lens | "Related treatment in [[U]] from the turbulence domain" |

### Cross-Link Quality Gate

- [ ] Every new FDO has at least 2 connections
- [ ] All links are bidirectional (if A references B, B references A)
- [ ] No dead wikilinks (every `[[id]]` resolves to an actual file)
- [ ] PAC hierarchy is consistent (parent's children list includes this FDO)

---

## Phase 6: Validation

**Goal**: Catch errors before declaring done.

### Checklist

#### Schema Compliance
- [ ] Every FDO has all required frontmatter fields
- [ ] `id` matches filename (kebab-case)
- [ ] `domain` is a valid value
- [ ] `status` is a valid value
- [ ] `confidence` is between 0.0 and 1.0
- [ ] `created` and `updated` are valid dates

#### Content Quality
- [ ] Every Summary is standalone and informative
- [ ] No duplicate FDOs covering the same concept
- [ ] Speculative claims are clearly marked as such
- [ ] No fabricated claims or hallucinated results
- [ ] Numbers match the source material exactly

#### Graph Integrity
- [ ] All [[wikilinks]] resolve to existing files
- [ ] All `related` IDs have corresponding files
- [ ] PAC parent/children are consistent in both directions
- [ ] No orphan FDOs (every FDO has at least one connection)

#### Spot Check (pick 2 random FDOs)
- [ ] Re-read the source material for that FDO
- [ ] Verify the Summary accurately captures the core claim
- [ ] Verify at least one quantitative result matches the source
- [ ] Verify connections make sense

**CHECKPOINT**: Report validation results to user. List any issues found.

---

## Phase 7: Summary Report

**Goal**: Give the user a clear picture of what was created.

### Report Template

```
## Ingestion Complete: [Source Name]

**FDOs Created**: X new, Y extended, Z linked
**Domain**: [domain]
**Vault Path**: [path]

### New FDOs
| ID | Title | Confidence | Status |
|----|-------|------------|--------|
| ... | ... | ... | ... |

### Extended FDOs
| ID | What Changed |
|----|--------------|
| ... | ... |

### Cross-Links Added
- [id] ↔ [id]: [relationship]
- ...

### Validation
- Schema: ✅ All pass
- Content: ✅ / ⚠️ [issues]
- Graph: ✅ / ⚠️ [issues]

### Recommended Next Steps
- [ ] Review [specific FDO] — confidence assessment may need adjustment
- [ ] Consider creating FDOs for [concepts mentioned but not captured]
- [ ] Tune prompts for [specific domain patterns discovered]
```

---

## Appendix A: Domain-Specific Guidance

### Physics Domain (Dawn Field Theory)

**Key terms to preserve exactly**: PAC, SEC, RBF, MED, φ (phi), Ξ (Xi), ξ (xi), Ψ, γ (Euler-Mascheroni), Landauer, Feigenbaum, DPI
**Acronyms go in BOTH concepts and entities**
**Equations**: Always LaTeX, always define variables
**Confidence**: Based on derivation chain — if it follows from Landauer/DPI, it's 0.7+. If speculative, say so.

### AI Systems Domain

**Key terms**: GRIM, GAIA, Kronos, FDO, MCP, IronClaw, Fracton, Axiom
**Focus on**: Architecture decisions, interfaces, status
**Confidence**: Based on implementation status — working code = 0.7+, planned = 0.3

### Tools Domain

**Key terms**: CIP, cip-core, actualization, pipeline, vault
**Focus on**: What it does, how to use it, what it depends on
**Confidence**: Based on test coverage and usage

---

## Appendix B: Common Mistakes

| Mistake | Fix |
|---------|-----|
| Creating one FDO per file | Create one FDO per **concept**. Multiple files may contribute to one FDO |
| Generic tags ("research", "science") | Use specific searchable terms ("Landauer erasure", "cascade coupling") |
| Summarizing the file instead of the knowledge | Extract the **insight**, not the file structure |
| Confidence = 0.5 on everything | Actually assess. Use the confidence scale |
| Missing connections | Every FDO should connect. Orphans are a quality failure |
| Expanding acronyms incorrectly | Use the source's own terminology. Don't guess what CIP stands for |
| Mixing established and speculative | Separate clearly in text AND in confidence |
