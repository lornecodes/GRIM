"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import type { GraphData, GraphNode, GraphEdge } from "./useVaultExplorer";

interface SessionKnowledgeEntry {
  fdo_id: string;
  fdo_title: string;
  fdo_domain: string;
  fdo_confidence: number;
  fetched_turn: number;
  fetched_by: string;
  query: string;
  last_referenced_turn: number;
  hit_count: number;
  related?: string[];
}

interface UseSessionKnowledgeResult {
  entries: SessionKnowledgeEntry[];
  graphData: GraphData | null;
  totalCount: number;
  isLoading: boolean;
  maxTurn: number;
  /** Filter graph to only show FDOs accumulated up to this turn */
  filterToTurn: (turn: number) => void;
  currentTurn: number;
  refresh: () => Promise<void>;
}

function getApiBase(): string {
  if (typeof window === "undefined") return "";
  return process.env.NEXT_PUBLIC_GRIM_API || "";
}

export function useSessionKnowledge(sessionId?: string): UseSessionKnowledgeResult {
  const [entries, setEntries] = useState<SessionKnowledgeEntry[]>([]);
  const [graphRaw, setGraphRaw] = useState<{ nodes: any[]; edges: any[] } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [currentTurn, setCurrentTurn] = useState(Infinity); // show all by default

  const apiBase = getApiBase();

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const params = sessionId ? `?session_id=${sessionId}` : "";

      const [entriesRes, graphRes] = await Promise.all([
        fetch(`${apiBase}/api/session/knowledge${params}`),
        fetch(`${apiBase}/api/session/knowledge/graph${params}`),
      ]);

      if (entriesRes.ok) {
        const data = await entriesRes.json();
        setEntries(data.entries || []);
      }
      if (graphRes.ok) {
        const data = await graphRes.json();
        setGraphRaw({ nodes: data.nodes || [], edges: data.edges || [] });
      }
    } catch {
      // Silently fail — API may not be available
    } finally {
      setIsLoading(false);
    }
  }, [apiBase, sessionId]);

  // Auto-refresh on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  const maxTurn = useMemo(() => {
    if (!entries.length) return 0;
    return Math.max(...entries.map((e) => e.fetched_turn));
  }, [entries]);

  const filterToTurn = useCallback((turn: number) => {
    setCurrentTurn(turn);
  }, []);

  // Build filtered GraphData from raw response + turn filter
  const graphData = useMemo((): GraphData | null => {
    if (!graphRaw) return null;

    const turnLimit = currentTurn === Infinity ? Infinity : currentTurn;

    // Filter entries by turn
    const visibleIds = new Set(
      entries
        .filter((e) => e.fetched_turn <= turnLimit)
        .map((e) => e.fdo_id)
    );

    const nodes: Record<string, GraphNode> = {};
    for (const n of graphRaw.nodes) {
      if (visibleIds.has(n.id)) {
        nodes[n.id] = {
          id: n.id,
          title: n.title || n.id,
          domain: n.domain || "",
          status: "stable",
          confidence: n.confidence || 0,
          tags: [],
        };
      }
    }

    const edges: GraphEdge[] = graphRaw.edges
      .filter(
        (e: any) => visibleIds.has(e.source) && visibleIds.has(e.target)
      )
      .map((e: any) => ({
        from: e.source,
        to: e.target,
        type: e.type || "related",
      }));

    return { nodes, edges, count: Object.keys(nodes).length };
  }, [graphRaw, entries, currentTurn]);

  return {
    entries,
    graphData,
    totalCount: entries.length,
    isLoading,
    maxTurn,
    filterToTurn,
    currentTurn: currentTurn === Infinity ? maxTurn : currentTurn,
    refresh,
  };
}
