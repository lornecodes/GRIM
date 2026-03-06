# Workspace Management Protocol

## Overview

Pool agents work in isolated git worktree workspaces. Each workspace is a branch off the base repo. When a job completes, the workspace can be merged (squash-merge to base) or destroyed.

## Phase 1: Discover

```
pool_list_workspaces → all active workspaces with job linkage
```

Each workspace has:
- `id` — workspace identifier
- `job_id` — associated pool job
- `branch_name` — git branch (grim/{workspace_id})
- `status` — active, merged, or destroyed

Cross-reference with jobs:
```
pool_list_jobs(status="complete") → jobs ready for review
pool_list_jobs(status="review") → jobs awaiting approval
```

## Phase 2: Inspect

### Full Diff
```
pool_workspace_diff(workspace_id) → all changes vs base branch
```

### Job Context
```
pool_job_detail(job_id) → what was the agent asked to do?
pool_job_logs(job_id, offset=-20, limit=20) → how did it finish?
```

Look for:
- Files changed and their nature (new files, modifications, deletions)
- Test results in the transcript
- Error messages or warnings
- Whether the changes match the original instructions

## Phase 3: Decide

**Approve if:**
- Changes match the job instructions
- No obvious errors or security issues
- Tests passed (check transcript)
- Code quality is acceptable

**Reject if:**
- Changes are wrong or incomplete
- Tests failed
- Security vulnerabilities introduced
- Changes go beyond what was requested

**Flag for human review if:**
- Changes are large or complex
- Uncertain about correctness
- Changes affect critical paths

## Phase 4: Execute

### Approve (Merge)
```
pool_review(job_id, action="approve")
```
This squash-merges the workspace branch to base and cleans up the worktree.

### Reject (Destroy)
```
pool_review(job_id, action="reject")
```
This destroys the workspace and its branch. The changes are lost.

### Cleanup Abandoned Workspaces
For workspaces linked to failed/cancelled jobs:
```
pool_review(job_id, action="reject")  # Destroys the workspace
```
