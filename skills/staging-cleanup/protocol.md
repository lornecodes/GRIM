# Staging Cleanup Protocol

> Operator agent protocol for cleaning up old staging directories.

## When This Applies

Periodically or on user request. Staging directories accumulate over time and need cleanup.

## Process

1. **List all staging jobs** — scan `/workspace/staging/` for job directories
2. **Read manifests** — check `manifest.json` for creation time and status
3. **Apply retention rules** — identify jobs eligible for deletion
4. **Report** — show what will be deleted (dry run by default)
5. **Delete** — remove eligible directories if confirmed

## Retention Rules

- **Keep** jobs less than 24 hours old (configurable)
- **Keep** jobs with status "in_progress" (active work)
- **Keep** jobs with unresolved rejections in `audit/rejections.jsonl`
- **Delete** jobs with status "completed" older than retention period
- **Delete** empty job directories (no output files)

## Safety

- Always list before deleting (dry run first)
- Never delete the staging root directory itself
- Log all deletions with timestamps and job IDs
- Report total freed disk space after cleanup
