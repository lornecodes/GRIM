"use client";

import { useMemo } from "react";
import { useGrimStore } from "@/store";
import type { TraceEvent } from "@/lib/types";

/** Infrastructure nodes — not shown as agents. */
const INFRA_NODES = new Set([
  "identity",
  "compress",
  "skill_match",
  "router",
  "integrate",
  "evolve",
  "__start__",
  "__end__",
]);

const AGENT_LABELS: Record<string, string> = {
  companion: "Companion",
  dispatch: "Dispatch",
  memory: "Memory",
  coder: "Coder",
  research: "Research",
  operator: "Operator",
  audit: "Audit",
  ironclaw: "IronClaw",
};

const AGENT_TIER: Record<string, "grim" | "ironclaw"> = {
  companion: "grim",
  dispatch: "grim",
  memory: "grim",
  coder: "grim",
  research: "grim",
  operator: "grim",
  audit: "ironclaw",
  ironclaw: "ironclaw",
};

export interface ActiveAgent {
  node: string;
  label: string;
  tier: "grim" | "ironclaw";
  traces: TraceEvent[];
  lastActive: number;
  totalMs: number;
}

/**
 * Derive active agents from recent message traces in the store.
 * Groups trace events by node, filters out infrastructure nodes,
 * and returns agents sorted by most recently active.
 */
export function useActiveAgents(recentMessageCount = 5): ActiveAgent[] {
  const messages = useGrimStore((s) => s.messages);

  return useMemo(() => {
    const recent = messages.slice(-recentMessageCount);

    const byNode = new Map<string, TraceEvent[]>();
    for (const msg of recent) {
      if (!msg.traces) continue;
      for (const trace of msg.traces) {
        // Use _activeNode (tagged in useGrimSocket) for traces without explicit node
        const node = trace.node || (trace as TraceEvent & { _activeNode?: string })._activeNode;
        if (!node || INFRA_NODES.has(node)) continue;
        if (!byNode.has(node)) byNode.set(node, []);
        byNode.get(node)!.push(trace);
      }
    }

    const agents: ActiveAgent[] = [];
    for (const [node, traces] of byNode) {
      const totalMs = traces.reduce(
        (sum, t) => sum + (t.duration_ms || 0),
        0,
      );
      const lastActive = traces.reduce(
        (max, t) => Math.max(max, t.ms),
        0,
      );
      agents.push({
        node,
        label: AGENT_LABELS[node] || node,
        tier: AGENT_TIER[node] || "grim",
        traces,
        lastActive,
        totalMs,
      });
    }

    agents.sort((a, b) => b.lastActive - a.lastActive);
    return agents;
  }, [messages, recentMessageCount]);
}
