# Git Operations Protocol

> Agent skill for safe git interaction.
> GRIM never touches git — this protocol is for ops-agents only.

## When This Applies

Any time an agent needs to commit, branch, push, or inspect git state.

## Protected Branches

These branches require extra caution:
- `main` — no force push, no direct commits without review
- `release/*` — no force push
- Any branch with upstream tracking — check before rewriting

## Operations

### Status Check (always first)
```
git status
git diff --stat
```
Before any write operation, understand what's changed.

### Commit
1. Run `git status` — review staged/unstaged changes
2. Stage only the intended files (`git add <specific files>`)
3. Never use `git add .` or `git add -A` without reviewing first
4. Check for secrets: scan staged files for API keys, tokens, passwords
5. Write commit message following conventional format:
   - `feat:` — new feature
   - `fix:` — bug fix
   - `docs:` — documentation
   - `refactor:` — code restructuring
   - `chore:` — maintenance
   - `research:` — experiment work
6. Commit with `-m` message

### Branch
1. Base new branches off main (unless specified otherwise)
2. Follow naming convention:
   - `feature/<brief-slug>` — new features
   - `fix/<brief-slug>` — bug fixes
   - `research/<brief-slug>` — experiments
   - `chore/<brief-slug>` — maintenance
3. Verify branch doesn't already exist

### Push
1. Verify remote exists
2. Verify you're on the correct branch
3. Use `--set-upstream` for new branches
4. Never `--force` on shared branches

### Tag
1. Use annotated tags (`git tag -a`)
2. Follow semver for releases
3. Include descriptive message

## Commit Message Format

```
type(scope): brief description

Extended description if needed.
Explain what and why, not how.

Refs: #issue-number (if applicable)
```

## Safety Rules

1. **No force push** on main or shared branches
2. **No secrets** — scan before every commit
3. **Atomic commits** — one logical change per commit
4. **Clean working tree** — don't leave uncommitted changes after operations
5. **Verify before push** — check branch, remote, and diff

## Vault Sync

After significant commits, check if vault FDOs need updating:
1. Did the committed changes affect architecture, features, or project status?
2. If yes, run the `vault-sync` skill or manually update affected FDOs
3. Key FDOs to check: project trackers (`proj-*`), architecture specs, skill inventories
4. Update `updated:` dates on any modified FDOs

> Skipping this step is how FDOs drift from reality. If you changed something meaningful, sync it.

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
