"use client";

import { useGrimStore } from "@/store";
import type { PoolJob } from "@/lib/poolTypes";
import { JOB_TYPE_BG, JOB_TYPE_COLORS, STATUS_COLORS } from "@/lib/poolTypes";

function timeSince(dateStr: string): string {
  const ms = Date.now() - new Date(dateStr).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m`;
  return `${Math.floor(ms / 3_600_000)}h`;
}

export function JobKanbanCard({ job }: { job: PoolJob }) {
  const navigateToJob = useGrimStore((s) => s.navigateToJob);

  const typeColor = JOB_TYPE_COLORS[job.job_type] ?? "text-grim-text-dim";
  const typeBg = JOB_TYPE_BG[job.job_type] ?? "bg-grim-surface border-grim-border";
  const statusDot = STATUS_COLORS[job.status] ?? "bg-gray-400";

  return (
    <button
      onClick={() => navigateToJob(job.id)}
      className="w-full text-left bg-grim-surface hover:bg-grim-surface-hover border border-grim-border rounded-md p-2 transition-colors"
    >
      {/* Header: type badge + elapsed time */}
      <div className="flex items-center justify-between mb-1">
        <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${typeBg} ${typeColor}`}>
          {job.job_type}
        </span>
        <span className="text-[9px] text-grim-text-dim font-mono">
          {timeSince(job.created_at)}
        </span>
      </div>

      {/* Instructions preview */}
      <div className="text-[11px] text-grim-text line-clamp-2 leading-tight mb-1.5">
        {job.instructions.slice(0, 80)}
        {job.instructions.length > 80 ? "..." : ""}
      </div>

      {/* Footer: status dot + job id + cost */}
      <div className="flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}`} />
        <span className="text-[9px] font-mono text-grim-text-dim truncate">
          {job.id}
        </span>
        {job.cost_usd != null && (
          <span className="text-[9px] font-mono text-grim-accent ml-auto">
            ${job.cost_usd.toFixed(3)}
          </span>
        )}
      </div>

      {/* Error preview for failed jobs */}
      {job.error && (
        <div className="text-[9px] text-red-400 mt-1 line-clamp-1">
          {job.error}
        </div>
      )}
    </button>
  );
}
