"use client";

/**
 * Derives live graph execution overlay from WebSocket traces in the store.
 *
 * Reads the most recent message's traces to determine which nodes are
 * active, completed, and what edges were traversed during the current turn.
 */

import { useMemo } from "react";
import { useGrimStore } from "@/store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NodeOverlayState {
  /** Currently executing (node start seen, no end yet). */
  active: boolean;
  /** Completed in the current turn. */
  completed: boolean;
  /** Execution duration in ms (from node end trace). */
  durationMs: number;
  /** Was traversed in the current turn (either active or completed). */
  path: boolean;
}

export interface EdgeOverlayState {
  /** This edge was traversed in the current turn. */
  traversed: boolean;
}

export interface GraphOverlay {
  isStreaming: boolean;
  nodes: Record<string, NodeOverlayState>;
  /** Key format: "source->target" */
  edges: Record<string, EdgeOverlayState>;
  activeNodeId: string | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useGraphOverlay(): GraphOverlay {
  const messages = useGrimStore((s) => s.messages);
  const isStreaming = useGrimStore((s) => s.isStreaming);

  return useMemo(() => {
    // Use traces from the most recent message (streaming or completed)
    const lastMsg = messages[messages.length - 1];
    if (!lastMsg?.traces?.length) {
      return { isStreaming, nodes: {}, edges: {}, activeNodeId: null };
    }

    const traces = lastMsg.traces;
    const nodeStates: Record<string, NodeOverlayState> = {};
    const edgeStates: Record<string, EdgeOverlayState> = {};
    let activeNodeId: string | null = null;
    let prevNode: string | null = null;

    for (const trace of traces) {
      if (trace.cat !== "node" || !trace.node) continue;
      const n = trace.node;

      if (!nodeStates[n]) {
        nodeStates[n] = { active: false, completed: false, durationMs: 0, path: false };
      }

      if (trace.action === "start") {
        nodeStates[n].active = true;
        nodeStates[n].path = true;
        activeNodeId = n;

        // Record edge from previous node to this one
        if (prevNode) {
          edgeStates[`${prevNode}->${n}`] = { traversed: true };
        }
        prevNode = n;
      } else if (trace.action === "end") {
        nodeStates[n].active = false;
        nodeStates[n].completed = true;
        nodeStates[n].durationMs = trace.duration_ms ?? 0;
        if (activeNodeId === n) activeNodeId = null;
      }
    }

    return { isStreaming, nodes: nodeStates, edges: edgeStates, activeNodeId };
  }, [messages, isStreaming]);
}
