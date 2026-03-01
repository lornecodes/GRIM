# IronClaw Review Protocol

> Audit agent protocol for reviewing staged execution output.

## When This Applies

Automatically triggered when IronClaw executes a task and stages output to the shared volume. The audit agent reviews every file before it enters the workspace.

## Review Process

1. **List all staged files** using `staging_list(job_id)`
2. **Read each file** using `staging_read(job_id, path)`
3. **Evaluate** each file against the criteria below
4. **Return a verdict** as structured JSON

## Security Checks (blocking)

- No hardcoded secrets, API keys, tokens, or credentials
- No destructive commands (`rm -rf /`, `DROP TABLE`, `FORMAT`, etc.)
- No suspicious downloads (`curl | bash`, `wget | sh`)
- No outbound data exfiltration patterns
- No attempts to read sensitive system files (`/etc/shadow`, SSH keys)

## Correctness Checks (blocking)

- File content aligns with the original task request
- Code has valid syntax (check for obvious parse errors)
- Output is complete — not truncated or partial
- No placeholder content ("TODO", "FIXME" without context)

## Style Checks (non-blocking, suggestions only)

- Follows visible project conventions
- Reasonable file naming (lowercase, hyphens/underscores)
- No obvious code smell (unused imports, dead code)
- Appropriate error handling where visible

## Verdict Format

Your final response MUST end with a JSON block:

```json
{
    "passed": true,
    "issues": [],
    "suggestions": ["Consider adding error handling"],
    "security_flags": [],
    "summary": "Clean output, matches task intent"
}
```

- `passed`: `true` only if zero blocking issues
- `issues`: List of specific, actionable blocking problems
- `suggestions`: Non-blocking improvements (don't block acceptance)
- `security_flags`: Any security concern, even minor
- `summary`: One-line verdict

## Re-review

If this is a re-review after feedback was sent, check specifically whether the previous issues were addressed. Note any that persist.

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Review criteria match current project standards
- [ ] Verdict format matches what the integrate node expects
