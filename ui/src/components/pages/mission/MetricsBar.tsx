"use client";

import { useGrimStore } from "@/store";

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="flex-1 min-w-[120px] bg-grim-surface border border-grim-border rounded-lg px-3 py-2">
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono text-grim-text mt-0.5">{value}</div>
      {sub && <div className="text-[9px] text-grim-text-dim mt-0.5">{sub}</div>}
    </div>
  );
}

export function MetricsBar() {
  const metrics = useGrimStore((s) => s.poolMetrics);
  const status = useGrimStore((s) => s.poolStatus);

  const activeJobs = status?.active_jobs ?? 0;
  const slotsTotal = status?.slots?.length ?? 0;
  const slotsBusy = status?.slots?.filter((s) => s.busy).length ?? 0;

  return (
    <div className="flex gap-2 flex-wrap">
      <StatCard
        label="Active Jobs"
        value={activeJobs}
        sub={`${slotsBusy}/${slotsTotal} slots`}
      />
      <StatCard
        label="Queued"
        value={metrics?.queued_count ?? 0}
      />
      <StatCard
        label="Completed"
        value={metrics?.completed_count ?? 0}
        sub={metrics ? `${metrics.throughput_per_hour}/hr` : undefined}
      />
      <StatCard
        label="Avg Duration"
        value={metrics ? `${(metrics.avg_duration_ms / 1000).toFixed(1)}s` : "--"}
      />
      <StatCard
        label="Total Cost"
        value={metrics ? `$${metrics.total_cost_usd.toFixed(2)}` : "--"}
        sub={metrics ? `last ${metrics.period_hours}h` : undefined}
      />
    </div>
  );
}
