"use client";

import { useState, useEffect } from "react";
import { IconDashboard } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useBridgeApi } from "@/hooks/useBridgeApi";
import { useActiveAgents } from "@/hooks/useActiveAgents";
import { useGrimMemory } from "@/hooks/useGrimMemory";
import { useSkills } from "@/hooks/useSkills";
import { useModels } from "@/hooks/useModels";
import { useGrimConfig } from "@/hooks/useGrimConfig";
import { formatCount } from "@/lib/format";
import { useGrimStore } from "@/store";
import { KnowledgeGraph, DOMAIN_COLORS } from "@/components/ui/KnowledgeGraph";
import type { GraphData, VaultStats } from "@/hooks/useVaultExplorer";
import { GrimTypingSprite } from "@/components/GrimTypingSprite";
import { MetricsBar } from "./mission/MetricsBar";
import { SlotGrid } from "./mission/SlotGrid";
import { JobKanban } from "./mission/JobKanban";
import { SubmitJobDialog } from "./mission/SubmitJobDialog";
import { useDaemonStatus } from "@/hooks/useDaemonStatus";
import { EscalationsPanel } from "./daemon/EscalationsPanel";

// ---------------------------------------------------------------------------
// Trace category colors (matches TraceEntry.tsx)
// ---------------------------------------------------------------------------

const catColors: Record<string, string> = {
  node: "text-trace-node",
  llm: "text-trace-llm",
  tool: "text-trace-tool",
  graph: "text-trace-graph",
};

// ---------------------------------------------------------------------------
// Nav link helper
// ---------------------------------------------------------------------------

function ViewAllLink({ page }: { page: string }) {
  const setActivePage = useGrimStore((s) => s.setActivePage);
  return (
    <button
      onClick={() => setActivePage(page)}
      className="text-[10px] text-grim-accent hover:underline"
    >
      view all
    </button>
  );
}

