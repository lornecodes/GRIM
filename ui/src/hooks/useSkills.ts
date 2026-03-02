"use client";

import { useState, useEffect, useCallback } from "react";

export interface SkillData {
  name: string;
  version: string;
  description: string;
  type: string;
  permissions: string[];
  phases: string[];
  enabled: boolean;
}

interface SkillsResponse {
  skills: SkillData[];
  total: number;
  disabled_count: number;
}

export function useSkills() {
  const [skills, setSkills] = useState<SkillData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const fetchSkills = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/skills");
      if (!res.ok) throw new Error(`Failed to fetch skills: ${res.status}`);
      const data: SkillsResponse = await res.json();
      setSkills(data.skills);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleSkill = useCallback(async (name: string) => {
    setToggling(name);
    try {
      const res = await fetch(`/api/skills/${name}/toggle`, { method: "POST" });
      if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
      const data = await res.json();
      setSkills((prev) =>
        prev.map((s) => (s.name === name ? { ...s, enabled: data.enabled } : s))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    } finally {
      setToggling(null);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  return { skills, loading, error, toggling, toggleSkill, refresh: fetchSkills };
}
