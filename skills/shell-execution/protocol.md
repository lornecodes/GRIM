# Shell Execution Protocol

> Agent skill for running shell commands safely.
> GRIM never runs commands — this protocol is for agents only.

## When This Applies

Any time an agent needs to execute a shell command — builds, tests, tools, queries.

## Pre-execution Checklist

Before running ANY command:
1. **Category check** — does this command fit an allowed category?
2. **Safety check** — could this command be destructive?
3. **Timeout set** — is there a reasonable timeout?
4. **Working dir** — is the working directory correct?
5. **Environment** — are needed env vars available (without exposing secrets)?

## Allowed Command Categories

| Category | Examples | Notes |
|----------|----------|-------|
| Build | `cargo build`, `pip install`, `npm ci` | Package installs may need review |
| Test | `pytest`, `cargo test`, `npm test` | Always capture output |
| Lint | `cargo clippy`, `ruff check`, `eslint` | Non-destructive, always safe |
| Tools | `git`, `docker`, `rclone` | Follow tool-specific skills first |
| Query | `find`, `grep`, `ls`, `cat` | Read-only, always safe |

## Forbidden Patterns

Never execute commands matching these patterns:
- `rm -rf /` or any recursive delete of system paths
- `curl ... | bash` or `wget ... | sh` — arbitrary code execution
- `chmod 777` — insecure permissions
- `> /dev/sda` or disk-level writes
- Any command with inline credentials (`PASSWORD=xxx command`)
- `sudo` commands without explicit user approval

## Execution

1. Set working directory
2. Set timeout (default: 300s, max: 1800s)
3. Execute command
4. Capture stdout and stderr separately
5. Record exit code
6. Check for timeout

## Output Handling

- Truncate output > 60KB (keep first 30KB + last 30KB)
- Parse structured output (JSON, YAML) when possible
- Filter sensitive information from output before returning
- Include exit code in every response

## Error Handling

- **Exit code 0** — success, return output
- **Exit code non-zero** — failure, return stderr + stdout
- **Timeout** — kill process, report timeout with partial output
- **Permission denied** — report, suggest fix, don't retry with elevated privileges

## Background Processes

For long-running commands (servers, watchers):
1. Start with background flag
2. Record PID/process handle
3. Return immediately with process ID
4. Check status on demand
5. Kill cleanly on shutdown (SIGTERM → wait → SIGKILL)

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
