"use client";

import { useState, useEffect, useCallback, useRef } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FDOSummary {
  id: string;
  title: string;
  domain: string;
  status: string;
  confidence: number;
  tags: string[];
  updated?: string;
}

export interface FDOFull extends FDOSummary {
  body: string;
  related: string[];
  pac_parent?: string;
  pac_children?: string[];
  source_repos?: string[];
  source_paths?: { repo: string; path: string; type: string }[];
  created?: string;
  log?: string[];
  confidence_basis?: string;
  summary?: string;
}

export interface GraphNode {
  id: string;
  title: string;
  domain: string;
  status: string;
  confidence: number;
  tags?: string[];
}

export interface GraphEdge {
  from: string;
  to: string;
  type: string;
}

export interface GraphData {
  nodes: Record<string, GraphNode>;
  edges: GraphEdge[];
  count: number;
}

export interface TagEntry {
  tag: string;
  count: number;
}

export interface TagData {
  total_tags: number;
  total_fdos: number;
  top_tags: TagEntry[];
  by_domain: Record<string, TagEntry[]>;
}

export interface VaultStats {
  total_fdos: number;
  domains: Record<string, number>;
  issues_count: number;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useVaultExplorer() {
  const [fdos, setFdos] = useState<FDOSummary[]>([]);
  const [searchResults, setSearchResults] = useState<FDOSummary[] | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [tagData, setTagData] = useState<TagData | null>(null);
  const [stats, setStats] = useState<VaultStats | null>(null);
  const [selectedFdo, setSelectedFdo] = useState<FDOFull | null>(null);

  const [domainFilter, setDomainFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const [loading, setLoading] = useState(true);
  const [graphLoading, setGraphLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";
  const abortRef = useRef<AbortController | null>(null);

  // ── Fetch FDO list ───────────────────────────────────────────────────────

  const fetchList = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (domainFilter) params.set("domain", domainFilter);
      const res = await fetch(`${apiBase}/api/vault/list?${params}`, {
        signal: ctrl.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setFdos(data.fdos || []);
      setSearchResults(null);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "Failed to load");
      }
    } finally {
      setLoading(false);
    }
  }, [apiBase, domainFilter]);

  // ── Search ───────────────────────────────────────────────────────────────

  const search = useCallback(
    async (query: string) => {
      if (!query.trim()) {
        setSearchResults(null);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `${apiBase}/api/vault/search?q=${encodeURIComponent(query)}&semantic=false`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        setSearchResults(data.results || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        setLoading(false);
      }
    },
    [apiBase]
  );

  // ── Fetch full graph ─────────────────────────────────────────────────────

  const fetchGraph = useCallback(
    async (scope?: string) => {
      setGraphLoading(true);
      try {
        const params = new URLSearchParams();
        if (scope && scope !== "all") params.set("scope", scope);
        const res = await fetch(`${apiBase}/api/vault/graph?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        setGraphData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Graph failed");
      } finally {
        setGraphLoading(false);
      }
    },
    [apiBase]
  );

  // ── Fetch tags ────────────────────────────────────────────────────────────

  const fetchTags = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/vault/tags`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setTagData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tags failed");
    }
  }, [apiBase]);

  // ── Fetch single FDO ─────────────────────────────────────────────────────

  const fetchFdo = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`${apiBase}/api/vault/${encodeURIComponent(id)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        setSelectedFdo(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load FDO");
      }
    },
    [apiBase]
  );

  // ── Update FDO ────────────────────────────────────────────────────────────

  const updateFdo = useCallback(
    async (id: string, fields: Record<string, unknown>) => {
      setSaving(true);
      try {
        const res = await fetch(`${apiBase}/api/vault/${encodeURIComponent(id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(fields),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        return data;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Update failed");
        return null;
      } finally {
        setSaving(false);
      }
    },
    [apiBase]
  );

  // ── Create FDO ────────────────────────────────────────────────────────────

  const createFdo = useCallback(
    async (args: Record<string, unknown>) => {
      setSaving(true);
      try {
        const res = await fetch(`${apiBase}/api/vault`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(args),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        return data;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Create failed");
        return null;
      } finally {
        setSaving(false);
      }
    },
    [apiBase]
  );

  // ── Fetch stats ───────────────────────────────────────────────────────────

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/vault/stats`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setStats(data);
    } catch {
      // Stats are optional — don't set error
    }
  }, [apiBase]);

  // ── Initial load ──────────────────────────────────────────────────────────

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  return {
    fdos,
    searchResults,
    graphData,
    tagData,
    stats,
    selectedFdo,
    domainFilter,
    setDomainFilter,
    statusFilter,
    setStatusFilter,
    loading,
    graphLoading,
    error,
    saving,
    fetchList,
    search,
    fetchGraph,
    fetchTags,
    fetchFdo,
    fetchStats,
    setSelectedFdo,
    updateFdo,
    createFdo,
    refresh: fetchList,
  };
}