function StatusDot({ ok, neutral }: { ok: boolean; neutral?: boolean }) {
  return (
    <div
      className={`w-2 h-2 rounded-full ${
        neutral ? "bg-grim-border" : ok ? "bg-green-400" : "bg-red-400"
      }`}
    />
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const DASHBOARD_TABS = [
  { id: "overview", label: "Overview" },
  { id: "pool", label: "Pool" },
  { id: "daemon", label: "Daemon" },
] as const;

export function DashboardHome() {
  const dashboardTab = useGrimStore((s) => s.dashboardTab);
  const setDashboardTab = useGrimStore((s) => s.setDashboardTab);
  const poolEnabled = useGrimStore((s) => s.poolEnabled);
  const poolRunning = useGrimStore((s) => s.poolStatus?.running ?? false);
  const [showSubmit, setShowSubmit] = useState(false);
  const { status: daemonStatus, daemonEnabled } = useDaemonStatus();
  const daemonRunning = daemonStatus?.running ?? false;

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconDashboard size={32} className="text-grim-accent" />
        <div>
          <h2 className="text-lg font-semibold text-grim-text">
            Dashboard
          </h2>
          <p className="text-xs text-grim-text-dim">
            Overview of active systems
          </p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-grim-border">
        {DASHBOARD_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setDashboardTab(tab.id)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              dashboardTab === tab.id
                ? "border-b-2 border-grim-accent text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
            {tab.id === "pool" && (
              <span className="ml-1.5">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${poolEnabled && poolRunning ? "bg-green-400" : "bg-grim-border"}`} />
              </span>
            )}
            {tab.id === "daemon" && (
              <span className="ml-1.5">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${daemonEnabled && daemonRunning ? "bg-green-400" : "bg-grim-border"}`} />
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {dashboardTab === "overview" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <HealthWidgetTile />
          <PoolWidgetTile />
          <DaemonWidgetTile />
          <MemoryWidgetTile />
          <ActiveAgentsTile />
          <TokenUsageTile />
          <SkillsWidgetTile />
          <ModelsWidgetTile />
          <SettingsWidgetTile />
          <VaultWidgetTile />
        </div>
      )}

      {/* Pool tab */}
      {dashboardTab === "pool" && (
        <PoolTabContent
          poolEnabled={poolEnabled}
          poolRunning={poolRunning}
          showSubmit={showSubmit}
          setShowSubmit={setShowSubmit}
        />
      )}

      {/* Daemon tab */}
      {dashboardTab === "daemon" && <DaemonTabContent />}
    </div>
  );
}

function PoolTabContent({
  poolEnabled,
  poolRunning,
  showSubmit,
  setShowSubmit,
}: {
  poolEnabled: boolean;
  poolRunning: boolean;
  showSubmit: boolean;
  setShowSubmit: (v: boolean) => void;
}) {
  if (!poolEnabled) {
    return (
      <div className="bg-grim-surface border border-grim-border rounded-lg p-6 text-center">
        <div className="text-grim-text-dim text-[12px] mb-2">Pool Offline</div>
        <div className="text-[11px] text-grim-text-dim">
          Enable the execution pool in <span className="font-mono text-grim-accent">grim.yaml</span> with{" "}
          <span className="font-mono text-grim-accent">pool.enabled: true</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${poolRunning ? "bg-green-400 animate-pulse" : "bg-red-400"}`} />
          <span className="text-[10px] text-grim-text-dim">
            {poolRunning ? "Pool Active" : "Pool Stopped"}
          </span>
        </div>
        <button
          onClick={() => setShowSubmit(true)}
          className="text-[11px] px-3 py-1.5 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors"
        >
          Submit Job
        </button>
      </div>
      <MetricsBar />
      <SlotGrid />
      <JobKanban />
      <SubmitJobDialog open={showSubmit} onClose={() => setShowSubmit(false)} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pool Widget Tile
// ---------------------------------------------------------------------------

function PoolWidgetTile() {
  const poolStatus = useGrimStore((s) => s.poolStatus);
  const poolEnabled = useGrimStore((s) => s.poolEnabled);
  const poolMetrics = useGrimStore((s) => s.poolMetrics);
  const poolJobs = useGrimStore((s) => s.poolJobs);
  const setDashboardTab = useGrimStore((s) => s.setDashboardTab);
  const navigateToJob = useGrimStore((s) => s.navigateToJob);

  const running = poolStatus?.running ?? false;
  const busySlots = poolStatus?.slots.filter((s) => s.busy).length ?? 0;
  const totalSlots = poolStatus?.slots.length ?? 0;
  const activeJobs = poolJobs.filter((j) => j.status === "running").length;
  const queuedJobs = poolJobs.filter((j) => j.status === "queued").length;

  return (
    <DashboardTile
      title="Execution Pool"
      headerRight={
        <div className="flex items-center gap-2">
          <StatusDot ok={poolEnabled && running} neutral={!poolEnabled} />
          <button
            onClick={() => setDashboardTab("pool")}
            className="text-[10px] text-grim-accent hover:underline"
          >
            view all
          </button>
        </div>
      }
    >
      {!poolEnabled ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Pool offline
        </div>
      ) : (
        <div className="space-y-2">
          {/* Slot utilization */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-grim-text-dim w-16">Slots</span>
            <div className="flex gap-1">
              {(poolStatus?.slots ?? []).map((slot) => (
                <button
                  key={slot.slot_id}
                  onClick={() => slot.current_job_id ? navigateToJob(slot.current_job_id) : undefined}
                  className={`w-3 h-3 rounded-sm ${
                    slot.busy
                      ? "bg-green-400 animate-pulse cursor-pointer"
                      : "bg-grim-border cursor-default"
                  }`}
                  title={slot.busy ? `${slot.slot_id}: ${slot.current_job_id}` : `${slot.slot_id}: idle`}
                />
              ))}
            </div>
            <span className="text-grim-text font-mono ml-auto">{busySlots}/{totalSlots}</span>
          </div>

          {/* Active/queued counts */}
          <div className="grid grid-cols-3 gap-2">
            <MiniStat label="Active" value={activeJobs} />
            <MiniStat label="Queued" value={queuedJobs} />
            <MiniStat label="Done" value={poolMetrics?.completed_count ?? 0} />
          </div>

          {/* Recent running jobs */}
          {poolJobs
            .filter((j) => j.status === "running")
            .slice(0, 3)
            .map((job) => (
              <button
                key={job.id}
                onClick={() => navigateToJob(job.id)}
                className="flex items-center gap-2 text-[10px] w-full text-left hover:bg-grim-surface/50 rounded px-1 py-0.5 transition-colors"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse shrink-0" />
                <span className="text-grim-text font-mono truncate">{job.id}</span>
                <span className="text-grim-text-dim ml-auto">{job.job_type}</span>
              </button>
            ))}

          {/* Open Pool tab */}
          <button
            onClick={() => setDashboardTab("pool")}
            className="text-[10px] text-grim-accent hover:underline w-full text-center pt-1"
          >
            Open Pool
          </button>
        </div>
      )}
    </DashboardTile>
  );
}

// ---------------------------------------------------------------------------
// Health Widget Tile
// ---------------------------------------------------------------------------

interface HealthData {
  status: string;
  env: string;
  vault: string | null;
  graph: boolean;
}

function HealthWidgetTile() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [error, setError] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  useEffect(() => {
    async function fetchHealth() {
      try {
        const resp = await fetch(`${apiBase}/health`);
        if (resp.ok) {
          setHealth(await resp.json());
          setError(false);
        } else {
          setError(true);
        }
      } catch {
        setError(true);
      }
    }
    fetchHealth();
    const interval = setInterval(fetchHealth, 30000);
    return () => clearInterval(interval);
  }, [apiBase]);

  const ok = health?.status === "ok";

  return (
    <DashboardTile
      title="System Health"
      headerRight={<StatusDot ok={ok && !error} />}
    >
      {error && (
        <div className="text-xs text-red-400 py-4 text-center">
          Server unreachable
        </div>
      )}
      {health && !error && (
        <div className="space-y-1.5">
          <HealthRow label="Server" ok={ok} detail={health.env} />
          <HealthRow label="Graph" ok={health.graph} detail={health.graph ? "ready" : "not loaded"} />
          <HealthRow label="Vault" ok={!!health.vault} detail={health.vault || "not set"} />
        </div>
      )}
    </DashboardTile>
  );
}

function HealthRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <StatusDot ok={ok} />
      <span className="text-grim-text-dim w-16">{label}</span>
      <span className="text-grim-text truncate">{detail}</span>
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
        <div className="flex items-center gap-2">
          {summary && (
            <span className="text-xs text-grim-accent font-semibold">
              {formatCount(summary.totals.total_tokens)}
            </span>
          )}
          <ViewAllLink page="tokens" />
        </div>
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
          <ViewAllLink page="agents" />
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
                    <div className="flex items-center gap-2 py-1 px-1">
                      <GrimTypingSprite size="xs" />
                      <span className="text-[9px] text-grim-text-dim">working...</span>
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
      headerRight={<ViewAllLink page="memory" />}
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

// ---------------------------------------------------------------------------
// Skills Widget Tile
// ---------------------------------------------------------------------------

function SkillsWidgetTile() {
  const { skills, loading } = useSkills();
  const enabled = skills.filter((s) => s.enabled);

  return (
    <DashboardTile
      title="Skills"
      headerRight={
        <div className="flex items-center gap-2">
          <span className="text-xs text-grim-text-dim">
            {enabled.length}/{skills.length}
          </span>
          <ViewAllLink page="skills" />
        </div>
      }
    >
      {loading ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      ) : skills.length === 0 ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          No skills loaded
        </div>
      ) : (
        <div className="space-y-1">
          {skills.slice(0, 8).map((skill) => (
            <div key={skill.name} className="flex items-center gap-2 text-[11px]">
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  skill.enabled ? "bg-green-400" : "bg-grim-border"
                }`}
              />
              <span className="text-grim-text truncate">{skill.name}</span>
              {skill.version && (
                <span className="text-[9px] text-grim-text-dim ml-auto font-mono">
                  v{skill.version}
                </span>
              )}
            </div>
          ))}
          {skills.length > 8 && (
            <div className="text-[10px] text-grim-text-dim text-center pt-1">
              +{skills.length - 8} more
            </div>
          )}
        </div>
      )}
    </DashboardTile>
  );
}

