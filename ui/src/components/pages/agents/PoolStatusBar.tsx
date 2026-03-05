"use client";

import type { PoolStatus } from "@/hooks/usePoolStatus";

interface PoolStatusBarProps {
  status: PoolStatus | null;
  enabled: boolean;
  activeJobs: number;
  queuedJobs: number;
}

export function PoolStatusBar({ status, enabled, activeJobs, queuedJobs }: PoolStatusBarProps) {
  if (!enabled) {
    return (
      <div className="bg-grim-surface border border-grim-border rounded-lg px-4 py-2 flex items-center gap-3 text-xs font-mono text-grim-text-dim">
        <div className="w-2 h-2 rounded-full bg-grim-border flex-shrink-0" />
        <span>Pool offline</span>
      </div>
    );
  }

  const busySlots = status?.slots.filter((s) => s.busy).length ?? 0;
  const totalSlots = status?.slots.length ?? 0;
  const running = status?.running ?? false;

  return (
    <div className="bg-grim-surface border border-grim-border rounded-lg px-4 py-2 flex items-center gap-4 text-xs font-mono">
      {/* Pool running indicator */}
      <div className="flex items-center gap-1.5">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 ${
            running ? "bg-green-400" : "bg-red-400"
          }`}
        />
        <span className="text-grim-text-dim">
          {running ? "Pool active" : "Pool stopped"}
        </span>
      </div>

      <div className="w-px h-3 bg-grim-border" />

      {/* Slots */}
      <div className="flex items-center gap-1.5">
        <span className="text-grim-text">{busySlots}</span>
        <span className="text-grim-text-dim">/ {totalSlots} slots</span>
      </div>

      <div className="w-px h-3 bg-grim-border" />

      {/* Active jobs */}
      <div className="flex items-center gap-1.5">
        {activeJobs > 0 && (
          <span className="text-green-400">{activeJobs} running</span>
        )}
        {activeJobs > 0 && queuedJobs > 0 && (
          <span className="text-grim-text-dim">/</span>
        )}
        {queuedJobs > 0 && (
          <span className="text-yellow-400">{queuedJobs} queued</span>
        )}
        {activeJobs === 0 && queuedJobs === 0 && (
          <span className="text-grim-text-dim">idle</span>
        )}
      </div>
    </div>
  );
}
