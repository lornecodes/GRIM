"use client";

import { useState, useEffect } from "react";
import { IconAgents } from "@/components/icons/NavIcons";
import { useGrimStore } from "@/store";
import { useActiveAgents, type ActiveAgent } from "@/hooks/useActiveAgents";
import type { TraceEvent } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types (engine status only — agent data comes from traces now)
// ---------------------------------------------------------------------------

interface EngineStatus {
  available: boolean;
  version?: string;
  uptime_secs?: number;
  metrics?: {
    requests_total: number;
    requests_failed: number;
    active_sessions: number;
    uptime_seconds: number;
  };
}

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
// Component
// ---------------------------------------------------------------------------

export function AgentTeam() {
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const ironclawStatus = useGrimStore((s) => s.ironclawStatus);
  const setIronclawStatus = useGrimStore((s) => s.setIronclawStatus);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  const activeAgents = useActiveAgents(10);
  const grimAgents = activeAgents.filter((a) => a.tier === "grim");
  const clawAgents = activeAgents.filter((a) => a.tier === "ironclaw");

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  useEffect(() => {
    async function fetchStatus() {
      try {
        const resp = await fetch(`${apiBase}/api/ironclaw/status`);
        if (resp.ok) {
          const data = await resp.json();
          setEngineStatus(data);
          setIronclawStatus(data.available ? "connected" : "disconnected");
        }
      } catch {
        setIronclawStatus("disconnected");
      }
    }

    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [apiBase, setIronclawStatus]);

  const connected = ironclawStatus === "connected";

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconAgents size={32} className="text-grim-accent" />
        <div>
          <h2 className="text-lg font-semibold text-grim-text">Agent Team</h2>
          <p className="text-xs text-grim-text-dim">
            Active agents from recent conversation
          </p>
        </div>
        {isStreaming && (
          <span className="ml-auto text-xs text-grim-accent animate-pulse">
            Live
          </span>
        )}
      </div>

      {/* GRIM Agents */}
      {grimAgents.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-grim-text mb-3">
            GRIM Agents ({grimAgents.length})
          </h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {grimAgents.map((agent) => (
              <ActiveAgentCard key={agent.node} agent={agent} />
            ))}
          </div>
        </section>
      )}

      {/* IronClaw Agents */}
      {clawAgents.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-semibold text-grim-text">
              IronClaw Agents ({clawAgents.length})
            </h3>
            <div className="flex items-center gap-1.5 ml-auto">
              <div
                className={`w-2 h-2 rounded-full ${
                  connected ? "bg-green-400" : "bg-red-400"
                }`}
              />
              <span className="text-xs text-grim-text-dim">
                {connected ? "Connected" : "Offline"}
              </span>
            </div>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {clawAgents.map((agent) => (
              <ActiveAgentCard key={agent.node} agent={agent} />
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {activeAgents.length === 0 && (
        <div className="text-center py-12 text-sm text-grim-text-dim">
          No agents active yet. Start a conversation to see agent activity.
        </div>
      )}

      {/* Engine Status Footer */}
      {engineStatus?.available && (
        <div className="bg-grim-surface border border-grim-border rounded-lg px-4 py-2.5 flex items-center gap-4 text-xs text-grim-text-dim">
          <span className="font-mono">v{engineStatus.version}</span>
          <span>{formatUptime(engineStatus.uptime_secs || 0)} uptime</span>
          {engineStatus.metrics && (
            <>
              <span>{engineStatus.metrics.requests_total} requests</span>
              {engineStatus.metrics.requests_failed > 0 && (
                <span className="text-red-400">
                  {engineStatus.metrics.requests_failed} errors
                </span>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ActiveAgentCard({ agent }: { agent: ActiveAgent }) {
  // Extract agent reasoning from step_content on node-end traces
  const agentStdout = agent.traces
    .filter((t) => t.cat === "node" && t.action === "end" && t.step_content)
    .map((t) => t.step_content!)
    .join("\n");

  return (
    <div className="bg-grim-bg border border-grim-border rounded-sm overflow-hidden font-mono">
      {/* Terminal title bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-grim-surface border-b border-grim-border">
        <div className="flex gap-1">
          <div className="w-2 h-2 rounded-full bg-red-500/60" />
          <div className="w-2 h-2 rounded-full bg-yellow-500/60" />
          <div className="w-2 h-2 rounded-full bg-green-500/60" />
        </div>
        <span className="text-[11px] text-grim-text-dim flex-1 text-center">
          {agent.label.toLowerCase()}@grim
        </span>
        {agent.totalMs > 0 && (
          <span className="text-[10px] text-grim-text-dim tabular-nums">
            {agent.totalMs}ms
          </span>
        )}
      </div>

      {/* Terminal body */}
      <div className="max-h-80 overflow-y-auto p-2 space-y-0.5">
        {/* Tool calls as shell commands */}
        {agent.traces
          .filter((t) => t.cat === "tool")
          .map((t, i) => (
            <div key={`tool-${i}`} className="text-[11px] leading-relaxed">
              <div>
                <span className="text-grim-accent select-none">$ </span>
                <span className="text-trace-tool">{t.tool || t.text}</span>
                {t.input != null && (
                  <span className="text-grim-text-dim ml-1">
                    {String(typeof t.input === "string" ? t.input : JSON.stringify(t.input)).slice(0, 80)}
                  </span>
                )}
              </div>
              {t.output_preview && (
                <div className="text-grim-text pl-4 whitespace-pre-wrap break-all text-[10.5px] mb-1">
                  {t.output_preview.length > 300
                    ? t.output_preview.slice(0, 300) + "\n…"
                    : t.output_preview}
                </div>
              )}
            </div>
          ))}

        {/* Agent stdout / reasoning */}
        {agentStdout && (
          <div className="border-t border-grim-border/30 mt-1 pt-1.5">
            <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-0.5">
              stdout
            </div>
            <div className="text-[11px] text-grim-text leading-relaxed whitespace-pre-wrap break-words">
              {agentStdout.length > 600
                ? agentStdout.slice(0, 600) + "\n…"
                : agentStdout}
            </div>
          </div>
        )}

        {/* Trace log lines (non-tool, non-content) */}
        {agent.traces
          .filter((t) => t.cat !== "tool" && !t.step_content)
          .map((trace, i) => (
            <TraceLogLine key={`trace-${i}`} trace={trace} />
          ))}

        {agent.traces.length === 0 && (
          <div className="text-[10px] text-grim-text-dim">
            <span className="text-grim-accent select-none">$ </span>
            <span className="animate-pulse">_</span>
          </div>
        )}
      </div>
    </div>
  );
}

function TraceLogLine({ trace }: { trace: TraceEvent }) {
  return (
    <div className="text-[10.5px] leading-relaxed truncate">
      <span className="text-grim-text-dim tabular-nums mr-1.5">
        [{trace.ms}ms]
      </span>
      <span
        className={`mr-1 ${catColors[trace.cat] || "text-grim-text-dim"}`}
      >
        {trace.text}
      </span>
    </div>
  );
}

function formatUptime(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  return `${h}h ${m}m`;
}
