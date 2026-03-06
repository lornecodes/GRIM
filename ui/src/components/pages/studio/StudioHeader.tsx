"use client";

import { useState, useCallback } from "react";
import { useGrimStore } from "@/store";
import type { PoolJob } from "@/lib/poolTypes";
import { JOB_TYPE_BG, JOB_TYPE_COLORS, STATUS_COLORS } from "@/lib/poolTypes";

function timeSince(dateStr: string): string {
  const ms = Date.now() - new Date(dateStr).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${(ms / 3_600_000).toFixed(1)}h ago`;
}

interface Props {
  job: PoolJob;
  onRefetch: () => void;
}

export function StudioHeader({ job, onRefetch }: Props) {
  const setAgentsTab = useGrimStore((s) => s.setAgentsTab);
  const [actionLoading, setActionLoading] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const doAction = useCallback(async (url: string, method = "POST", body?: unknown) => {
    setActionLoading(true);
    try {
      const opts: RequestInit = { method, headers: { "Content-Type": "application/json" } };
      if (body) opts.body = JSON.stringify(body);
      await fetch(`${apiBase}${url}`, opts);
      onRefetch();
    } catch {
      // ignore
    } finally {
      setActionLoading(false);
    }
  }, [apiBase, onRefetch]);

  const typeColor = JOB_TYPE_COLORS[job.job_type] ?? "text-grim-text-dim";
  const typeBg = JOB_TYPE_BG[job.job_type] ?? "bg-grim-surface";
  const statusDot = STATUS_COLORS[job.status] ?? "bg-gray-400";

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Back button */}
      <button
        onClick={() => setAgentsTab("jobs")}
        className="text-[11px] text-grim-text-dim hover:text-grim-text transition-colors"
      >
        &larr; Jobs
      </button>

      {/* Divider */}
      <div className="w-px h-4 bg-grim-border" />

      {/* Job ID */}
      <span className="text-[11px] font-mono text-grim-text">{job.id}</span>

      {/* Type badge */}
      <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${typeBg} ${typeColor}`}>
        {job.job_type}
      </span>

      {/* Status */}
      <div className="flex items-center gap-1">
        <div className={`w-2 h-2 rounded-full ${statusDot}`} />
        <span className="text-[10px] text-grim-text-dim">{job.status}</span>
      </div>

      {/* Elapsed */}
      <span className="text-[10px] text-grim-text-dim font-mono">
        {timeSince(job.created_at)}
      </span>

      {/* Cost */}
      {job.cost_usd != null && (
        <span className="text-[10px] font-mono text-grim-accent">
          ${job.cost_usd.toFixed(4)}
        </span>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Action buttons */}
      {(job.status === "running" || job.status === "queued") && (
        <button
          onClick={() => doAction(`/api/pool/jobs/${job.id}/cancel`)}
          disabled={actionLoading}
          className="text-[10px] px-2 py-1 rounded border border-red-400/30 text-red-400 hover:bg-red-400/10 transition-colors disabled:opacity-40"
        >
          Cancel
        </button>
      )}

      {job.status === "failed" && (
        <button
          onClick={() => doAction(`/api/pool/jobs/${job.id}/retry`)}
          disabled={actionLoading}
          className="text-[10px] px-2 py-1 rounded border border-yellow-400/30 text-yellow-400 hover:bg-yellow-400/10 transition-colors disabled:opacity-40"
        >
          Retry
        </button>
      )}

      {job.status === "review" && (
        <>
          <button
            onClick={() => doAction(`/api/pool/jobs/${job.id}/review`, "POST", { action: "approve" })}
            disabled={actionLoading}
            className="text-[10px] px-2 py-1 rounded border border-green-400/30 text-green-400 hover:bg-green-400/10 transition-colors disabled:opacity-40"
          >
            Approve
          </button>
          <button
            onClick={() => doAction(`/api/pool/jobs/${job.id}/review`, "POST", { action: "reject" })}
            disabled={actionLoading}
            className="text-[10px] px-2 py-1 rounded border border-red-400/30 text-red-400 hover:bg-red-400/10 transition-colors disabled:opacity-40"
          >
            Reject
          </button>
        </>
      )}
    </div>
  );
}
