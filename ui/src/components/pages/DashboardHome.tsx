"use client";

import { IconDashboard } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useBridgeApi } from "@/hooks/useBridgeApi";
import { useActiveAgents } from "@/hooks/useActiveAgents";
import { useGrimMemory } from "@/hooks/useGrimMemory";
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
        <MemoryWidgetTile />
        <ActiveAgentsTile />
        <TokenUsageTile />
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
        <div className="max-h-64 overflow-y-auto space-y-2">
          {activeAgents.map((agent) => {
            const toolTraces = agent.traces.filter((t) => t.cat === "tool");
            const stdout = agent.traces
              .filter((t) => t.cat === "node" && t.action === "end" && t.step_content)
              .map((t) => t.step_content!)
              .join("\n");
            return (
              <div
                key={agent.node}
                className="bg-grim-bg rounded-sm border border-grim-border overflow-hidden font-mono"
              >
                {/* Mini terminal header */}
                <div className="flex items-center gap-2 px-2 py-1 bg-grim-surface/50 border-b border-grim-border/50">
                  <span className="text-[10px] text-grim-text-dim">
                    {agent.label.toLowerCase()}@grim
                  </span>
                  <span className="text-[9px] text-grim-text-dim ml-auto tabular-nums">
                    {agent.totalMs}ms
                  </span>
                </div>
                {/* Terminal body — tool calls + stdout */}
                <div className="px-2 py-1 space-y-0.5 max-h-32 overflow-y-auto">
                  {toolTraces.slice(0, 3).map((t, i) => (
                    <div key={i} className="text-[10px] truncate">
                      <span className="text-grim-accent select-none">$ </span>
                      <span className="text-trace-tool">{t.tool || t.text}</span>
                      {t.output_preview && (
                        <span className="text-grim-text-dim ml-1">
                          → {t.output_preview.slice(0, 60)}
                        </span>
                      )}
                    </div>
                  ))}
                  {stdout && (
                    <div className="text-[10px] text-grim-text leading-relaxed mt-0.5 line-clamp-3">
                      {stdout.slice(0, 200)}
                    </div>
                  )}
                  {toolTraces.length === 0 && !stdout && agent.traces.length > 0 && (
                    <div className="text-[10px] text-grim-text-dim truncate">
                      <span className={`${catColors[agent.traces[agent.traces.length - 1].cat] || "text-grim-text-dim"}`}>
                        {agent.traces[agent.traces.length - 1].text}
                      </span>
                    </div>
                  )}
                  {agent.traces.length === 0 && (
                    <div className="text-[10px] text-grim-text-dim">
                      <span className="text-grim-accent">$ </span>
                      <span className="animate-pulse">_</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </DashboardTile>
  );
}

// ---------------------------------------------------------------------------
// Memory Widget Tile
// ---------------------------------------------------------------------------

function MemoryWidgetTile() {
  const { memory, loading, error } = useGrimMemory();
  const setActivePage = useGrimStore((s) => s.setActivePage);

  const sections = memory?.sections || {};
  const objectives = sections["Active Objectives"] || "";
  const recentTopics = sections["Recent Topics"] || "";

  // Parse bullet items from objectives
  const objectiveItems = objectives
    .split("\n")
    .filter((l) => l.trim().startsWith("-"))
    .map((l) => l.replace(/^-\s*/, "").trim())
    .slice(0, 4);

  // Parse bullet items from recent topics
  const topicItems = recentTopics
    .split("\n")
    .filter((l) => l.trim().startsWith("-"))
    .map((l) => l.replace(/^-\s*/, "").trim())
    .slice(0, 3);

  return (
    <DashboardTile
      title="Working Memory"
      headerRight={
        <button
          onClick={() => setActivePage("memory")}
          className="text-[10px] text-grim-accent hover:underline"
        >
          view all
        </button>
      }
    >
      {loading && (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      )}
      {error && (
        <div className="text-xs text-red-400 py-4 text-center">
          Memory offline
        </div>
      )}
      {!loading && !error && (
        <div className="space-y-3">
          {/* Objectives */}
          {objectiveItems.length > 0 && (
            <div>
              <div className="text-[10px] text-grim-text-dim uppercase mb-1">
                Objectives
              </div>
              <div className="space-y-0.5">
                {objectiveItems.map((item, i) => {
                  const statusMatch = item.match(/^\[(\w+)\]\s*(.*)$/);
                  return (
                    <div key={i} className="flex items-start gap-1.5 text-[11px]">
                      {statusMatch ? (
                        <>
                          <span className={`text-[9px] px-1 py-0.5 rounded shrink-0 ${
                            statusMatch[1] === "active"
                              ? "bg-grim-success/15 text-grim-success"
                              : "bg-grim-border/30 text-grim-text-dim"
                          }`}>
                            {statusMatch[1]}
                          </span>
                          <span className="text-grim-text truncate">{statusMatch[2]}</span>
                        </>
                      ) : (
                        <>
                          <span className="text-grim-accent shrink-0">{"\u25CF"}</span>
                          <span className="text-grim-text truncate">{item}</span>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Recent Topics */}
          {topicItems.length > 0 && (
            <div>
              <div className="text-[10px] text-grim-text-dim uppercase mb-1">
                Recent Topics
              </div>
              <div className="space-y-0.5">
                {topicItems.map((item, i) => (
                  <div key={i} className="text-[11px] text-grim-text-dim truncate">
                    {item}
                  </div>
                ))}
              </div>
            </div>
          )}

          {objectiveItems.length === 0 && topicItems.length === 0 && (
            <div className="text-xs text-grim-text-dim py-2 text-center">
              Memory will populate as you interact with GRIM
            </div>
          )}
        </div>
      )}
    </DashboardTile>
  );
}
