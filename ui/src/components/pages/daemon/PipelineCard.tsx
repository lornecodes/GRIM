"use client";

import type { PipelineItem } from "@/lib/daemonTypes";
import { PIPELINE_STATUS_COLORS, ASSIGNEE_BADGE } from "@/lib/daemonTypes";

function timeSince(dateStr: string): string {
  const ms = Date.now() - new Date(dateStr).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m`;
  return `${Math.floor(ms / 3_600_000)}h`;
}

interface PipelineCardProps {
  item: PipelineItem;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
}

export function PipelineCard({ item, onApprove, onReject }: PipelineCardProps) {
  const statusDot = PIPELINE_STATUS_COLORS[item.status] ?? "bg-gray-400";
  const badge = item.assignee ? ASSIGNEE_BADGE[item.assignee] : null;

  return (
    <div className="w-full text-left bg-grim-surface hover:bg-grim-surface-hover border border-grim-border rounded-md p-2 transition-colors">
      {/* Header: assignee badge + elapsed time */}
      <div className="flex items-center justify-between mb-1">
        {badge ? (
          <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${badge.color}`}>
            {badge.label}
          </span>
        ) : (
          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border bg-grim-border/30 border-grim-border text-grim-text-dim">
            —
          </span>
        )}
        <span className="text-[9px] text-grim-text-dim font-mono">
          {timeSince(item.updated_at)}
        </span>
      </div>

      {/* Story ID */}
      <div className="text-[11px] text-grim-text leading-tight mb-1 truncate">
        {item.story_id}
      </div>

      {/* Footer: status dot + project + PR link */}
      <div className="flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}`} />
        <span className="text-[9px] font-mono text-grim-text-dim truncate">
          {item.project_id}
        </span>
        {item.pr_url && (
          <a
            href={item.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-[9px] font-mono text-grim-accent hover:underline ml-auto"
          >
            PR #{item.pr_number}
          </a>
        )}
      </div>

      {/* Error preview for failed/blocked items */}
      {item.error && (
        <div className={`text-[9px] mt-1 line-clamp-2 ${
          item.status === "blocked" ? "text-orange-400" : "text-red-400"
        }`}>
          {item.error}
        </div>
      )}

      {/* Approve/Reject buttons for review items */}
      {item.status === "review" && onApprove && onReject && (
        <div className="flex gap-1.5 mt-2">
          <button
            onClick={(e) => { e.stopPropagation(); onApprove(item.id); }}
            className="flex-1 text-[9px] px-2 py-1 rounded border border-green-400/30 bg-green-400/10 text-green-400 hover:bg-green-400/20 transition-colors"
          >
            Approve
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onReject(item.id); }}
            className="flex-1 text-[9px] px-2 py-1 rounded border border-red-400/30 bg-red-400/10 text-red-400 hover:bg-red-400/20 transition-colors"
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
