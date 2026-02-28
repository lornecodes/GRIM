"use client";

import { useState, useEffect, useCallback } from "react";
import type { TokenSummary, TokenDayEntry, TokenRecentEntry } from "@/lib/types";

const BRIDGE_URL =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_BRIDGE_URL ?? "http://localhost:8318"
    : "http://localhost:8318";

const POLL_INTERVAL = 30_000; // 30 seconds

interface UseBridgeApiReturn {
  summary: TokenSummary | null;
  byDay: TokenDayEntry[];
  recent: TokenRecentEntry[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useBridgeApi(): UseBridgeApiReturn {
  const [summary, setSummary] = useState<TokenSummary | null>(null);
  const [byDay, setByDay] = useState<TokenDayEntry[]>([]);
  const [recent, setRecent] = useState<TokenRecentEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [summaryRes, byDayRes, recentRes] = await Promise.all([
        fetch(`${BRIDGE_URL}/bridge/usage/summary?days=30`),
        fetch(`${BRIDGE_URL}/bridge/usage/by-day?days=30`),
        fetch(`${BRIDGE_URL}/bridge/usage/recent?limit=20`),
      ]);

      if (!summaryRes.ok || !byDayRes.ok || !recentRes.ok) {
        throw new Error("Bridge API returned an error");
      }

      const [summaryData, byDayData, recentData] = await Promise.all([
        summaryRes.json(),
        byDayRes.json(),
        recentRes.json(),
      ]);

      setSummary(summaryData);
      setByDay(byDayData);
      setRecent(recentData);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Failed to connect to AI Bridge"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchAll]);

  return { summary, byDay, recent, loading, error, refresh: fetchAll };
}
