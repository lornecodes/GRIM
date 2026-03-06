// ── Pool / Mission Control types ──
// Matches server pool API responses and WebSocket event shapes.

export type JobStatus = "queued" | "running" | "complete" | "failed" | "blocked" | "cancelled" | "review";
export type JobType = "code" | "research" | "audit" | "plan" | "index";
export type JobPriority = "low" | "normal" | "high" | "critical";

export interface SlotInfo {
  slot_id: string;
  busy: boolean;
  current_job_id: string | null;
}

export interface PoolStatus {
  running: boolean;
  slots: SlotInfo[];
  active_jobs: number;
  active_workspaces: number;
  resource_locks: Record<string, unknown>;
}

export interface PoolJob {
  id: string;
  job_type: JobType;
  status: JobStatus;
  priority: JobPriority;
  instructions: string;
  plan: string | null;
  workspace_id: string | null;
  assigned_slot: string | null;
  retry_count: number;
  max_retries: number;
  result: string | null;
  error: string | null;
  transcript: TranscriptEntry[];
  created_at: string;
  updated_at: string;
  started_at?: string;
  completed_at?: string;
  cost_usd?: number | null;
  num_turns?: number | null;
  kronos_fdo_ids?: string[];
}

export interface PoolMetrics {
  completed_count: number;
  failed_count: number;
  running_count: number;
  queued_count: number;
  avg_duration_ms: number;
  total_cost_usd: number;
  throughput_per_hour: number;
  period_hours: number;
}

// ── Transcript / streaming types ──

export interface TranscriptEntry {
  /** Sequential index within the job transcript */
  seq: number;
  /** Client-side receive timestamp (ms since epoch) */
  timestamp: number;
  /** Entry type */
  type: "text" | "tool_use" | "tool_result" | "result" | "lifecycle" | "unknown";
  /** Text content (for text entries) */
  text?: string;
  /** Tool name (for tool_use entries) */
  toolName?: string;
  /** Tool input (for tool_use entries) */
  toolInput?: unknown;
  /** Output preview (for tool_result entries) */
  outputPreview?: string;
  /** Cost in USD (for result entries) */
  costUsd?: number;
  /** Turn count (for result entries) */
  numTurns?: number;
}

// ── Pool WebSocket event types ──

export type PoolEventType =
  | "job_submitted"
  | "job_started"
  | "job_complete"
  | "job_failed"
  | "job_blocked"
  | "job_cancelled"
  | "job_review"
  | "agent_output"
  | "agent_tool_result"
  // Daemon intelligence events (Mewtwo Phase 3)
  | "daemon_escalation"
  | "daemon_auto_resolved"
  // Daemon PR lifecycle events (Mewtwo Phase 4)
  | "daemon_approved"
  | "daemon_rejected";

export interface PoolWsEvent {
  type: "pool_event";
  /** Event subtype */
  // eslint-disable-next-line @typescript-eslint/no-redundant-type-constituents
  event_type: PoolEventType | string;
  job_id: string;
  timestamp: string;
  [key: string]: unknown;
}

// ── Kanban column type ──

export const KANBAN_COLUMNS: { id: JobStatus; label: string }[] = [
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "review", label: "Review" },
  { id: "complete", label: "Complete" },
  { id: "failed", label: "Failed" },
];

// ── Helpers ──

/** Map a raw server transcript block to a TranscriptEntry */
export function parseTranscriptBlock(block: Record<string, unknown>, seq: number): TranscriptEntry {
  const role = block.role as string | undefined;
  const content = block.content as Array<Record<string, unknown>> | undefined;

  if (role === "assistant" && Array.isArray(content)) {
    // Flatten assistant content blocks into individual entries
    // (caller should iterate; this returns the first block as a representative)
    const first = content[0];
    if (first?.type === "text") {
      return { seq, timestamp: Date.now(), type: "text", text: first.text as string };
    }
    if (first?.type === "tool_use") {
      return { seq, timestamp: Date.now(), type: "tool_use", toolName: first.name as string, toolInput: first.input };
    }
  }

  if (role === "result") {
    return {
      seq,
      timestamp: Date.now(),
      type: "result",
      costUsd: block.cost_usd as number | undefined,
      numTurns: block.num_turns as number | undefined,
    };
  }

  return { seq, timestamp: Date.now(), type: "unknown" };
}

/** Flatten a raw transcript (list of message dicts) into TranscriptEntry[] */
export function flattenTranscript(raw: Array<Record<string, unknown>>): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  let seq = 0;

  for (const block of raw) {
    const role = block.role as string | undefined;
    const content = block.content as Array<Record<string, unknown>> | undefined;

    if (role === "assistant" && Array.isArray(content)) {
      for (const sub of content) {
        if (sub.type === "text") {
          entries.push({ seq: seq++, timestamp: Date.now(), type: "text", text: sub.text as string });
        } else if (sub.type === "tool_use") {
          entries.push({
            seq: seq++,
            timestamp: Date.now(),
            type: "tool_use",
            toolName: sub.name as string,
            toolInput: sub.input,
          });
        }
      }
    } else if (role === "result") {
      entries.push({
        seq: seq++,
        timestamp: Date.now(),
        type: "result",
        costUsd: block.cost_usd as number | undefined,
        numTurns: block.num_turns as number | undefined,
      });
    }
  }

  return entries;
}

/** Job type → display color class */
export const JOB_TYPE_COLORS: Record<JobType, string> = {
  code: "text-green-400",
  research: "text-blue-400",
  audit: "text-orange-400",
  plan: "text-purple-400",
  index: "text-yellow-400",
};

/** Job type → background badge class */
export const JOB_TYPE_BG: Record<JobType, string> = {
  code: "bg-green-400/10 border-green-400/30",
  research: "bg-blue-400/10 border-blue-400/30",
  audit: "bg-orange-400/10 border-orange-400/30",
  plan: "bg-purple-400/10 border-purple-400/30",
  index: "bg-yellow-400/10 border-yellow-400/30",
};

/** Status → dot color */
export const STATUS_COLORS: Record<JobStatus, string> = {
  queued: "bg-gray-400",
  running: "bg-green-400 animate-pulse",
  complete: "bg-blue-400",
  failed: "bg-red-400",
  blocked: "bg-yellow-400",
  cancelled: "bg-gray-500",
  review: "bg-purple-400",
};
