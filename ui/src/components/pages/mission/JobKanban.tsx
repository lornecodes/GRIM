"use client";

import { useMemo } from "react";
import { useGrimStore } from "@/store";
import { KANBAN_COLUMNS } from "@/lib/poolTypes";
import type { PoolJob } from "@/lib/poolTypes";
import type { PipelineItem, PipelineStatus } from "@/lib/daemonTypes";
import { PIPELINE_COLUMNS } from "@/lib/daemonTypes";
import { JobKanbanCard } from "./JobKanbanCard";
import { PipelineCard } from "../daemon/PipelineCard";

interface JobKanbanProps {
  /** When provided, shows the full pipeline view with upstream/downstream columns. */
  pipelineItems?: PipelineItem[];
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
}

/** Map pipeline status to pool job status for the shared columns. */
const PIPELINE_TO_POOL: Record<string, string> = {
  dispatched: "queued",  // dispatched pipeline items appear as queued/running pool jobs
  review: "review",
};

export function JobKanban({ pipelineItems, onApprove, onReject }: JobKanbanProps) {
  const jobs = useGrimStore((s) => s.poolJobs);
  const showPipeline = !!pipelineItems && pipelineItems.length > 0;

  // Pool job columns (always shown)
  const poolColumns = useMemo(() => {
    const map: Record<string, PoolJob[]> = {};
    for (const col of KANBAN_COLUMNS) {
      map[col.id] = [];
    }
    for (const job of jobs) {
      if (map[job.status]) {
        map[job.status].push(job);
      }
    }
    for (const col of KANBAN_COLUMNS) {
      map[col.id].sort((a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
    }
    return map;
  }, [jobs]);

  // Pipeline columns (only when pipeline data provided)
  const pipelineColumns = useMemo(() => {
    if (!pipelineItems) return {};
    const map: Record<string, PipelineItem[]> = {};
    for (const col of PIPELINE_COLUMNS) {
      map[col.id] = [];
    }
    for (const item of pipelineItems) {
      if (map[item.status]) {
        map[item.status].push(item);
      }
    }
    for (const col of PIPELINE_COLUMNS) {
      map[col.id]?.sort((a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
    }
    return map;
  }, [pipelineItems]);

  // Build unified column list
  const columns = useMemo(() => {
    if (!showPipeline) {
      // Standard pool-only view
      return KANBAN_COLUMNS.map((col) => ({
        id: col.id,
        label: col.label,
        poolJobs: poolColumns[col.id] ?? [],
        pipelineItems: [] as PipelineItem[],
        type: "pool" as const,
      }));
    }

    // Unified view: pipeline columns that don't overlap with pool + pool columns
    const unified: Array<{
      id: string;
      label: string;
      poolJobs: PoolJob[];
      pipelineItems: PipelineItem[];
      type: "pipeline" | "pool" | "merged";
    }> = [];

    // Upstream pipeline columns (no pool equivalent)
    unified.push({
      id: "backlog", label: "Backlog",
      poolJobs: [], pipelineItems: pipelineColumns["backlog"] ?? [],
      type: "pipeline",
    });
    unified.push({
      id: "ready", label: "Ready",
      poolJobs: [], pipelineItems: pipelineColumns["ready"] ?? [],
      type: "pipeline",
    });

    // Pool columns (queued, running)
    unified.push({
      id: "queued", label: "Queued",
      poolJobs: poolColumns["queued"] ?? [], pipelineItems: [],
      type: "pool",
    });
    unified.push({
      id: "running", label: "Running",
      poolJobs: poolColumns["running"] ?? [], pipelineItems: [],
      type: "pool",
    });

    // Merged: review shows both pool and pipeline
    unified.push({
      id: "review", label: "Review",
      poolJobs: poolColumns["review"] ?? [],
      pipelineItems: pipelineColumns["review"] ?? [],
      type: "merged",
    });

    // Downstream pipeline
    unified.push({
      id: "merged", label: "Merged",
      poolJobs: [], pipelineItems: pipelineColumns["merged"] ?? [],
      type: "pipeline",
    });

    // Exception lanes
    unified.push({
      id: "failed", label: "Failed",
      poolJobs: poolColumns["failed"] ?? [],
      pipelineItems: pipelineColumns["failed"] ?? [],
      type: "merged",
    });
    unified.push({
      id: "blocked", label: "Blocked",
      poolJobs: poolColumns["blocked"] ?? [],
      pipelineItems: pipelineColumns["blocked"] ?? [],
      type: "merged",
    });

    return unified;
  }, [showPipeline, poolColumns, pipelineColumns]);

  return (
    <div>
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-1.5">
        {showPipeline ? "Pipeline Board" : "Job Board"}
      </div>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {columns.map((col) => {
          const totalCount = col.poolJobs.length + col.pipelineItems.length;

          return (
            <div key={col.id} className={`flex-1 ${showPipeline ? "min-w-[150px]" : "min-w-[200px]"}`}>
              {/* Column header */}
              <div className="flex items-center gap-2 mb-2 px-1">
                <span className="text-[11px] font-medium text-grim-text">{col.label}</span>
                <span className="text-[9px] font-mono text-grim-text-dim bg-grim-surface px-1.5 py-0.5 rounded">
                  {totalCount}
                </span>
              </div>

              {/* Cards */}
              <div className="space-y-1.5 min-h-[100px]">
                {/* Pipeline-only items (backlog, ready, merged, or merged columns) */}
                {col.pipelineItems.map((item) => (
                  <PipelineCard
                    key={item.id}
                    item={item}
                    onApprove={onApprove}
                    onReject={onReject}
                  />
                ))}

                {/* Pool job items */}
                {col.poolJobs.map((job) => (
                  <JobKanbanCard key={job.id} job={job} />
                ))}

                {totalCount === 0 && (
                  <div className="text-[10px] text-grim-text-dim text-center py-4 border border-dashed border-grim-border/30 rounded-md">
                    Empty
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