// ---------------------------------------------------------------------------
// Models Widget Tile
// ---------------------------------------------------------------------------

function ModelsWidgetTile() {
  const { models, routing, loading } = useModels();

  return (
    <DashboardTile
      title="Models"
      headerRight={
        <div className="flex items-center gap-2">
          <span className="text-xs text-grim-text-dim">Anthropic</span>
          <ViewAllLink page="models" />
        </div>
      }
    >
      {loading ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      ) : (
        <div className="space-y-2">
          {models.map((m) => (
            <div key={m.tier} className="flex items-center gap-2 text-[11px]">
              <div
                className={`w-2 h-2 rounded-full ${
                  m.enabled ? "bg-green-400" : "bg-grim-border"
                }`}
              />
              <span className="text-grim-text">{m.name}</span>
              {m.is_default && (
                <span className="text-[9px] px-1 py-0.5 rounded bg-grim-accent/15 text-grim-accent ml-auto">
                  default
                </span>
              )}
            </div>
          ))}
          {routing && (
            <div className="flex items-center gap-2 pt-1 border-t border-grim-border/30 text-[10px] text-grim-text-dim">
              <span>Routing</span>
              <span
                className={`px-1 py-0.5 rounded text-[9px] font-medium ${
                  routing.enabled
                    ? "bg-grim-success/15 text-grim-success"
                    : "bg-grim-border/30 text-grim-text-dim"
                }`}
              >
                {routing.enabled ? "on" : "off"}
              </span>
              <span className="ml-auto font-mono">{routing.default_tier}</span>
            </div>
          )}
        </div>
      )}
    </DashboardTile>
  );
}

