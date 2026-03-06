"use client";

import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { IconAgents } from "@/components/icons/NavIcons";
import { useGrimStore } from "@/store";
import { useActiveAgents } from "@/hooks/useActiveAgents";
import { usePoolStatus } from "@/hooks/usePoolStatus";
import { useJobDetail } from "@/hooks/useJobDetail";
import { PoolStatusBar } from "@/components/pages/agents/PoolStatusBar";
import { AgentDetailPanel } from "@/components/pages/agents/AgentDetailPanel";
import { JobKanban } from "./mission/JobKanban";
import { SubmitJobDialog } from "./mission/SubmitJobDialog";
import { StudioHeader } from "./studio/StudioHeader";
import { LiveTranscript } from "./studio/LiveTranscript";
import { DiffViewer } from "./studio/DiffViewer";
import { WorkspaceBrowser } from "./studio/WorkspaceBrowser";
import { AuditPanel } from "./studio/AuditPanel";

// Lazy-load Graph Studio (heavy — contains ForceGraph2D canvas)
const GraphStudio = lazy(() =>
  import("@/components/graph/GraphStudio").then((m) => ({
    default: m.GraphStudio,
  }))
);

const AGENT_TABS = [
  { id: "team", label: "Team" },
  { id: "jobs", label: "Jobs" },
  { id: "studio", label: "Studio" },
  { id: "graph", label: "Graph" },
] as const;

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
  const [showSubmit, setShowSubmit] = useState(false);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  const agentsTab = useGrimStore((s) => s.agentsTab);
  const setAgentsTab = useGrimStore((s) => s.setAgentsTab);
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
          <h2 className="text-lg font-semibold text-grim-text">Agents</h2>
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
            onClick={() => { setAgentsTab(tab.id); setSelectedAgent(null); }}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              agentsTab === tab.id
                ? "border-b-2 border-grim-accent text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Team tab */}
      {agentsTab === "team" && (
        <>
          <PoolStatusBar
            status={poolStatus}
            enabled={poolEnabled}
            activeJobs={totalRunning}
            queuedJobs={totalQueued}
          />

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
              {activeAgents.length > 0 && (
                <div className="flex items-center gap-2 text-xs text-grim-text-dim">
                  <div className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                  <span>{activeAgents.length} agent{activeAgents.length !== 1 ? "s" : ""} active in this conversation</span>
                </div>
              )}

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

      {/* Jobs tab */}
      {agentsTab === "jobs" && (
        <JobsTabContent
          poolEnabled={poolEnabled}
          showSubmit={showSubmit}
          setShowSubmit={setShowSubmit}
        />
      )}

      {/* Studio tab */}
      {agentsTab === "studio" && <StudioTabContent />}

      {/* Graph Studio tab */}
      {agentsTab === "graph" && (
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
    </div>
  );
}

// ── Jobs Tab Content ──

function JobsTabContent({
  poolEnabled,
  showSubmit,
  setShowSubmit,
}: {
  poolEnabled: boolean;
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
      <div className="flex justify-end">
        <button
          onClick={() => setShowSubmit(true)}
          className="text-[11px] px-3 py-1.5 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors"
        >
          Submit Job
        </button>
      </div>
      <JobKanban />
      <SubmitJobDialog open={showSubmit} onClose={() => setShowSubmit(false)} />
    </div>
  );
}

// ── Studio Tab Content ──

const STUDIO_TABS = [
  { id: "transcript", label: "Transcript" },
  { id: "diff", label: "Diff" },
  { id: "workspace", label: "Workspace" },
  { id: "audit", label: "Audit" },
] as const;

type StudioTabId = (typeof STUDIO_TABS)[number]["id"];

function StudioTabContent() {
  const [studioTab, setStudioTab] = useState<StudioTabId>("transcript");
  const selectedJobId = useGrimStore((s) => s.selectedJobId);
  const setAgentsTab = useGrimStore((s) => s.setAgentsTab);

  const { job, transcript, isLive, loading, diff, refetch } = useJobDetail(selectedJobId);

  if (!selectedJobId) {
    return (
      <div className="bg-grim-surface border border-grim-border rounded-lg p-6 text-center">
        <div className="text-[12px] text-grim-text-dim mb-3">
          No job selected. Pick one from the Jobs tab.
        </div>
        <button
          onClick={() => setAgentsTab("jobs")}
          className="text-[11px] px-3 py-1.5 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors"
        >
          View Jobs
        </button>
      </div>
    );
  }

  if (loading && !job) {
    return (
      <div className="text-center py-8">
        <div className="text-[11px] text-grim-text-dim">Loading job {selectedJobId}...</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="text-center py-8">
        <div className="text-[11px] text-red-400">Job not found: {selectedJobId}</div>
        <button
          onClick={() => setAgentsTab("jobs")}
          className="text-[11px] text-grim-text-dim hover:text-grim-text mt-2"
        >
          &larr; Back to Jobs
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <StudioHeader job={job} onRefetch={refetch} />

      {/* Studio sub-tabs */}
      <div className="flex gap-0 border-b border-grim-border">
        {STUDIO_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setStudioTab(tab.id)}
            className={`px-4 py-2 text-[11px] border-b-2 transition-colors ${
              studioTab === tab.id
                ? "border-grim-accent text-grim-accent"
                : "border-transparent text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
            {tab.id === "transcript" && isLive && (
              <span className="ml-1.5 w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse inline-block" />
            )}
          </button>
        ))}
      </div>

      <div>
        {studioTab === "transcript" && (
          <LiveTranscript jobId={job.id} transcript={transcript} isLive={isLive} />
        )}
        {studioTab === "diff" && <DiffViewer diff={diff} />}
        {studioTab === "workspace" && <WorkspaceBrowser workspaceId={job.workspace_id} />}
        {studioTab === "audit" && <AuditPanel transcript={transcript} jobType={job.job_type} />}
      </div>

      {job.error && (
        <div className="bg-red-400/5 border border-red-400/20 rounded-md p-3">
          <div className="text-[10px] text-red-400 uppercase tracking-wider mb-1">Error</div>
          <div className="text-[11px] text-red-300 font-mono whitespace-pre-wrap">{job.error}</div>
        </div>
      )}

      {job.result && (
        <div className="bg-grim-surface border border-grim-border rounded-md p-3">
          <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-1">Result</div>
          <div className="text-[11px] text-grim-text whitespace-pre-wrap">{job.result}</div>
        </div>
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

          {jobCounts && jobCounts.total > 0 && jobCounts.running === 0 && jobCounts.queued === 0 && (
            <div className="mt-1.5">
              <span className="text-[9px] text-grim-text-dim font-mono">
                {jobCounts.total} job{jobCounts.total !== 1 ? "s" : ""} total
              </span>
            </div>
          )}
        </div>

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
