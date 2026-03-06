"use client";

import { useState, useEffect, useCallback, useRef } from "react";

// ── Types ──

export interface SlotInfo {
  slot_id: string;
  busy: boolean;
  current_job_id: string | null;
}

export interface PoolStatus {
  running: boolean;
  slots: SlotInfo[];
  active_jobs: number;
}

export interface PoolJob {
  id: string;
  job_type: string;
  status: string;
  priority: string;
  instructions: string;
  plan: string | null;
  workspace_id: string | null;
  assigned_slot: string | null;
  retry_count: number;
  result: string | null;
  error: string | null;
  transcript: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface JobsByType {
  [jobType: string]: {
    running: number;
    queued: number;
    total: number;
  };
}

export interface UsePoolStatusResult {
  poolStatus: PoolStatus | null;
  poolEnabled: boolean;
  jobs: PoolJob[];
  jobsByType: JobsByType;
  fetchJob: (jobId: string) => Promise<PoolJob | null>;
  fetchJobsByType: (jobType: string) => Promise<PoolJob[]>;
}

const POLL_INTERVAL_ACTIVE = 5_000;
const POLL_INTERVAL_DISABLED = 60_000;

/**
 * Poll the execution pool status and job list.
 * Derives per-job-type counts for agent roster badges.
 *
 * When pool returns 503 (disabled), backs off to 60s polling
 * and only checks /status (skips /jobs to avoid 503 spam).
 */
export function usePoolStatus(): UsePoolStatusResult {
  const [poolStatus, setPoolStatus] = useState<PoolStatus | null>(null);
  const [poolEnabled, setPoolEnabled] = useState(true);
  const [jobs, setJobs] = useState<PoolJob[]>([]);
  const [jobsByType, setJobsByType] = useState<JobsByType>({});
  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disabledRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/api/pool/status`);
      if (resp.status === 503) {
        if (!disabledRef.current) {
          disabledRef.current = true;
          setPoolEnabled(false);
          setPoolStatus(null);
        }
        return;
      }
      if (resp.ok) {
        disabledRef.current = false;
        setPoolEnabled(true);
        setPoolStatus(await resp.json());
      }
    } catch {
      if (!disabledRef.current) {
        disabledRef.current = true;
        setPoolEnabled(false);
      }
    }
  }, [apiBase]);

  const fetchJobs = useCallback(async () => {
    if (disabledRef.current) return; // Skip when pool disabled
    try {
      const resp = await fetch(`${apiBase}/api/pool/jobs?limit=100`);
      if (!resp.ok) return;
      const data: PoolJob[] = await resp.json();
      setJobs(data);

      // Derive per-type counts
      const counts: JobsByType = {};
      for (const job of data) {
        if (!counts[job.job_type]) {
          counts[job.job_type] = { running: 0, queued: 0, total: 0 };
        }
        counts[job.job_type].total++;
        if (job.status === "running" || job.status === "assigned") {
          counts[job.job_type].running++;
        } else if (job.status === "queued") {
          counts[job.job_type].queued++;
        }
      }
      setJobsByType(counts);
    } catch { /* pool may be offline */ }
  }, [apiBase]);

  useEffect(() => {
    // Initial fetch
    fetchStatus();
    fetchJobs();

    // Adaptive polling — fast when active, slow when disabled
    const schedulePoll = () => {
      const interval = disabledRef.current ? POLL_INTERVAL_DISABLED : POLL_INTERVAL_ACTIVE;
      timerRef.current = setTimeout(async () => {
        await fetchStatus();
        await fetchJobs();
        schedulePoll();
      }, interval);
    };
    schedulePoll();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchStatus, fetchJobs]);

  const fetchJob = useCallback(async (jobId: string): Promise<PoolJob | null> => {
    try {
      const resp = await fetch(`${apiBase}/api/pool/jobs/${jobId}`);
      if (resp.ok) return await resp.json();
    } catch { /* ignore */ }
    return null;
  }, [apiBase]);

  const fetchJobsByType = useCallback(async (jobType: string): Promise<PoolJob[]> => {
    try {
      const resp = await fetch(`${apiBase}/api/pool/jobs?job_type=${jobType}&limit=50`);
      if (resp.ok) return await resp.json();
    } catch { /* ignore */ }
    return [];
  }, [apiBase]);

  return { poolStatus, poolEnabled, jobs, jobsByType, fetchJob, fetchJobsByType };
}
