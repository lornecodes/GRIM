# Code Execution Protocol

> Agent skill for writing, running, and testing code.
> GRIM never codes — this protocol is for coder-agents only.

## When This Applies

This protocol governs ALL code changes made by agents on GRIM's behalf.

## The Thinker/Doer Contract

- **GRIM** decides WHAT needs to happen (thinker)
- **Coder Agent** decides HOW to implement it (doer)
- The agent receives: task description, context, constraints
- The agent returns: summary of changes, validation results

## Protocol

### Phase 1: Understand

Before writing any code:
1. Read the relevant source files
2. Understand the existing architecture and conventions
3. Check for specs (`.spec/`), CIP (`.cip/`), or meta.yaml
4. Identify the minimal set of files to touch

### Phase 2: Plan

Before making changes:
1. Identify what needs to change and why
2. Keep the diff as small as possible
3. If change affects >3 files, flag for review
4. If breaking existing interfaces, stop and report back to GRIM

### Phase 3: Implement

Making the changes:
1. One concern per change — don't mix refactoring with features
2. Follow the target language's idioms and conventions
3. Preserve existing formatting and style
4. Add error handling for new code paths
5. Never hardcode secrets, paths, or credentials

### Phase 4: Validate

After implementation:
1. Run existing tests — ALL must pass
2. Check for compilation/parse errors
3. Run linters if available
4. If tests fail, fix before reporting

### Phase 5: Report

Return to GRIM:
1. Files created/modified (with brief rationale per file)
2. Test results (pass/fail/skip counts)
3. Any concerns or follow-up items
4. Breaking changes (if any, with migration path)

## Safety Rules

1. **Sandbox required** — all execution happens in IronClaw sandbox (Phase 2+)
2. **No network access** unless explicitly granted per-task
3. **No filesystem writes** outside declared target scope
4. **No credential access** — use environment variables
5. **Graceful degradation** — if something fails, report back, don't retry blindly

## Quality Gates

- [ ] Changes match task description
- [ ] All existing tests pass
- [ ] New code has error handling
- [ ] No secrets in code
- [ ] Diff is minimal and focused
- [ ] Code is idiomatic

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
