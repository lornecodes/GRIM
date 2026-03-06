"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import type { PipelineItem, DaemonStatus } from "@/lib/daemonTypes";

// ── Module-level daemon refetch callbacks (for WebSocket integration) ──

const _daemonRefetchCallbacks = new Set<() => void>();

/** Register a refetch callback (called from usePoolSocket on daemon events). */
export function registerDaemonRefetch(cb: () => void) {
  _daemonRefetchCallbacks.add(cb);
}

/** Unregister a refetch callback. */
export function unregisterDaemonRefetch(cb: () => void) {
  _daemonRefetchCallbacks.delete(cb);
}

/** Trigger all registered daemon refetch callbacks. */
export function triggerDaemonRefetch() {
  for (const cb of _daemonRefetchCallbacks) cb();
}

// ── Hook ──

const POLL_INTERVAL_ACTIVE = 10_000;
const POLL_INTERVAL_DISABLED = 60_000;

export interface UseDaemonResult {
  status: DaemonStatus | null;
  pipeline: PipelineItem[];
  escalations: PipelineItem[];
  loading: boolean;
  daemonEnabled: boolean;
  // Actions
  approveItem: (id: string) => Promise<void>;
  rejectItem: (id: string) => Promise<void>;
  retryItem: (id: string) => Promise<void>;
  resolveEscalation: (id: string, answer: string) => Promise<void>;
  refetch: () => Promise<void>;
}

export function useDaemonStatus(): UseDaemonResult {
  const [status, setStatus] = useState<DaemonStatus | null>(null);
  const [pipeline, setPipeline] = useState<PipelineItem[]>([]);
  const [escalations, setEscalations] = useState<PipelineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [daemonEnabled, setDaemonEnabled] = useState(true);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disabledRef = useRef(false);

  const fetchAll = useCallback(async () => {
    try {
      const statusResp = await fetch(`${apiBase}/api/daemon/status`);
      if (statusResp.status === 503) {
        disabledRef.current = true;
        setDaemonEnabled(false);
        setStatus(null);
        setLoading(false);
        return;
      }
      if (statusResp.ok) {
        disabledRef.current = false;
        setDaemonEnabled(true);
        setStatus(await statusResp.json());
      }
    } catch {
      disabledRef.current = true;
      setDaemonEnabled(false);
      setStatus(null);
      setLoading(false);
      return;
    }

    // Fetch pipeline + escalations in parallel
    try {
      const [pipeResp, escResp] = await Promise.allSettled([
        fetch(`${apiBase}/api/daemon/pipeline`),
        fetch(`${apiBase}/api/daemon/escalations`),
      ]);
      if (pipeResp.status === "fulfilled" && pipeResp.value.ok) {
        setPipeline(await pipeResp.value.json());
      }
      if (escResp.status === "fulfilled" && escResp.value.ok) {
        setEscalations(await escResp.value.json());
      }
    } catch { /* ignore */ }

    setLoading(false);
  }, [apiBase]);

  // Polling loop
  useEffect(() => {
    fetchAll();

    const schedulePoll = () => {
      const interval = disabledRef.current ? POLL_INTERVAL_DISABLED : POLL_INTERVAL_ACTIVE;
      timerRef.current = setTimeout(async () => {
        await fetchAll();
        schedulePoll();
      }, interval);
    };
    schedulePoll();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchAll]);

  // Register for WebSocket-triggered refetches
  useEffect(() => {
    registerDaemonRefetch(fetchAll);
    return () => { unregisterDaemonRefetch(fetchAll); };
  }, [fetchAll]);

  // ── Action methods ──

  const approveItem = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/daemon/pipeline/${id}/approve`, { method: "POST" });
      await fetchAll();
    } catch { /* ignore */ }
  }, [apiBase, fetchAll]);

  const rejectItem = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/daemon/pipeline/${id}/reject`, { method: "POST" });
      await fetchAll();
    } catch { /* ignore */ }
  }, [apiBase, fetchAll]);

  const retryItem = useCallback(async (id: string) => {
    try {
      await fetch(`${apiBase}/api/daemon/pipeline/${id}/retry`, { method: "POST" });
      await fetchAll();
    } catch { /* ignore */ }
  }, [apiBase, fetchAll]);

  const resolveEscalation = useCallback(async (id: string, answer: string) => {
    try {
      await fetch(`${apiBase}/api/daemon/escalations/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
      });
      await fetchAll();
    } catch { /* ignore */ }
  }, [apiBase, fetchAll]);

  return {
    status,
    pipeline,
    escalations,
    loading,
    daemonEnabled,
    approveItem,
    rejectItem,
    retryItem,
    resolveEscalation,
    refetch: fetchAll,
  };
}
