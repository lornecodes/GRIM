# Pool Management Protocol

## Overview

Manage the GRIM execution pool — submit jobs, monitor agents, handle lifecycle events. The pool runs Claude Code agents in isolated git worktree workspaces.

## Job Types

| Type | Agent | Use For |
|------|-------|---------|
| `code` | Claude Code (Opus) | Writing code, fixing bugs, implementing features |
| `research` | Claude Code (Sonnet) | Investigating codebases, reading docs, searching vault |
| `audit` | Claude Code (Sonnet) | Security review, code quality, test coverage |
| `plan` | Claude Code (Sonnet) | Architecture planning, design docs, implementation plans |

## Job Lifecycle

```
QUEUED → RUNNING → COMPLETE → (review) → MERGED/CLOSED
                 → FAILED → (retry) → QUEUED
                 → BLOCKED → (clarify) → RUNNING
         ↓
      CANCELLED
```

## Phase 1: Assess

Determine what the user wants:
- **Submit**: New work to be done → need job_type + instructions
- **Status**: Check what's happening → pool_status or pool_job_detail
- **Cancel**: Stop a job → need job_id
- **Clarify**: Answer a question → need job_id + answer
- **Retry**: Re-run a failure → need job_id
- **Review**: Accept/reject work → need job_id + decision
- **List**: Browse jobs → optional filters

## Phase 2: Context

Before acting, check the current state:

```
pool_status → overview (running, queued, blocked counts)
pool_list_jobs(status="running") → what's actively executing
pool_list_jobs(status="blocked") → what needs attention
```

For specific jobs:
```
pool_job_detail(job_id) → full details including transcript
pool_job_logs(job_id, offset=0, limit=50) → recent output
```

## Phase 3: Execute

### Submit a Job
```
pool_submit(
  job_type="code",
  instructions="Implement the user authentication flow...",
  priority="normal",
  kronos_fdo_ids=["grim-architecture"]  # optional context
)
```

**Guidelines:**
- Write clear, specific instructions (the agent sees only these + its tools)
- Include acceptance criteria in the instructions
- Set priority appropriately: critical (blocks everything), high (urgent), normal (default), low (background)
- Link relevant Kronos FDOs for context

### Cancel a Job
```
pool_cancel(job_id="job-xxx")
```
Only works for `queued` or `blocked` jobs. Running jobs must complete.

### Provide Clarification
```
pool_clarify(job_id="job-xxx", answer="Use JWT tokens with 24h expiry")
```
When an agent is `blocked` waiting for a decision, provide the answer to unblock it.

### Retry a Failed Job
```
pool_retry(job_id="job-xxx")
```
Re-queues the job. Check the error first via `pool_job_detail` to understand why it failed.

### Review Completed Work
```
# First, inspect the changes
pool_workspace_diff(workspace_id="ws-xxx")

# Then approve (merges to base branch) or reject (destroys workspace)
pool_review(job_id="job-xxx", action="approve")
pool_review(job_id="job-xxx", action="reject")
```

## Phase 4: Report

After each action, report:
- What was done (submitted/cancelled/clarified/retried/reviewed)
- Current state (job status, queue depth)
- Next steps if any (e.g., "Job is now running, check back in a few minutes")

## Quality Gates

### Before Submitting
- [ ] Instructions are specific and actionable
- [ ] Job type matches the work
- [ ] No duplicate jobs already queued/running
- [ ] Priority is justified

### Before Approving Review
- [ ] Read the workspace diff
- [ ] Check transcript for errors
- [ ] Verify tests passed (if applicable)
- [ ] Code quality is acceptable
