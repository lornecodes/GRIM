"use client";

import { useBridgeApi } from "@/hooks/useBridgeApi";
import { formatCount } from "@/lib/format";
import { StatCard } from "@/components/ui/StatCard";
import { ChartContainer, CHART_COLORS } from "@/components/ui/ChartContainer";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { ErrorState } from "@/components/ui/ErrorState";
import type { TokenRecentEntry } from "@/lib/types";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Bar,
} from "recharts";

function formatDate(dateStr: string): string {
  // "2026-02-28" → "Feb 28"
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const recentColumns: Column<TokenRecentEntry>[] = [
  {
    key: "timestamp",
    label: "Time",
    render: (row) => formatTimestamp(row.timestamp),
    width: "140px",
  },
  { key: "caller_id", label: "Caller", width: "80px" },
  {
    key: "model",
    label: "Model",
    render: (row) => {
      const model = row.model ?? "—";
      // Shorten model names: "claude-sonnet-4-6" → "sonnet-4-6"
      return model.replace("claude-", "");
    },
    width: "120px",
  },
  {
    key: "input_tokens",
    label: "Input",
    align: "right",
    render: (row) => formatCount(row.input_tokens),
  },
  {
    key: "output_tokens",
    label: "Output",
    align: "right",
    render: (row) => formatCount(row.output_tokens),
  },
  {
    key: "total_tokens",
    label: "Total",
    align: "right",
    render: (row) => formatCount(row.total_tokens),
  },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div
      className="text-[11px] rounded-lg px-3 py-2 shadow-lg border"
      style={{
        background: CHART_COLORS.tooltip.bg,
        borderColor: CHART_COLORS.tooltip.border,
      }}
    >
      <div className="text-grim-text-dim mb-1">{label}</div>
      {payload.map((entry: { name: string; value: number; color: string }) => (
        <div key={entry.name} className="flex items-center gap-2">
          <div
            className="w-2 h-2 rounded-full"
            style={{ background: entry.color }}
          />
          <span className="text-grim-text">
            {entry.name}: {formatCount(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function TokenDashboard() {
  const { summary, byDay, recent, loading, error, refresh } = useBridgeApi();

  if (error) {
    return (
      <ErrorState
        title="AI Bridge Offline"
        message={`Cannot reach the token tracking bridge. ${error}`}
        onRetry={refresh}
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-sm text-grim-text-dim">Loading token data...</div>
      </div>
    );
  }

  const totals = summary?.totals;
  const chartData = byDay.map((d) => ({
    ...d,
    label: formatDate(d.date),
  }));

  const callerData = summary?.by_caller
    ? Object.entries(summary.by_caller).map(([caller, data]) => ({
        caller,
        ...data,
      }))
    : [];

  return (
    <div className="flex flex-col gap-5 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-grim-text">Token Usage</h2>
        <button
          onClick={refresh}
          className="text-[11px] text-grim-text-dim hover:text-grim-accent transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-3">
        <StatCard
          label="Total Tokens"
          value={formatCount(totals?.total_tokens ?? 0)}
          subtitle="Last 30 days"
          accent
        />
        <StatCard
          label="Input"
          value={formatCount(totals?.input_tokens ?? 0)}
          subtitle="tokens"
        />
        <StatCard
          label="Output"
          value={formatCount(totals?.output_tokens ?? 0)}
          subtitle="tokens"
        />
        <StatCard
          label="API Calls"
          value={totals?.calls ?? 0}
          subtitle="Last 30 days"
        />
      </div>

      {/* Daily chart */}
      {chartData.length > 0 && (
        <ChartContainer title="Daily Token Usage (30d)" height={220}>
          <AreaChart data={chartData}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke={CHART_COLORS.grid}
              vertical={false}
            />
            <XAxis
              dataKey="label"
              tick={{ fill: CHART_COLORS.axis, fontSize: 10 }}
              axisLine={{ stroke: CHART_COLORS.grid }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: CHART_COLORS.axis, fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => formatCount(v)}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone"
              dataKey="input_tokens"
              name="Input"
              stackId="1"
              stroke={CHART_COLORS.input}
              fill={CHART_COLORS.input}
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="output_tokens"
              name="Output"
              stackId="1"
              stroke={CHART_COLORS.output}
              fill={CHART_COLORS.output}
              fillOpacity={0.3}
            />
          </AreaChart>
        </ChartContainer>
      )}

      {/* Caller breakdown */}
      {callerData.length > 1 && (
        <ChartContainer title="By Caller" height={Math.max(120, callerData.length * 40)}>
          <BarChart data={callerData} layout="vertical">
            <CartesianGrid
              strokeDasharray="3 3"
              stroke={CHART_COLORS.grid}
              horizontal={false}
            />
            <XAxis
              type="number"
              tick={{ fill: CHART_COLORS.axis, fontSize: 10 }}
              axisLine={{ stroke: CHART_COLORS.grid }}
              tickLine={false}
              tickFormatter={(v) => formatCount(v)}
            />
            <YAxis
              type="category"
              dataKey="caller"
              tick={{ fill: CHART_COLORS.axis, fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={80}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar
              dataKey="input_tokens"
              name="Input"
              stackId="1"
              fill={CHART_COLORS.input}
              radius={[0, 0, 0, 0]}
            />
            <Bar
              dataKey="output_tokens"
              name="Output"
              stackId="1"
              fill={CHART_COLORS.output}
              radius={[0, 4, 4, 0]}
            />
          </BarChart>
        </ChartContainer>
      )}

      {/* Recent calls table */}
      <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
        <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
          Recent API Calls
        </div>
        <DataTable
          columns={recentColumns}
          rows={recent}
          emptyMessage="No API calls recorded yet"
        />
      </div>
    </div>
  );
}
