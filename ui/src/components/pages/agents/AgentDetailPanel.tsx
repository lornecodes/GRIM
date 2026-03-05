"use client";

import { useState, useEffect, useCallback } from "react";
import type { TraceEvent } from "@/lib/types";
import type { ActiveAgent } from "@/hooks/useActiveAgents";
import type { PoolJob } from "@/hooks/usePoolStatus";
import { GrimTypingSprite } from "@/components/GrimTypingSprite";

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

interface AgentDetailPanelProps {
  agent: AgentRosterEntry;
  activeAgent: ActiveAgent | null;
  onBack: () => void;
  fetchJobsByType: (jobType: string) => Promise<PoolJob[]>;
  poolEnabled: boolean;
}

const DETAIL_TABS = [
  { id: "activity", label: "Activity" },
  { id: "jobs", label: "Jobs" },
] as const;

type DetailTabId = (typeof DETAIL_TABS)[number]["id"];

// ── Trace category colors ──

const catColors: Record<string, string> = {
  node: "text-trace-node",
  llm: "text-trace-llm",
  tool: "text-trace-tool",
  claw: "text-orange-400",
  graph: "text-trace-graph",
};

// ── Status badge colors ──

const statusColors: Record<string, string> = {
  queued: "bg-yellow-400/15 text-yellow-400",
  assigned: "bg-blue-400/15 text-blue-400",
  running: "bg-green-400/15 text-green-400",
  blocked: "bg-orange-400/15 text-orange-400",
  review: "bg-purple-400/15 text-purple-400",
  complete: "bg-green-400/15 text-green-400",
  failed: "bg-red-400/15 text-red-400",
  cancelled: "bg-grim-border/30 text-grim-text-dim",
};

// ── Component ──

export function AgentDetailPanel({
  agent,
  activeAgent,
  onBack,
  fetchJobsByType,
  poolEnabled,
}: AgentDetailPanelProps) {
  const [tab, setTab] = useState<DetailTabId>("activity");
  const [jobs, setJobs] = useState<PoolJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);

  const jobType = AGENT_JOB_TYPE[agent.id];

  const loadJobs = useCallback(async () => {
    if (!jobType || !poolEnabled) return;
    setLoadingJobs(true);
    const data = await fetchJobsByType(jobType);
    setJobs(data);
    setLoadingJobs(false);
  }, [jobType, poolEnabled, fetchJobsByType]);

  // Load jobs when switching to Jobs tab
  useEffect(() => {
    if (tab === "jobs") loadJobs();
  }, [tab, loadJobs]);

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="text-xs text-grim-text-dim hover:text-grim-text transition-colors"
        >
          &larr; Back
        </button>
        <div
          className="w-3 h-3 rounded-full flex-shrink-0"
          style={{ backgroundColor: agent.color }}
        />
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-grim-text">{agent.name}</span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim">
              {agent.role}
            </span>
          </div>
          <p className="text-[10px] text-grim-text-dim mt-0.5">{agent.description}</p>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-grim-border">
        {DETAIL_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === t.id
                ? "border-b-2 border-grim-accent text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {t.label}
            {t.id === "jobs" && jobType && jobs.length > 0 && (
              <span className="ml-1 text-grim-text-dim">({jobs.length})</span>
            )}
          </button>
        ))}
      </div>

      {/* Activity tab */}
      {tab === "activity" && (
        <ActivityView agent={activeAgent} />
      )}

      {/* Jobs tab */}
      {tab === "jobs" && (
        <JobsView
          jobType={jobType}
          jobs={jobs}
          loading={loadingJobs}
          poolEnabled={poolEnabled}
          expandedJob={expandedJob}
          onToggleExpand={(id) => setExpandedJob(expandedJob === id ? null : id)}
          onRefresh={loadJobs}
        />
      )}
    </div>
  );
}

// ── Activity sub-view ──

