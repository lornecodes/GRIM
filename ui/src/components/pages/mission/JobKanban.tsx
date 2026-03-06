"use client";

import { useMemo } from "react";
import { useGrimStore } from "@/store";
import { KANBAN_COLUMNS } from "@/lib/poolTypes";
import type { PoolJob, JobStatus } from "@/lib/poolTypes";
import { JobKanbanCard } from "./JobKanbanCard";

export function JobKanban() {
  const jobs = useGrimStore((s) => s.poolJobs);

  const columns = useMemo(() => {
    const map: Record<string, PoolJob[]> = {};
    for (const col of KANBAN_COLUMNS) {
      map[col.id] = [];
    }
    for (const job of jobs) {
      if (map[job.status]) {
        map[job.status].push(job);
      }
    }
    // Sort each column: running by started_at desc, queued by priority, complete by updated_at desc
    for (const col of KANBAN_COLUMNS) {
      map[col.id].sort((a, b) => {
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      });
    }
    return map;
  }, [jobs]);

  return (
    <div>
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-1.5">Job Board</div>
      <div className="flex gap-2 overflow-x-auto pb-2">
        {KANBAN_COLUMNS.map((col) => (
          <div key={col.id} className="flex-1 min-w-[200px]">
            {/* Column header */}
            <div className="flex items-center gap-2 mb-2 px-1">
              <span className="text-[11px] font-medium text-grim-text">{col.label}</span>
              <span className="text-[9px] font-mono text-grim-text-dim bg-grim-surface px-1.5 py-0.5 rounded">
                {columns[col.id]?.length ?? 0}
              </span>
            </div>

            {/* Cards */}
            <div className="space-y-1.5 min-h-[100px]">
              {(columns[col.id] ?? []).map((job) => (
                <JobKanbanCard key={job.id} job={job} />
              ))}
              {(columns[col.id]?.length ?? 0) === 0 && (
                <div className="text-[10px] text-grim-text-dim text-center py-4 border border-dashed border-grim-border/30 rounded-md">
                  No jobs
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
