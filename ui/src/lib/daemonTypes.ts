// ── Daemon / Pipeline types ──
// Matches server daemon API responses.

export type PipelineStatus = "backlog" | "ready" | "dispatched" | "review" | "merged" | "failed" | "blocked";

export interface PipelineItem {
  id: string;
  story_id: string;
  project_id: string;
  status: PipelineStatus;
  job_id: string | null;
  workspace_id: string | null;
  priority: number;
  assignee: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
  attempts: number;
  daemon_retries: number;
  pr_number: number | null;
  pr_url: string | null;
  pr_comment_count: number;
}

export interface DaemonStatus {
  running: boolean;
  uptime_seconds: number;
  started_at: string | null;
  scan_count: number;
  dispatch_count: number;
  last_scan_at: string | null;
  last_dispatch_at: string | null;
  pipeline: Record<PipelineStatus, number>;
  recent_errors: string[];
}

/** Columns for the unified pipeline kanban (extends pool KANBAN_COLUMNS). */
export const PIPELINE_COLUMNS: { id: PipelineStatus; label: string }[] = [
  { id: "backlog", label: "Backlog" },
  { id: "ready", label: "Ready" },
  { id: "dispatched", label: "Dispatched" },
  { id: "review", label: "Review" },
  { id: "merged", label: "Merged" },
  { id: "failed", label: "Failed" },
  { id: "blocked", label: "Blocked" },
];

/** Pipeline status → dot color class. */
export const PIPELINE_STATUS_COLORS: Record<PipelineStatus, string> = {
  backlog: "bg-gray-400",
  ready: "bg-blue-400",
  dispatched: "bg-yellow-400 animate-pulse",
  review: "bg-purple-400",
  merged: "bg-green-400",
  failed: "bg-red-400",
  blocked: "bg-orange-400",
};

/** Assignee type → badge styling. */
export const ASSIGNEE_BADGE: Record<string, { label: string; color: string }> = {
  code: { label: "Code", color: "bg-green-400/10 border-green-400/30 text-green-400" },
  research: { label: "Research", color: "bg-blue-400/10 border-blue-400/30 text-blue-400" },
  audit: { label: "Audit", color: "bg-orange-400/10 border-orange-400/30 text-orange-400" },
  plan: { label: "Plan", color: "bg-purple-400/10 border-purple-400/30 text-purple-400" },
};
