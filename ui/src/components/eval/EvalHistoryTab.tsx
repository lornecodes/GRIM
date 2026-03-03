"use client";

import { useMemo } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { useEval } from "@/hooks/useEval";

interface Props {
  eval_: ReturnType<typeof useEval>;
}

const SUITE_COLORS: Record<string, string> = {
  routing: "#60a5fa",
  keyword_routing: "#34d399",
  skill_matching: "#fbbf24",
  tool_groups: "#f97316",
  single_turn: "#a78bfa",
  multi_turn: "#f472b6",
};

const CHART_COLORS = {
  overall: "#7c6fef",
  grid: "#2a2a3a",
  axis: "#8888a0",
};

export function EvalHistoryTab({ eval_ }: Props) {
  const { runs } = eval_;

  // Discover all suite names across runs
  const suiteNames = useMemo(() => {
    const names = new Set<string>();
    for (const r of runs) {
      if (r.suite_scores) {
        for (const k of Object.keys(r.suite_scores)) names.add(k);
      }
    }
    return Array.from(names).sort();
  }, [runs]);

  // Transform runs into chart data (sorted by time ascending)
  const chartData = useMemo(() => {
    const sorted = [...runs]
      .filter((r) => r.status === "completed")
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
      .slice(-20);

    return sorted.map((r) => {
      const point: Record<string, string | number> = {
        label: r.run_id.slice(0, 6),
        date: new Date(r.timestamp).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
        }),
        overall: Math.round(r.overall_score * 100),
        passed: r.passed_cases,
        total: r.total_cases,
        duration: Math.round(r.duration_ms / 1000),
        git: r.git_sha,
      };
      // Add per-suite scores
      if (r.suite_scores) {
        for (const [name, score] of Object.entries(r.suite_scores)) {
          point[name] = Math.round(score * 100);
        }
      }
      return point;
    });
  }, [runs]);

  if (runs.length === 0) {
    return (
      <div className="text-xs text-grim-text-dim text-center py-12">
        No eval runs yet. Run an evaluation from the Run tab to see history.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Score over time chart */}
      <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
        <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
          Score Over Time
        </div>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={320}>
            <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={CHART_COLORS.grid} strokeDasharray="0" />
              <XAxis
                dataKey="date"
                stroke={CHART_COLORS.axis}
                tick={{ fontSize: 10 }}
              />
              <YAxis
                stroke={CHART_COLORS.axis}
                tick={{ fontSize: 10 }}
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 10, paddingTop: 8 }}
              />
              {/* Overall score — filled area */}
              <Area
                type="monotone"
                dataKey="overall"
                name="Overall"
                stroke={CHART_COLORS.overall}
                fill={CHART_COLORS.overall}
                fillOpacity={0.1}
                strokeWidth={2}
              />
              {/* Per-suite lines — no fill */}
              {suiteNames.map((name) => (
                <Area
                  key={name}
                  type="monotone"
                  dataKey={name}
                  name={name}
                  stroke={SUITE_COLORS[name] || "#888"}
                  fill="transparent"
                  fillOpacity={0}
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  connectNulls
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-xs text-grim-text-dim text-center py-12">
            Need at least one completed run to show chart.
          </div>
        )}
      </div>

      {/* Run history table */}
      <div className="bg-grim-surface border border-grim-border rounded-xl overflow-hidden">
        <div className="text-[11px] text-grim-text-dim uppercase tracking-wider px-4 pt-4 pb-2">
          Run History
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-grim-text-dim border-b border-grim-border">
              <th className="text-left px-4 py-2 font-medium">Run</th>
              <th className="text-left px-3 py-2 font-medium">Date</th>
              <th className="text-left px-3 py-2 font-medium">Tier</th>
              <th className="text-right px-3 py-2 font-medium">Score</th>
              <th className="text-right px-3 py-2 font-medium">Pass Rate</th>
              <th className="text-right px-3 py-2 font-medium">Duration</th>
              <th className="text-left px-4 py-2 font-medium">Git</th>
            </tr>
          </thead>
          <tbody>
            {[...runs].reverse().map((r) => (
              <tr key={r.run_id} className="border-b border-grim-border/50 hover:bg-grim-bg/30">
                <td className="px-4 py-2 font-mono text-grim-accent">{r.run_id.slice(0, 8)}</td>
                <td className="px-3 py-2 text-grim-text-dim">
                  {new Date(r.timestamp).toLocaleString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </td>
                <td className="px-3 py-2 text-grim-text-dim">{r.tier}</td>
                <td className="px-3 py-2 text-right">
                  <span
                    className={
                      r.overall_score >= 0.9
                        ? "text-emerald-400"
                        : r.overall_score >= 0.7
                          ? "text-amber-400"
                          : "text-red-400"
                    }
                  >
                    {(r.overall_score * 100).toFixed(1)}%
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  {r.passed_cases}/{r.total_cases}
                </td>
                <td className="px-3 py-2 text-right text-grim-text-dim">
                  {(r.duration_ms / 1000).toFixed(1)}s
                </td>
                <td className="px-4 py-2 font-mono text-grim-text-dim">
                  {r.git_sha?.slice(0, 7) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div
      className="text-[11px] rounded-lg px-3 py-2 border"
      style={{
        backgroundColor: "#12121a",
        borderColor: "#2a2a3a",
      }}
    >
      <div className="text-grim-text-dim mb-1">{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <div
            className="w-2 h-2 rounded-full"
            style={{ background: entry.color }}
          />
          <span className="text-grim-text">
            {entry.name}: {entry.value}%
          </span>
        </div>
      ))}
    </div>
  );
}
