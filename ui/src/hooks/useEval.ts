"use client";

import { useState, useCallback, useRef, useEffect } from "react";

// ── Types ──

export interface EvalRunSummary {
  run_id: string;
  timestamp: string;
  status: string;
  tier: string | number;
  total_cases: number;
  passed_cases: number;
  overall_score: number;
  git_sha: string;
  duration_ms: number;
  suite_scores?: Record<string, number>;
  file?: string;
}

export interface EvalDatasetInfo {
  tier: number;
  category: string;
  description: string;
  case_count: number;
  path: string;
}

export interface SuiteProgress {
  type: string;
  tier: number;
  category: string;
  total?: number;
  passed?: number;
  score?: number;
  // Tier 3 per-case fields
  case_id?: string;
  index?: number;
  duration_ms?: number;
}

export interface TestCase {
  id: string;
  tier: number;
  category: string;
  description: string;
  tags: string[];
  turn_count: number;
}

export interface CaseRunStatus {
  case_id: string;
  status: "pending" | "running" | "passed" | "failed";
  score?: number;
  duration_ms?: number;
}

// ── API base ──

function apiBase(): string {
  if (typeof window !== "undefined") {
    const proto = window.location.protocol;
    const host = window.location.hostname;
    return `${proto}//${host}:8080`;
  }
  return "http://localhost:8080";
}

function wsBase(): string {
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    return `ws://${host}:8080`;
  }
  return "ws://localhost:8080";
}

// ── Hook ──

export function useEval() {
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [activeResult, setActiveResult] = useState<Record<string, unknown> | null>(null);
  const [datasets, setDatasets] = useState<EvalDatasetInfo[]>([]);
  const [datasetContent, setDatasetContent] = useState<Record<string, unknown> | null>(null);
  const [runStatus, setRunStatus] = useState<"idle" | "running" | "completed" | "failed">("idle");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [progress, setProgress] = useState<SuiteProgress[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testCases, setTestCases] = useState<TestCase[]>([]);
  const [caseRunStatus, setCaseRunStatus] = useState<CaseRunStatus[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Fetch saved runs ──
  const fetchRuns = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/runs`);
      if (res.ok) setRuns(await res.json());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // ── Fetch datasets ──
  const fetchDatasets = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/datasets`);
      if (res.ok) setDatasets(await res.json());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // ── Fetch test cases for a tier ──
  const fetchTestCases = useCallback(async (tier: number, category?: string) => {
    try {
      let url = `${apiBase()}/api/eval/cases/${tier}`;
      if (category) url += `?category=${encodeURIComponent(category)}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setTestCases(data.cases || []);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // ── Fetch full results for a run ──
  const fetchResults = useCallback(async (runId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${apiBase()}/api/eval/results/${runId}`);
      if (res.ok) {
        const data = await res.json();
        setActiveResult(data);
      } else {
        setError("Failed to fetch results");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Fetch dataset content ──
  const fetchDatasetContent = useCallback(async (tier: number, category: string) => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/datasets/${tier}/${category}`);
      if (res.ok) setDatasetContent(await res.json());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // ── Start an eval run ──
  const startRun = useCallback(async (tier: number | string = "all", categories?: string[]) => {
    setRunStatus("running");
    setProgress([]);
    setCaseRunStatus([]);
    setError(null);

    try {
      const res = await fetch(`${apiBase()}/api/eval/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, categories }),
      });
      if (!res.ok) throw new Error(`Start failed: ${res.status}`);
      const data = await res.json();
      const runId = data.run_id;
      setActiveRunId(runId);

      // Connect WebSocket for progress
      try {
        const ws = new WebSocket(`${wsBase()}/ws/eval/${runId}`);
        wsRef.current = ws;
        ws.onmessage = (e) => {
          const event = JSON.parse(e.data);

          // Tier 1/2 suite events
          if (event.type === "suite_start" || event.type === "suite_end") {
            setProgress((p) => [...p, event]);
          }

          // Tier 3 per-case events
          if (event.type === "tier3_case_start") {
            setCaseRunStatus((prev) => [
              ...prev.filter((c) => c.case_id !== event.case_id),
              { case_id: event.case_id, status: "running" },
            ]);
            setProgress((p) => [...p, { ...event, tier: 3 }]);
          }
          if (event.type === "tier3_case_end") {
            setCaseRunStatus((prev) =>
              prev.map((c) =>
                c.case_id === event.case_id
                  ? {
                      ...c,
                      status: event.passed ? "passed" : "failed",
                      score: event.score,
                      duration_ms: event.duration_ms,
                    }
                  : c
              )
            );
            setProgress((p) => [...p, { ...event, tier: 3 }]);
          }
          if (event.type === "tier3_start") {
            setProgress((p) => [...p, { ...event, tier: 3, type: "tier3_start" }]);
          }

          if (event.type === "complete") {
            setRunStatus("completed");
            fetchRuns();
            fetchResults(runId);
          }
          if (event.type === "error") {
            setRunStatus("failed");
            setError(event.message);
          }
        };
        ws.onerror = () => {
          ws.close();
        };
      } catch {
        // WS not available, fallback to polling
      }

      // Also poll as fallback
      pollRef.current = setInterval(async () => {
        try {
          const statusRes = await fetch(`${apiBase()}/api/eval/run/${runId}`);
          if (statusRes.ok) {
            const statusData = await statusRes.json();
            if (statusData.status === "completed") {
              setRunStatus("completed");
              setActiveResult(statusData.results);
              if (pollRef.current) clearInterval(pollRef.current);
              fetchRuns();
            } else if (statusData.status === "failed") {
              setRunStatus("failed");
              setError(statusData.error);
              if (pollRef.current) clearInterval(pollRef.current);
            }
          }
        } catch { /* ignore poll errors */ }
      }, 2000);
    } catch (e) {
      setRunStatus("failed");
      setError((e as Error).message);
    }
  }, [fetchRuns, fetchResults]);

  // ── Append a case ──
  const appendCase = useCallback(async (tier: number, category: string, caseData: Record<string, unknown>) => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/datasets/${tier}/${category}/cases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case: caseData }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || `Append failed: ${res.status}`);
      }
      fetchDatasets(); // refresh counts
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, [fetchDatasets]);

  // ── Update a case ──
  const updateCase = useCallback(async (tier: number, category: string, caseId: string, caseData: Record<string, unknown>) => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/datasets/${tier}/${category}/cases/${encodeURIComponent(caseId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case: caseData }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || `Update failed: ${res.status}`);
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, []);

  // ── Delete a case ──
  const deleteCase = useCallback(async (tier: number, category: string, caseId: string) => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/datasets/${tier}/${category}/cases/${encodeURIComponent(caseId)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || `Delete failed: ${res.status}`);
      }
      fetchDatasets();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, [fetchDatasets]);

  // ── Compare runs ──
  const compareRuns = useCallback(async (baseId: string, targetId: string) => {
    try {
      const res = await fetch(`${apiBase()}/api/eval/compare?base=${baseId}&target=${targetId}`);
      if (res.ok) return await res.json();
    } catch (e) {
      setError((e as Error).message);
    }
    return null;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  return {
    // State
    runs, activeResult, datasets, datasetContent, runStatus, activeRunId,
    progress, loading, error, testCases, caseRunStatus,
    // Actions
    fetchRuns, fetchDatasets, fetchResults, fetchDatasetContent, fetchTestCases,
    startRun, appendCase, updateCase, deleteCase, compareRuns,
    setActiveResult, setError,
  };
}
