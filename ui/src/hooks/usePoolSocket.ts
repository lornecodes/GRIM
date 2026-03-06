"use client";

import { useEffect, useRef, useCallback } from "react";
import { useGrimStore } from "@/store";
import type { TranscriptEntry, JobStatus } from "@/lib/poolTypes";

/** Build pool WebSocket URL from env or window origin. */
function getPoolWsUrl(): string {
  if (typeof window === "undefined") return "";

  const apiUrl = process.env.NEXT_PUBLIC_GRIM_API;
  if (apiUrl) {
    const url = new URL(apiUrl);
    const protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${url.host}/ws-pool`;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws-pool`;
}

const RECONNECT_DELAY = 3_000;
const POLL_INTERVAL_ACTIVE = 10_000;
const POLL_INTERVAL_DISABLED = 60_000;

// ── Module-level singleton state ──
// Single WebSocket + subscription set shared across all consumers.
let _ws: WebSocket | null = null;
let _subs: Set<string> = new Set();
let _poolDisabled = false;

/** Subscribe to streaming events for a job (callable from any hook/component). */
export function poolSubscribe(jobId: string) {
  _subs.add(jobId);
  if (_ws?.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ action: "subscribe", job_id: jobId }));
  }
}

/** Unsubscribe from streaming events for a job. */
export function poolUnsubscribe(jobId: string) {
  _subs.delete(jobId);
  if (_ws?.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ action: "unsubscribe", job_id: jobId }));
  }
}

/** Subscribe to ALL streaming events. */
export function poolSubscribeAll() {
  _subs.add("*");
  if (_ws?.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ action: "subscribe_all" }));
  }
}

/**
 * Mount this hook ONCE at the app root (ChatApp).
 * It manages the singleton WebSocket connection and feeds the Zustand store.
 *
 * When the pool is disabled (503), backs off to 60s polling and skips
 * jobs/metrics requests to avoid 503 spam.
 */
export function usePoolSocket() {
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pollRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const store = useGrimStore;
  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  // ── Fetch full state via REST (heartbeat / fallback) ──

  const fetchPoolState = useCallback(async () => {
    // If pool was previously disabled, just check status (not jobs/metrics)
    if (_poolDisabled) {
      try {
        const resp = await fetch(`${apiBase}/api/pool/status`);
        if (resp.status === 503) {
          // Still disabled — don't update store (already set)
          return;
        }
        if (resp.ok) {
          // Pool came back online — full fetch below
          _poolDisabled = false;
          store.getState().setPoolEnabled(true);
          store.getState().setPoolStatus(await resp.json());
        }
      } catch {
        return; // Still down, skip
      }
    }

    // Pool is (or just became) enabled — fetch everything
    if (!_poolDisabled) {
      try {
        const [statusResp, jobsResp, metricsResp] = await Promise.allSettled([
          fetch(`${apiBase}/api/pool/status`),
          fetch(`${apiBase}/api/pool/jobs?limit=200`),
          fetch(`${apiBase}/api/pool/metrics`),
        ]);

        if (statusResp.status === "fulfilled") {
          if (statusResp.value.status === 503) {
            _poolDisabled = true;
            store.getState().setPoolEnabled(false);
            store.getState().setPoolStatus(null);
            return; // Don't process jobs/metrics responses
          } else if (statusResp.value.ok) {
            store.getState().setPoolEnabled(true);
            store.getState().setPoolStatus(await statusResp.value.json());
          }
        }

        if (jobsResp.status === "fulfilled" && jobsResp.value.ok) {
          store.getState().setPoolJobs(await jobsResp.value.json());
        }

        if (metricsResp.status === "fulfilled" && metricsResp.value.ok) {
          store.getState().setPoolMetrics(await metricsResp.value.json());
        }
      } catch {
        _poolDisabled = true;
        store.getState().setPoolEnabled(false);
      }
    }
  }, [apiBase, store]);

  // ── Handle incoming pool WS messages ──

  const handleMessage = useCallback(
    (data: Record<string, unknown>) => {
      if (data.type !== "pool_event") return;

      const eventType = data.event_type as string | undefined;
      const jobId = data.job_id as string;

      if (!eventType) return;

      switch (eventType) {
        // Lifecycle events — incremental update + targeted refetch
        case "job_submitted": {
          fetchPoolState();
          break;
        }
        case "job_started":
        case "job_complete":
        case "job_failed":
        case "job_blocked":
        case "job_cancelled":
        case "job_review": {
          const statusMap: Record<string, JobStatus> = {
            job_started: "running",
            job_complete: "complete",
            job_failed: "failed",
            job_blocked: "blocked",
            job_cancelled: "cancelled",
            job_review: "review",
          };
          store.getState().upsertPoolJob({ id: jobId, status: statusMap[eventType] });
          fetchPoolState();
          break;
        }

        // Streaming events — append to live transcript
        case "agent_output": {
          const blockType = (data.block_type as string) || (data.text ? "text" : data.name ? "tool_use" : null);
          let entry: TranscriptEntry;
          if (blockType === "text") {
            entry = {
              seq: store.getState().liveTranscripts[jobId]?.length ?? 0,
              timestamp: Date.now(),
              type: "text",
              text: (data.text as string) || "",
            };
          } else if (blockType === "tool_use") {
            entry = {
              seq: store.getState().liveTranscripts[jobId]?.length ?? 0,
              timestamp: Date.now(),
              type: "tool_use",
              toolName: (data.name as string) || "",
              toolInput: data.input,
            };
          } else {
            return;
          }
          store.getState().appendTranscriptEntry(jobId, entry);
          break;
        }

        case "agent_tool_result": {
          const entry: TranscriptEntry = {
            seq: store.getState().liveTranscripts[jobId]?.length ?? 0,
            timestamp: Date.now(),
            type: "tool_result",
            outputPreview: JSON.stringify(data).slice(0, 500),
          };
          store.getState().appendTranscriptEntry(jobId, entry);
          break;
        }
      }
    },
    [store, fetchPoolState],
  );

  // ── Re-send tracked subscriptions on reconnect ──

  const sendSubscriptions = useCallback((ws: WebSocket) => {
    for (const id of _subs) {
      if (id === "*") {
        ws.send(JSON.stringify({ action: "subscribe_all" }));
      } else {
        ws.send(JSON.stringify({ action: "subscribe", job_id: id }));
      }
    }
  }, []);

  // ── WebSocket connect ──

  const connect = useCallback(() => {
    const url = getPoolWsUrl();
    if (!url) return;

    const ws = new WebSocket(url);
    _ws = ws;

    ws.onopen = () => {
      sendSubscriptions(ws);
      fetchPoolState();
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        handleMessage(data);
      } catch {
        // ignore malformed
      }
    };

    ws.onclose = () => {
      _ws = null;
      reconnectRef.current = setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [fetchPoolState, handleMessage, sendSubscriptions]);

  // ── Lifecycle — adaptive polling ──

  useEffect(() => {
    connect();

    // Adaptive interval: fast when pool active, slow when disabled
    const schedulePoll = () => {
      const interval = _poolDisabled ? POLL_INTERVAL_DISABLED : POLL_INTERVAL_ACTIVE;
      pollRef.current = setTimeout(async () => {
        await fetchPoolState();
        schedulePoll(); // Reschedule with possibly-updated interval
      }, interval);
    };
    schedulePoll();

    return () => {
      if (_ws) {
        _ws.close();
        _ws = null;
      }
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [connect, fetchPoolState]);
}
