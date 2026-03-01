"use client";

import { useState, useEffect, useCallback } from "react";

export interface GrimMemoryData {
  content: string;
  sections: Record<string, string>;
}

interface UseGrimMemoryReturn {
  memory: GrimMemoryData | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  save: (content: string) => Promise<void>;
  saving: boolean;
}

export function useGrimMemory(): UseGrimMemoryReturn {
  const [memory, setMemory] = useState<GrimMemoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchMemory = useCallback(async () => {
    try {
      setLoading(true);
      const resp = await fetch(`${apiBase}/api/memory`);
      if (!resp.ok) throw new Error(`Memory API returned ${resp.status}`);
      const data = await resp.json();
      setMemory(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchMemory();
  }, [fetchMemory]);

  const save = useCallback(
    async (content: string) => {
      try {
        setSaving(true);
        const resp = await fetch(`${apiBase}/api/memory`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        if (!resp.ok) throw new Error(`Save failed: ${resp.status}`);
        const data = await resp.json();
        setMemory(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save memory");
      } finally {
        setSaving(false);
      }
    },
    [apiBase]
  );

  return { memory, loading, error, refresh: fetchMemory, save, saving };
}