function ActivityView({ agent }: { agent: ActiveAgent | null }) {
  if (!agent || agent.traces.length === 0) {
    return (
      <div className="text-center py-8 text-xs text-grim-text-dim">
        No activity in the current conversation. Start chatting to see this agent work.
      </div>
    );
  }

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
      <div className="max-h-[500px] overflow-y-auto p-2 space-y-0.5">
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
                    ? t.output_preview.slice(0, 300) + "\n..."
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
                ? agentStdout.slice(0, 600) + "\n..."
                : agentStdout}
            </div>
          </div>
        )}

        {/* Trace log lines */}
        {agent.traces
          .filter((t) => t.cat !== "tool" && !t.step_content)
          .map((trace, i) => (
            <div key={`trace-${i}`} className="text-[10.5px] leading-relaxed truncate">
              <span className="text-grim-text-dim tabular-nums mr-1.5">
                [{trace.ms}ms]
              </span>
              <span className={`mr-1 ${catColors[trace.cat] || "text-grim-text-dim"}`}>
                {trace.text}
              </span>
            </div>
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

// ── Jobs sub-view ──

interface JobsViewProps {
  jobType: string | undefined;
  jobs: PoolJob[];
  loading: boolean;
  poolEnabled: boolean;
  expandedJob: string | null;
  onToggleExpand: (id: string) => void;
  onRefresh: () => void;
}

function JobsView({ jobType, jobs, loading, poolEnabled, expandedJob, onToggleExpand, onRefresh }: JobsViewProps) {
  if (!poolEnabled) {
    return (
      <div className="text-center py-8 text-xs text-grim-text-dim">
        Execution pool is offline. Enable it in <code className="text-grim-accent">grim.yaml</code> to see pool jobs.
      </div>
    );
  }

  if (!jobType) {
    return (
      <div className="text-center py-8 text-xs text-grim-text-dim">
        This agent doesn&apos;t execute pool jobs.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="text-center py-8 text-xs text-grim-text-dim animate-pulse">
        Loading jobs...
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="text-center py-8 text-xs text-grim-text-dim">
        No pool jobs yet for <code className="text-grim-accent">{jobType}</code>.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-grim-text-dim uppercase tracking-wider">
          {jobs.length} job{jobs.length !== 1 ? "s" : ""}
        </span>
        <button
          onClick={onRefresh}
          className="text-[10px] text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
      </div>

      {jobs.map((job) => (
        <JobCard
          key={job.id}
          job={job}
          expanded={expandedJob === job.id}
          onToggle={() => onToggleExpand(job.id)}
        />
      ))}
    </div>
  );
}

// ── Job card ──

function JobCard({ job, expanded, onToggle }: { job: PoolJob; expanded: boolean; onToggle: () => void }) {
  const elapsed = getElapsed(job.created_at, job.updated_at);
  const statusClass = statusColors[job.status] ?? "bg-grim-border/30 text-grim-text-dim";

  return (
    <div className="bg-grim-surface border border-grim-border rounded-lg overflow-hidden">
      {/* Summary row */}
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-grim-bg/50 transition-colors"
      >
        <span className="text-[10px] text-grim-text-dim font-mono flex-shrink-0">
          {expanded ? "v" : ">"}
        </span>
        <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono flex-shrink-0 ${statusClass}`}>
          {job.status}
        </span>
        <span className="text-[10px] text-grim-text truncate flex-1">
          {job.instructions.slice(0, 100)}
          {job.instructions.length > 100 ? "..." : ""}
        </span>
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim flex-shrink-0">
          {job.priority}
        </span>
        <span className="text-[10px] text-grim-text-dim tabular-nums flex-shrink-0">
          {elapsed}
        </span>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-grim-border px-3 py-2 space-y-2">
          {/* Meta row */}
          <div className="flex flex-wrap gap-2 text-[10px] text-grim-text-dim font-mono">
            <span>id: {job.id}</span>
            {job.assigned_slot && <span>slot: {job.assigned_slot}</span>}
            {job.retry_count > 0 && <span>retries: {job.retry_count}</span>}
            {job.workspace_id && <span>workspace: {job.workspace_id}</span>}
          </div>

          {/* Instructions */}
          <div>
            <div className="text-[9px] text-grim-text-dim uppercase tracking-wider mb-0.5">
              Instructions
            </div>
            <div className="text-[11px] text-grim-text whitespace-pre-wrap break-words font-mono bg-grim-bg rounded p-2 max-h-40 overflow-y-auto">
              {job.instructions}
            </div>
          </div>

          {/* Result or Error */}
          {job.result && (
            <div>
              <div className="text-[9px] text-green-400 uppercase tracking-wider mb-0.5">
                Result
              </div>
              <div className="text-[11px] text-grim-text whitespace-pre-wrap break-words font-mono bg-grim-bg rounded p-2 max-h-40 overflow-y-auto">
                {job.result}
              </div>
            </div>
          )}

          {job.error && (
            <div>
              <div className="text-[9px] text-red-400 uppercase tracking-wider mb-0.5">
                Error
              </div>
              <div className="text-[11px] text-red-300 whitespace-pre-wrap break-words font-mono bg-grim-bg rounded p-2 max-h-40 overflow-y-auto">
                {job.error}
              </div>
            </div>
          )}

          {/* Transcript */}
          {job.transcript.length > 0 && (
            <div>
              <div className="text-[9px] text-grim-text-dim uppercase tracking-wider mb-0.5">
                Transcript ({job.transcript.length} entries)
              </div>
              <div className="bg-grim-bg rounded p-2 max-h-60 overflow-y-auto space-y-1 font-mono">
                {job.transcript.map((entry, i) => (
                  <div key={i} className="text-[10px] text-grim-text-dim">
                    <span className="text-grim-accent">
                      {(entry.role as string) || "system"}
                    </span>
                    <span className="text-grim-text ml-1">
                      {String(entry.content || entry.text || JSON.stringify(entry)).slice(0, 200)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Timestamps */}
          <div className="flex gap-4 text-[9px] text-grim-text-dim font-mono">
            <span>created: {formatTime(job.created_at)}</span>
            <span>updated: {formatTime(job.updated_at)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Helpers ──

function getElapsed(created: string, updated: string): string {
  try {
    const start = new Date(created).getTime();
    const end = new Date(updated).getTime();
    const ms = end - start;
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  } catch {
    return "—";
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}
