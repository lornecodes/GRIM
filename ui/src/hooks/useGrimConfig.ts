"use client";

import { useState, useEffect, useCallback } from "react";

export interface GrimConfigData {
  env: string;
  vault_path: string;
  model: string;
  temperature: number;
  max_tokens: number;
  routing: {
    enabled: boolean;
    default_tier: string;
    classifier_enabled: boolean;
    confidence_threshold: number;
  };
  context: {
    max_tokens: number;
    keep_recent: number;
  };
  identity: {
    system_prompt_path: string;
    personality_path: string;
    personality_cache_path: string;
    skills_path: string;
  };
  skills: {
    auto_load: boolean;
    match_per_turn: boolean;
  };
  persistence: {
    checkpoint_backend: string;
    checkpoint_path: string;
  };
  evolution: {
    frequency: string;
    directory: string;
  };
  objectives_max_active: number;
  redis_url: boolean;
}

interface UseGrimConfigReturn {
  config: GrimConfigData | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  saveConfig: (updates: Partial<GrimConfigData>) => Promise<void>;
  refresh: () => void;
}

export function useGrimConfig(): UseGrimConfigReturn {
  const [config, setConfig] = useState<GrimConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchConfig = useCallback(async () => {
    try {
      setLoading(true);
      const resp = await fetch(`${apiBase}/api/config`);
      if (!resp.ok) throw new Error(`Config API returned ${resp.status}`);
      const data = await resp.json();
      setConfig(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const saveConfig = useCallback(
    async (updates: Partial<GrimConfigData>) => {
      try {
        setSaving(true);
        const resp = await fetch(`${apiBase}/api/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updates),
        });
        if (!resp.ok) throw new Error(`Save failed: ${resp.status}`);
        const data = await resp.json();
        setConfig(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save config");
      } finally {
        setSaving(false);
      }
    },
    [apiBase]
  );

  return { config, loading, error, saving, saveConfig, refresh: fetchConfig };
}
