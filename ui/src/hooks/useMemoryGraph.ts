"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import type { GraphData, GraphNode, GraphEdge } from "./useVaultExplorer";

interface MemoryGraphNode {
  id: string;
  title: string;
  sections: string[];
  reference_count: number;
}

interface UseMemoryGraphResult {
  graphData: GraphData | null;
  sections: string[];
  selectedNode: string | null;
  setSelectedNode: (id: string | null) => void;
  isLoading: boolean;
  filters: {
    section: string | null;
    setSection: (s: string | null) => void;
  };
  refresh: () => Promise<void>;
}

function getApiBase(): string {
  if (typeof window === "undefined") return "";
  return process.env.NEXT_PUBLIC_GRIM_API || "";
}

export function useMemoryGraph(): UseMemoryGraphResult {
  const [rawNodes, setRawNodes] = useState<MemoryGraphNode[]>([]);
  const [rawEdges, setRawEdges] = useState<any[]>([]);
  const [sections, setSections] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [sectionFilter, setSectionFilter] = useState<string | null>(null);

  const apiBase = getApiBase();

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/memory/graph`);
      if (res.ok) {
        const data = await res.json();
        setRawNodes(data.nodes || []);
        setRawEdges(data.edges || []);
        setSections(data.sections || []);
      }
    } catch {
      // Silently fail
    } finally {
      setIsLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const graphData = useMemo((): GraphData | null => {
    if (!rawNodes.length) return null;

    // Apply section filter
    const filtered = sectionFilter
      ? rawNodes.filter((n) => n.sections.includes(sectionFilter))
      : rawNodes;

    const visibleIds = new Set(filtered.map((n) => n.id));

    const nodes: Record<string, GraphNode> = {};
    for (const n of filtered) {
      nodes[n.id] = {
        id: n.id,
        title: n.title,
        domain: "notes", // memory FDOs don't have domain from this endpoint
        status: "stable",
        confidence: Math.min(1, n.reference_count / 5),
        tags: n.sections,
      };
    }

    const edges: GraphEdge[] = rawEdges
      .filter((e: any) => visibleIds.has(e.source) && visibleIds.has(e.target))
      .map((e: any) => ({
        from: e.source,
        to: e.target,
        type: e.type || "co_section",
      }));

    return { nodes, edges, count: Object.keys(nodes).length };
  }, [rawNodes, rawEdges, sectionFilter]);

  return {
    graphData,
    sections,
    selectedNode,
    setSelectedNode,
    isLoading,
    filters: {
      section: sectionFilter,
      setSection: setSectionFilter,
    },
    refresh,
  };
}
