"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useGrimStore } from "@/store";
import { poolSubscribe, poolUnsubscribe } from "./usePoolSocket";
import type { PoolJob, TranscriptEntry } from "@/lib/poolTypes";
import { flattenTranscript } from "@/lib/poolTypes";

const EMPTY_TRANSCRIPT: TranscriptEntry[] = [];

export interface WorkspaceDiff {
  workspace_id: string;
  diff_stat: string | null;
  changed_files: string[] | null;
  full_diff: string | null;
}

export interface UseJobDetailResult {
  job: PoolJob | null;
  transcript: TranscriptEntry[];
  isLive: boolean;
  loading: boolean;
  diff: WorkspaceDiff | null;
  refetch: () => Promise<void>;
}

/**
 * Fetch full job details and subscribe to live transcript streaming.
 *
 * - Fetches the job via REST on mount
 * - Subscribes to streaming events via pool WebSocket
 * - Merges stored transcript + live streaming entries
 * - Fetches workspace diff when job has a workspace
 */
export function useJobDetail(jobId: string | null): UseJobDetailResult {
  const [job, setJob] = useState<PoolJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [diff, setDiff] = useState<WorkspaceDiff | null>(null);
  const prevJobId = useRef<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  // Live transcript from store
  const liveTranscript = useGrimStore((s) => (jobId ? s.liveTranscripts[jobId] ?? EMPTY_TRANSCRIPT : EMPTY_TRANSCRIPT));

  const fetchJob = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}/api/pool/jobs/${jobId}`);
      if (resp.ok) {
        const data = await resp.json();
        setJob(data);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [apiBase, jobId]);

  const fetchDiff = useCallback(async (wsId: string) => {
    try {
      const resp = await fetch(`${apiBase}/api/pool/workspaces/${wsId}/diff`);
      if (resp.ok) {
        setDiff(await resp.json());
      }
    } catch {
      // ignore
    }
  }, [apiBase]);

  // Subscribe/unsubscribe when jobId changes
  useEffect(() => {
    if (prevJobId.current && prevJobId.current !== jobId) {
      poolUnsubscribe(prevJobId.current);
      useGrimStore.getState().clearTranscript(prevJobId.current);
    }

    if (jobId) {
      poolSubscribe(jobId);
      fetchJob();
    }

    prevJobId.current = jobId;

    return () => {
      if (jobId) {
        poolUnsubscribe(jobId);
      }
    };
  }, [jobId, fetchJob]);

  // Fetch diff when job has workspace and is complete/review
  useEffect(() => {
    if (job?.workspace_id && (job.status === "complete" || job.status === "review")) {
      fetchDiff(job.workspace_id);
    }
  }, [job?.workspace_id, job?.status, fetchDiff]);

  // Merge stored transcript + live entries
  const isLive = job?.status === "running";
  const storedEntries = job?.transcript ? flattenTranscript(job.transcript as unknown as Array<Record<string, unknown>>) : [];
  // If live, prefer live transcript (it has real-time entries); if complete, use stored
  const transcript = isLive && liveTranscript.length > 0 ? liveTranscript : storedEntries;

  return {
    job,
    transcript,
    isLive,
    loading,
    diff,
    refetch: fetchJob,
  };
}
