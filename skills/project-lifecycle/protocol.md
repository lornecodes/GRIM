# Project Lifecycle — Roadmaps, Designs, and ADRs

> **Skill**: `project-lifecycle`
> **Version**: 1.0
> **Purpose**: Maintain the DFI project knowledge system so every decision and design is captured, linked, and discoverable across sessions.

---

## When This Applies

Activate this skill when:
- Starting a new project or major feature
- About to build something that needs a design spec
- Just finished building something that warrants an ADR
- Reviewing project health or progress
- A new Claude session needs to understand what's been done and why

## The Three Artifact Types

### 1. Project FDOs (`proj-*`)
**The roadmap.** Lives in `kronos-vault/projects/`.

- Created at project kickoff
- Contains phases, milestones (checkbox list), current focus, blockers
- Links to ALL design and ADR FDOs for the project
- Updated continuously as work progresses
- Template: `templates/project-template.md`

### 2. Design FDOs (`design-*`)
**The plan.** Lives in the project's domain directory (e.g., `ai-systems/`).

- Created BEFORE building a feature
- Contains requirements, architecture, implementation plan
- Status: `seed` (drafted) → `developing` (building) → `stable` (implemented)
- Links to project FDO and related specs
- Template: `templates/design-template.md`

### 3. ADR FDOs (`adr-*`)
**The record.** Lives in `kronos-vault/decisions/`.

- Created AFTER building something
- Captures: context, decision, alternatives, code references
- Always includes file paths and commit hashes
- Status is always `stable` — ADRs are historical records
- Links to project FDO and design FDO (if one exists)
- Template: `templates/adr-template.md`

## Workflow

### Kickoff — New Project

1. Create `proj-{name}` FDO from `project-template.md`
2. Fill in: overview, phases, initial milestones
3. Set status to `seed`, confidence to 0.5
4. Link to any existing related FDOs

### Pre-Build — Design Spec

1. Check if a `design-{feature}` FDO already exists
2. If not, create from `design-template.md`
3. Fill in: goal, requirements, architecture, implementation plan
4. Link to `proj-{project}` and mark as `seed`
5. Get user approval on the design before proceeding

> **CHECKPOINT**: Design reviewed before implementation begins.

### Build Phase

1. Update `design-{feature}` status to `developing`
2. Update `proj-{project}` current focus
3. Mark milestones as complete when done: `- [x] Milestone`

### Post-Build — ADR Capture

1. Create `adr-{decision-slug}` FDO from `adr-template.md`
2. Fill in:
   - **Context**: What problem or situation prompted this
   - **Decision**: What was decided and why
   - **Decision Boundaries**: What GRIM can handle autonomously vs. escalate to human
   - **Acceptance Criteria**: Verifiable completion conditions (checklist format)
   - **Dependencies**: What must exist first (related ADRs, features, infrastructure)
   - **Estimated Complexity**: Simple / Moderate / Complex with justification
   - **Alternatives**: What else was considered (table format)
   - **Code References**: Actual file paths, commit hashes, PRs
   - **Consequences**: Trade-offs, follow-up work
3. Link to `proj-{project}` and `design-{feature}` (if exists)
4. Set status to `stable`, confidence to 0.9

### Post-Build — Roadmap Update

1. Update `proj-{project}`:
   - Mark completed milestones
   - Update current focus to next item
   - Bump confidence if appropriate
   - Add new design/ADR FDOs to `related:` list
2. Update `design-{feature}` status to `stable`

### Review — Project Health

Check a project for completeness:
- [ ] All completed features have ADRs
- [ ] All planned features have design FDOs (or are too small to need one)
- [ ] ADRs include Decision Boundaries section (autonomy contract for GRIM dispatch)
- [ ] ADRs include Acceptance Criteria (verifiable completion conditions)
- [ ] Project milestones are current
- [ ] Current focus matches actual work
- [ ] All FDOs are bidirectionally linked
- [ ] Confidence reflects actual state

## When to Create Each Type

| Situation | Create |
|-----------|--------|
| Starting a new project | `proj-*` |
| Planning a feature (>1 day of work) | `design-*` |
| Made a tech choice (framework, library, architecture) | `adr-*` |
| Completed a milestone | Update `proj-*` + `adr-*` |
| Fixed a significant bug | `adr-*` (captures root cause + fix) |
| Changed approach mid-build | `adr-*` (captures why the pivot) |
| Trivial change (typo, config tweak) | Nothing — not everything needs an artifact |

## ADR Code Reference Format

Always include concrete code pointers:

```markdown
## Code References

- **Files**: `GRIM/server/app.py`, `GRIM/ui/src/hooks/useGrimSocket.ts`
- **Commit**: `88a8391` — feat: Docker release pipeline
- **Key functions**: `websocket_chat()` in server/app.py:265
- **Config**: `docker-compose.yml` lines 12-38
```

## Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Project | `proj-{name}` | `proj-grim`, `proj-reality-engine` |
| Design | `design-{feature}` | `design-grim-ui-nextjs`, `design-claw-sandboxing` |
| ADR | `adr-{decision}` | `adr-cliproxyapi-integration`, `adr-langgraph-over-ironclaw` |

## Vault Sync

After project lifecycle changes, verify related FDOs are current:
1. Did the project status change? Update the `proj-*` FDO
2. Did architecture or features change? Update architecture and skill FDOs
3. Were new ADRs or designs created? Add bidirectional links to related FDOs
4. Update `updated:` dates on any modified FDOs

> Skipping this step is how FDOs drift from reality. If you changed something meaningful, sync it.

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
