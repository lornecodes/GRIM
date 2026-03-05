"use client";

import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { IconAgents } from "@/components/icons/NavIcons";
import { useGrimStore } from "@/store";
import { useActiveAgents } from "@/hooks/useActiveAgents";
import { usePoolStatus, type JobsByType } from "@/hooks/usePoolStatus";
import { PoolStatusBar } from "@/components/pages/agents/PoolStatusBar";
import { AgentDetailPanel } from "@/components/pages/agents/AgentDetailPanel";

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

// ── Agent ID → Pool JobType mapping ──

const AGENT_JOB_TYPE: Record<string, string> = {
  code: "code",
  coder: "code",
  research: "research",
  audit: "audit",
};

// ── Types ──

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

// ── Component ──

export function AgentTeam() {
  const [roster, setRoster] = useState<AgentRosterEntry[]>([]);
  const [togglingAgent, setTogglingAgent] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  const [activeTab, setActiveTab] = useState<AgentTabId>("team");
  const activeAgents = useActiveAgents(10);
  const { poolStatus, poolEnabled, jobs, jobsByType, fetchJobsByType } = usePoolStatus();

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
    fetchRoster();
  }, [fetchRoster]);

  // Compute aggregate pool numbers
  const totalRunning = jobs.filter((j) => j.status === "running" || j.status === "assigned").length;
  const totalQueued = jobs.filter((j) => j.status === "queued").length;

  // Find the selected agent's data
  const selectedRosterAgent = roster.find((a) => a.id === selectedAgent) ?? null;
  const selectedActiveAgent = activeAgents.find((a) => a.node === selectedAgent) ?? null;

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconAgents size={32} className="text-grim-accent" />
        <div>
          <h2 className="text-lg font-semibold text-grim-text">Agent Team</h2>
          <p className="text-xs text-grim-text-dim">
            {roster.length} agents registered
            {poolEnabled && poolStatus?.running && (
              <> &middot; Pool active</>
            )}
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
            onClick={() => { setActiveTab(tab.id); setSelectedAgent(null); }}
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

      {/* Team tab */}
      {activeTab === "team" && (
        <>
          {/* Pool Status Bar */}
          <PoolStatusBar
            status={poolStatus}
            enabled={poolEnabled}
            activeJobs={totalRunning}
            queuedJobs={totalQueued}
          />

          {/* Agent Detail Panel (when an agent is selected) */}
          {selectedRosterAgent ? (
            <AgentDetailPanel
              agent={selectedRosterAgent}
              activeAgent={selectedActiveAgent}
              onBack={() => setSelectedAgent(null)}
              fetchJobsByType={fetchJobsByType}
              poolEnabled={poolEnabled}
            />
          ) : (
            <>
              {/* Active agents indicator */}
              {activeAgents.length > 0 && (
                <div className="flex items-center gap-2 text-xs text-grim-text-dim">
                  <div className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                  <span>{activeAgents.length} agent{activeAgents.length !== 1 ? "s" : ""} active in this conversation</span>
                </div>
              )}

              {/* Agent Roster */}
              {roster.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {roster.map((agent) => {
                    const isActive = activeAgents.some((a) => a.node === agent.id);
                    const jt = AGENT_JOB_TYPE[agent.id];
                    const jobCounts = jt ? jobsByType[jt] : undefined;

                    return (
                      <RosterCard
                        key={agent.id}
                        agent={agent}
                        active={isActive}
                        jobCounts={jobCounts}
                        toggling={togglingAgent === agent.id}
                        onToggle={() => toggleAgent(agent.id)}
                        onSelect={() => setSelectedAgent(agent.id)}
                      />
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-12 text-sm text-grim-text-dim">
                  No agents loaded. Check the GRIM server connection.
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

// ── Roster Card ──

function RosterCard({
  agent,
  active,
  jobCounts,
  toggling,
  onToggle,
  onSelect,
}: {
  agent: AgentRosterEntry;
  active: boolean;
  jobCounts?: { running: number; queued: number; total: number };
  toggling: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      onClick={onSelect}
      className={`bg-grim-surface border rounded-xl p-3 transition-all cursor-pointer ${
        agent.enabled
          ? "border-grim-border hover:border-grim-accent/40"
          : "border-grim-border/50 opacity-60"
      } ${active ? "ring-1 ring-green-400/30" : ""}`}
    >
      <div className="flex items-start gap-2">
        {/* Color dot + active pulse */}
        <div className="relative mt-1 flex-shrink-0">
          <div
            className="w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: agent.color }}
          />
          {active && (
            <div className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          )}
        </div>

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

          {/* Pool job badges */}
          {jobCounts && (jobCounts.running > 0 || jobCounts.queued > 0) && (
            <div className="flex gap-1.5 mt-1.5">
              {jobCounts.running > 0 && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-400/15 text-green-400 font-mono">
                  {jobCounts.running} running
                </span>
              )}
              {jobCounts.queued > 0 && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-400/15 text-yellow-400 font-mono">
                  {jobCounts.queued} queued
                </span>
              )}
            </div>
          )}

          {/* Total completed jobs indicator */}
          {jobCounts && jobCounts.total > 0 && jobCounts.running === 0 && jobCounts.queued === 0 && (
            <div className="mt-1.5">
              <span className="text-[9px] text-grim-text-dim font-mono">
                {jobCounts.total} job{jobCounts.total !== 1 ? "s" : ""} total
              </span>
            </div>
          )}
        </div>

        {/* Toggle or always-on badge */}
        <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          {agent.toggleable ? (
            <button
              onClick={onToggle}
              disabled={toggling}
              className={`relative w-9 h-5 rounded-full transition-colors ${
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
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-success/15 text-grim-success">
              always on
            </span>
          )}
        </div>
      </div>

      {/* Tools (collapsible) */}
      {agent.tools.length > 0 && (
        <div className="mt-2">
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
            className="text-[9px] text-grim-text-dim hover:text-grim-text transition-colors"
          >
            {expanded ? "v" : ">"} {agent.tools.length} tools
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
