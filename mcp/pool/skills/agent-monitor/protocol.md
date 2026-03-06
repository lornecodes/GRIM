# Agent Monitor Protocol

## Overview

Monitor running pool agents — read transcripts, assess progress, diagnose issues. This is a read-only skill; it never modifies jobs.

## Phase 1: Identify

Determine what to monitor:

```
pool_status → how many agents running, queued, blocked
pool_list_jobs(status="running") → active agents
pool_list_jobs(status="blocked") → agents waiting for input
```

If user asks about a specific job, go directly to Phase 2.

## Phase 2: Read

### Quick Check
```
pool_job_detail(job_id) → status, type, instructions, created_at, updated_at
```

### Transcript Tail (most recent output)
```
pool_job_logs(job_id, offset=-20, limit=20) → last 20 lines
```

For long transcripts, paginate:
```
pool_job_logs(job_id, offset=0, limit=50) → first 50 lines
pool_job_logs(job_id, offset=50, limit=50) → next 50
```

### Workspace Changes
```
pool_workspace_diff(workspace_id) → what files the agent has changed so far
```

## Phase 3: Analyze

**Healthy signs:**
- Recent `updated_at` timestamp (within last few minutes)
- Tool calls in transcript (agent is actively working)
- Progressive file changes in workspace diff

**Warning signs:**
- `updated_at` is stale (>10 minutes without activity)
- Repeated identical tool calls (agent is looping)
- Error messages in transcript
- No workspace changes despite running for a while

**Blocked agents:**
- Status is `blocked` with `clarification_question` set
- Agent needs human input to proceed
- Use `pool_clarify` (from pool-manage skill) to unblock

## Phase 4: Report

Summarize for the user:
- **Status**: Running/blocked/stuck + elapsed time
- **Progress**: What the agent has done so far (key tool calls, files changed)
- **Current activity**: What it's doing right now (last few transcript lines)
- **Issues**: Any errors, loops, or stalls detected
- **Recommendation**: Wait, intervene (clarify/cancel), or review
