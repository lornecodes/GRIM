"use client";

import { IconDashboard } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useBridgeApi } from "@/hooks/useBridgeApi";
import { useActiveAgents } from "@/hooks/useActiveAgents";
import { formatCount } from "@/lib/format";
import { useGrimStore } from "@/store";

// ---------------------------------------------------------------------------
// Trace category colors (matches TraceEntry.tsx)
// ---------------------------------------------------------------------------

const catColors: Record<string, string> = {
  node: "text-trace-node",
  llm: "text-trace-llm",
  tool: "text-trace-tool",
  claw: "text-orange-400",
  graph: "text-trace-graph",
};

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export function DashboardHome() {
  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconDashboard size={32} className="text-grim-accent" />
        <div>
          <h2 className="text-lg font-semibold text-grim-text">
            Mission Control
          </h2>
          <p className="text-xs text-grim-text-dim">
            Overview of active systems
          </p>
        </div>
      </div>

      {/* Tile grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <TokenUsageTile />
        <ActiveAgentsTile />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Token Usage Tile
// ---------------------------------------------------------------------------

function TokenUsageTile() {
  const { summary, recent, loading, error } = useBridgeApi();

  return (
    <DashboardTile
      title="Token Usage"
      headerRight={
        summary ? (
          <span className="text-xs text-grim-accent font-semibold">
            {formatCount(summary.totals.total_tokens)}
          </span>
        ) : null
      }
    >
      {loading && (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      )}
      {error && (
        <div className="text-xs text-red-400 py-4 text-center">
          Bridge offline
        </div>
      )}
      {summary && (
        <div className="space-y-3">
          {/* Mini stat row */}
          <div className="grid grid-cols-3 gap-2">
            <MiniStat
              label="Input"
              value={formatCount(summary.totals.input_tokens)}
            />
            <MiniStat
              label="Output"
              value={formatCount(summary.totals.output_tokens)}
            />
            <MiniStat label="Calls" value={summary.totals.calls} />
          </div>

          {/* Recent calls log */}
          <div className="max-h-40 overflow-y-auto space-y-1">
            {recent.slice(0, 8).map((entry) => (
              <div
                key={entry.id}
                className="flex items-center gap-2 text-[11px]"
              >
                <span className="text-grim-text-dim min-w-[60px]">
                  {new Date(entry.timestamp).toLocaleTimeString("en-US", {
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
                <span className="text-grim-text truncate flex-1">
                  {(entry.model ?? "").replace("claude-", "")}
                </span>
                <span className="text-grim-text-dim tabular-nums">
                  {formatCount(entry.total_tokens)}
                </span>
              </div>
            ))}
            {recent.length === 0 && !loading && (
              <div className="text-[10px] text-grim-text-dim text-center py-2">
                No recent calls
              </div>
            )}
          </div>
        </div>
      )}
    </DashboardTile>
  );
}

function MiniStat({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="text-center">
      <div className="text-[10px] text-grim-text-dim uppercase">{label}</div>
      <div className="text-sm font-semibold text-grim-text">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active Agents Tile
// ---------------------------------------------------------------------------

function ActiveAgentsTile() {
  const activeAgents = useActiveAgents(5);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  return (
    <DashboardTile
      title="Active Agents"
      headerRight={
        <div className="flex items-center gap-2">
          {isStreaming && (
            <span className="text-[10px] text-grim-accent animate-pulse">
              Live
            </span>
          )}
          <span className="text-xs text-grim-text-dim">
            {activeAgents.length} active
          </span>
        </div>
      }
    >
      {activeAgents.length === 0 ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          No agent activity yet
        </div>
      ) : (
        <div className="max-h-52 overflow-y-auto space-y-2">
          {activeAgents.map((agent) => (
            <div
              key={agent.node}
              className="bg-grim-bg rounded border border-grim-border p-2"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-medium text-grim-text">
                  {agent.label}
                </span>
                <span className="text-[10px] text-grim-text-dim ml-auto tabular-nums">
                  {agent.totalMs}ms
                </span>
              </div>
              {/* Compact trace log — last 4 entries */}
              <div className="space-y-0.5">
                {agent.traces.slice(-4).map((trace, i) => (
                  <div
                    key={i}
                    className="text-[10px] text-grim-text-dim truncate"
                  >
                    <span
                      className={`font-bold uppercase mr-1 ${
                        catColors[trace.cat] || "text-grim-text-dim"
                      }`}
                    >
                      {trace.cat}
                    </span>
                    {trace.text}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </DashboardTile>
  );
}
