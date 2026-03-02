"use client";

import { useState, useEffect, useCallback } from "react";

export interface ModelData {
  id: string;
  name: string;
  tier: string;
  context_window: number;
  max_output: number;
  enabled: boolean;
  is_default: boolean;
}

export interface RoutingConfig {
  enabled: boolean;
  default_tier: string;
  classifier_enabled: boolean;
  confidence_threshold: number;
}

interface ModelsResponse {
  provider: string;
  models: ModelData[];
  routing: RoutingConfig;
}

export function useModels() {
  const [models, setModels] = useState<ModelData[]>([]);
  const [routing, setRouting] = useState<RoutingConfig | null>(null);
  const [provider, setProvider] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/models");
      if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
      const data: ModelsResponse = await res.json();
      setModels(data.models);
      setRouting(data.routing);
      setProvider(data.provider);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load models");
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleModel = useCallback(async (tier: string) => {
    setToggling(tier);
    try {
      const res = await fetch(`/api/models/${tier}/toggle`, { method: "POST" });
      if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
      const data = await res.json();
      setModels((prev) =>
        prev.map((m) => (m.tier === tier ? { ...m, enabled: data.enabled } : m))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    } finally {
      setToggling(null);
    }
  }, []);

  const updateRouting = useCallback(async (updates: Partial<RoutingConfig>) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ routing: updates }),
      });
      if (!res.ok) throw new Error(`Update failed: ${res.status}`);
      await fetchModels();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }, [fetchModels]);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  return { models, routing, provider, loading, error, toggling, toggleModel, updateRouting, refresh: fetchModels };
}
