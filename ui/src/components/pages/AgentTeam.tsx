"use client";

import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { IconAgents } from "@/components/icons/NavIcons";
import { useGrimStore } from "@/store";
import { useActiveAgents, type ActiveAgent } from "@/hooks/useActiveAgents";
import type { TraceEvent } from "@/lib/types";
import { GrimTypingSprite } from "@/components/GrimTypingSprite";

// Lazy-load Graph Studio (heavy — contains ForceGraph2D canvas)
const GraphStudio = lazy(() =>
  import("@/components/graph/GraphStudio").then((m) => ({
    default: m.GraphStudio,
  }))
);

const AGENT_TABS = [
  { id: "team", label: "Team" },
  { id: "graph", label: "Graph Studio" },
] as const;

type AgentTabId = (typeof AGENT_TABS)[number]["id"];

// ---------------------------------------------------------------------------
// Types
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

interface AgentRosterEntry {
  id: string;
  name: string;
  role: string;
  description: string;
  tools: string[];
  color: string;
  toggleable: boolean;
  enabled: boolean;
}

interface IronClawRole {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  color: string;
}

interface IronClawAgentsData {
  enabled: boolean;
  roles: IronClawRole[];
  coordination_patterns?: string[];
  active_sessions: number;
  max_concurrent_sessions: number;
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
  const [roster, setRoster] = useState<AgentRosterEntry[]>([]);
  const [togglingAgent, setTogglingAgent] = useState<string | null>(null);
  const [clawRoles, setClawRoles] = useState<IronClawAgentsData | null>(null);
  const ironclawStatus = useGrimStore((s) => s.ironclawStatus);
  const setIronclawStatus = useGrimStore((s) => s.setIronclawStatus);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  const [activeTab, setActiveTab] = useState<AgentTabId>("team");
  const activeAgents = useActiveAgents(10);
  const grimAgents = activeAgents.filter((a) => a.tier === "grim");
  const clawAgents = activeAgents.filter((a) => a.tier === "ironclaw");

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchRoster = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/api/agents`);
      if (resp.ok) {
        const data = await resp.json();
        setRoster(data.agents || []);
      }
    } catch { /* ignore */ }
  }, [apiBase]);

  const toggleAgent = useCallback(async (id: string) => {
    setTogglingAgent(id);
    try {
      const resp = await fetch(`${apiBase}/api/agents/${id}/toggle`, { method: "POST" });
      if (resp.ok) {
        const data = await resp.json();
        setRoster((prev) =>
          prev.map((a) => (a.id === id ? { ...a, enabled: data.enabled } : a))
        );
      }
    } catch { /* ignore */ }
    setTogglingAgent(null);
  }, [apiBase]);

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

    async function fetchClawAgents() {
      try {
        const resp = await fetch(`${apiBase}/api/ironclaw/agents`);
        if (resp.ok) setClawRoles(await resp.json());
      } catch { /* ignore */ }
    }

    fetchStatus();
    fetchRoster();
    fetchClawAgents();
    const interval = setInterval(() => { fetchStatus(); fetchClawAgents(); }, 30000);
    return () => clearInterval(interval);
  }, [apiBase, setIronclawStatus, fetchRoster]);

  const connected = ironclawStatus === "connected";

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-8">
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

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-grim-border">
        {AGENT_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? "border-b-2 border-grim-accent text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Graph Studio tab */}
      {activeTab === "graph" && (
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-96 text-xs text-grim-text-dim">
              Loading Graph Studio...
            </div>
          }
        >
          <GraphStudio />
        </Suspense>
      )}

      {/* Team tab — existing content */}
      {activeTab === "team" && <>

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

      {/* Agent Roster */}
      {roster.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-grim-text mb-3">
            Roster
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {roster.map((agent) => (
              <RosterCard
                key={agent.id}
                agent={agent}
                toggling={togglingAgent === agent.id}
                onToggle={() => toggleAgent(agent.id)}
              />
            ))}
          </div>
        </section>
      )}

      {/* IronClaw Engine Agents */}
      {clawRoles && clawRoles.roles.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-semibold text-grim-text">
              IronClaw Engine Agents
            </h3>
            <div className="flex items-center gap-1.5 ml-auto">
              <div
                className={`w-2 h-2 rounded-full ${
                  clawRoles.enabled ? "bg-green-400" : "bg-red-400"
                }`}
              />
              <span className="text-xs text-grim-text-dim">
                {clawRoles.enabled ? "Engine Online" : "Engine Offline"}
              </span>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {clawRoles.roles.map((role) => (
              <IronClawRoleCard
                key={role.id}
                role={role}
                online={clawRoles.enabled}
              />
            ))}
          </div>
          {clawRoles.coordination_patterns &&
            clawRoles.coordination_patterns.length > 0 && (
              <div className="flex items-center gap-2 mt-3">
                <span className="text-[9px] text-grim-text-dim uppercase tracking-wider">
                  Coordination
                </span>
                <div className="flex flex-wrap gap-1">
                  {clawRoles.coordination_patterns.map((p) => (
                    <span
                      key={p}
                      className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim"
                    >
                      {p}
                    </span>
                  ))}
                </div>
              </div>
            )}
        </section>
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

      </>}
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
          <div className="flex items-center gap-2 p-2">
            <GrimTypingSprite size="sm" />
            <span className="text-[10px] text-grim-text-dim animate-pulse">working...</span>
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

function RosterCard({
  agent,
  toggling,
  onToggle,
}: {
  agent: AgentRosterEntry;
  toggling: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`bg-grim-surface border rounded-xl p-3 transition-all ${
        agent.enabled
          ? "border-grim-border hover:border-grim-accent/40"
          : "border-grim-border/50 opacity-60"
      }`}
    >
      <div className="flex items-start gap-2">
        {/* Color dot */}
        <div
          className="w-2.5 h-2.5 rounded-full mt-1 flex-shrink-0"
          style={{ backgroundColor: agent.color }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-grim-text">
              {agent.name}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim">
              {agent.role}
            </span>
          </div>
          <p className="text-[10px] text-grim-text-dim mt-0.5 line-clamp-2">
            {agent.description}
          </p>
        </div>
        {agent.toggleable ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            disabled={toggling}
            className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
              agent.enabled ? "bg-grim-accent" : "bg-grim-border"
            } ${toggling ? "opacity-50" : "cursor-pointer"}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                agent.enabled ? "translate-x-4" : ""
              }`}
            />
          </button>
        ) : (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-success/15 text-grim-success flex-shrink-0">
            always on
          </span>
        )}
      </div>

      {/* Tools (collapsible) */}
      {agent.tools.length > 0 && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[9px] text-grim-text-dim hover:text-grim-text transition-colors"
          >
            {expanded ? "▾" : "▸"} {agent.tools.length} tools
          </button>
          {expanded && (
            <div className="flex flex-wrap gap-1 mt-1">
              {agent.tools.map((t) => (
                <span
                  key={t}
                  className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim font-mono"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function IronClawRoleCard({
  role,
  online,
}: {
  role: IronClawRole;
  online: boolean;
}) {
  return (
    <div
      className={`bg-grim-surface border rounded-xl p-3 transition-all ${
        online
          ? "border-grim-border hover:border-orange-400/40"
          : "border-grim-border/50 opacity-50"
      }`}
    >
      <div className="flex items-start gap-2">
        <div
          className="w-2.5 h-2.5 rounded-full mt-1 flex-shrink-0"
          style={{ backgroundColor: online ? role.color : "#6b7280" }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-grim-text">
              {role.name}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-orange-400/15 text-orange-400 font-mono">
              claw
            </span>
          </div>
          <p className="text-[10px] text-grim-text-dim mt-0.5 line-clamp-2">
            {role.description}
          </p>
        </div>
        {!online && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-400/15 text-red-400 flex-shrink-0">
            offline
          </span>
        )}
      </div>
      {role.capabilities.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {role.capabilities.map((c) => (
            <span
              key={c}
              className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim font-mono"
            >
              {c}
            </span>
          ))}
        </div>
      )}
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