// ---------------------------------------------------------------------------
// Settings Widget Tile
// ---------------------------------------------------------------------------

function SettingsWidgetTile() {
  const { config, loading } = useGrimConfig();

  return (
    <DashboardTile
      title="Settings"
      headerRight={<ViewAllLink page="settings" />}
    >
      {loading || !config ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      ) : (
        <div className="space-y-1.5">
          <SettingsRow label="Env" value={config.env} />
          <SettingsRow
            label="Model"
            value={config.model.replace("claude-", "")}
          />
          <SettingsRow label="Temp" value={`${config.temperature}`} />
          <SettingsRow
            label="Tokens"
            value={`${(config.max_tokens / 1000).toFixed(0)}K`}
          />
          <SettingsRow
            label="Vault"
            value={config.vault_path.split("/").pop() || config.vault_path}
          />
        </div>
      )}
    </DashboardTile>
  );
}

// ---------------------------------------------------------------------------
// Vault Knowledge Graph Widget Tile
// ---------------------------------------------------------------------------

function VaultWidgetTile() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [stats, setStats] = useState<VaultStats | null>(null);
  const [loading, setLoading] = useState(true);
  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";
  const setActivePage = useGrimStore((s) => s.setActivePage);

  useEffect(() => {
    async function fetchData() {
      try {
        const [graphRes, statsRes] = await Promise.all([
          fetch(`${apiBase}/api/vault/graph`),
          fetch(`${apiBase}/api/vault/stats`),
        ]);
        if (graphRes.ok) {
          const g = await graphRes.json();
          if (!g.error) setGraphData(g);
        }
        if (statsRes.ok) {
          const s = await statsRes.json();
          if (!s.error) setStats(s);
        }
      } catch {
        // Vault widget is optional — don't error the dashboard
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [apiBase]);

  // Top 6 domains by count
  const topDomains = stats?.domains
    ? Object.entries(stats.domains)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
    : [];

  return (
    <DashboardTile
      title="Knowledge Graph"
      headerRight={
        <div className="flex items-center gap-2">
          {stats && (
            <span className="text-xs text-grim-accent font-semibold">
              {stats.total_fdos} FDOs
            </span>
          )}
          <ViewAllLink page="vault" />
        </div>
      }
    >
      {loading && (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Loading...
        </div>
      )}
      {!loading && !graphData && (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Vault unavailable
        </div>
      )}
      {!loading && graphData && (
        <div className="space-y-2">
          {/* Mini graph */}
          <button
            onClick={() => setActivePage("vault")}
            className="block w-full rounded overflow-hidden hover:ring-1 hover:ring-grim-accent/30 transition-all"
          >
            <KnowledgeGraph
              data={graphData}
              width={320}
              height={160}
              mini
            />
          </button>

          {/* Domain legend */}
          {topDomains.length > 0 && (
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {topDomains.map(([domain, count]) => (
                <div key={domain} className="flex items-center gap-1 text-[10px]">
                  <div
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: DOMAIN_COLORS[domain] || "#8888a0" }}
                  />
                  <span className="text-grim-text-dim">{domain}</span>
                  <span className="text-grim-text-dim/60">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </DashboardTile>
  );
}

function SettingsRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-grim-text-dim w-12">{label}</span>
      <span className="text-grim-text font-mono truncate">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daemon Tab Content
// ---------------------------------------------------------------------------

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return "—";
  const ms = Date.now() - new Date(isoStr).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${Math.floor(ms / 3_600_000)}h ago`;
}

const PIPELINE_DOT_COLORS: Record<string, string> = {
  backlog: "bg-gray-400",
  ready: "bg-blue-400",
  dispatched: "bg-yellow-400",
  review: "bg-purple-400",
  merged: "bg-green-400",
  failed: "bg-red-400",
  blocked: "bg-orange-400",
};

function DaemonTabContent() {
  const {
    status, pipeline, escalations, daemonEnabled,
    approveItem, rejectItem, retryItem, resolveEscalation,
  } = useDaemonStatus();

  if (!daemonEnabled) {
    return (
      <div className="bg-grim-surface border border-grim-border rounded-lg p-6 text-center">
        <div className="text-grim-text-dim text-[12px] mb-2">Daemon Offline</div>
        <div className="text-[11px] text-grim-text-dim">
          Enable the management daemon in <span className="font-mono text-grim-accent">grim.yaml</span> with{" "}
          <span className="font-mono text-grim-accent">daemon.enabled: true</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Health bar */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${status?.running ? "bg-green-400 animate-pulse" : "bg-red-400"}`} />
          <span className="text-[10px] text-grim-text-dim">
            {status?.running ? "Daemon Active" : "Daemon Stopped"}
          </span>
        </div>
        {status && (
          <>
            <MiniStat label="Uptime" value={formatUptime(status.uptime_seconds)} />
            <MiniStat label="Scans" value={status.scan_count} />
            <MiniStat label="Dispatches" value={status.dispatch_count} />
            <div className="text-[10px] text-grim-text-dim">
              Last scan: {formatRelativeTime(status.last_scan_at)}
            </div>
          </>
        )}
      </div>

      {/* Pipeline summary dots */}
      {status?.pipeline && (
        <div className="flex items-center gap-3 flex-wrap">
          {Object.entries(status.pipeline).map(([key, count]) => (
            <div key={key} className="flex items-center gap-1 text-[10px]">
              <div className={`w-2 h-2 rounded-full ${PIPELINE_DOT_COLORS[key] ?? "bg-gray-400"}`} />
              <span className="text-grim-text-dim capitalize">{key}</span>
              <span className="text-grim-text font-mono">{count}</span>
            </div>
          ))}
        </div>
      )}

      {/* Unified kanban with pipeline data */}
      <JobKanban
        pipelineItems={pipeline}
        onApprove={approveItem}
        onReject={rejectItem}
      />

      {/* Escalations */}
      {escalations.length > 0 && (
        <EscalationsPanel
          escalations={escalations}
          onResolve={resolveEscalation}
          onRetry={retryItem}
        />
      )}

      {/* Recent errors */}
      {status?.recent_errors && status.recent_errors.length > 0 && (
        <DashboardTile title="Recent Errors">
          <div className="max-h-40 overflow-y-auto space-y-1">
            {status.recent_errors.map((err, i) => (
              <div key={i} className="text-[10px] text-red-300 font-mono truncate">
                {err}
              </div>
            ))}
          </div>
        </DashboardTile>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daemon Widget Tile (Dashboard Overview)
// ---------------------------------------------------------------------------

function DaemonWidgetTile() {
  const { status, escalations, daemonEnabled } = useDaemonStatus();
  const setDashboardTab = useGrimStore((s) => s.setDashboardTab);
  const running = status?.running ?? false;
  const escCount = escalations.length;

  return (
    <DashboardTile
      title="Management Daemon"
      headerRight={
        <div className="flex items-center gap-2">
          <StatusDot ok={daemonEnabled && running} neutral={!daemonEnabled} />
          <button
            onClick={() => setDashboardTab("daemon")}
            className="text-[10px] text-grim-accent hover:underline"
          >
            view all
          </button>
        </div>
      }
    >
      {!daemonEnabled ? (
        <div className="text-xs text-grim-text-dim py-4 text-center">
          Daemon offline
        </div>
      ) : (
        <div className="space-y-2">
          {/* Pipeline counts */}
          {status?.pipeline && (
            <div className="grid grid-cols-4 gap-1">
              {Object.entries(status.pipeline)
                .filter(([, count]) => count > 0)
                .map(([key, count]) => (
                  <div key={key} className="flex items-center gap-1 text-[10px]">
                    <div className={`w-1.5 h-1.5 rounded-full ${PIPELINE_DOT_COLORS[key] ?? "bg-gray-400"}`} />
                    <span className="text-grim-text-dim capitalize">{key}</span>
                    <span className="text-grim-text font-mono">{count}</span>
                  </div>
                ))}
            </div>
          )}

          {/* Metrics row */}
          {status && (
            <div className="grid grid-cols-3 gap-2">
              <MiniStat label="Scans" value={status.scan_count} />
              <MiniStat label="Dispatches" value={status.dispatch_count} />
              <MiniStat label="Uptime" value={formatUptime(status.uptime_seconds)} />
            </div>
          )}

          {/* Escalation alert */}
          {escCount > 0 && (
            <button
              onClick={() => setDashboardTab("daemon")}
              className="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded bg-orange-400/10 border border-orange-400/20 hover:bg-orange-400/15 transition-colors"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />
              <span className="text-[10px] text-orange-400">
                {escCount} escalation{escCount !== 1 ? "s" : ""} need attention
              </span>
            </button>
          )}

          {/* Open link */}
          <button
            onClick={() => setDashboardTab("daemon")}
            className="text-[10px] text-grim-accent hover:underline w-full text-center pt-1"
          >
            Open Daemon
          </button>
        </div>
      )}
    </DashboardTile>
  );
}
