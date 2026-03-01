"use client";

import { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CalendarEntry {
  // Work schedule entries
  story_id?: string;
  title: string;
  feature?: string;
  project?: string;
  start_date?: string;
  end_date?: string;
  estimate_days?: number;
  status?: string;
  // Personal event entries
  id?: string;
  date?: string;
  time?: string;
  duration_hours?: number;
  recurring?: boolean;
  notes?: string;
  type?: "work" | "personal";
}

export interface CalendarData {
  entries: CalendarEntry[];
  range?: { start: string; end: string };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useCalendar(startDate: string, endDate: string) {
  const [calendar, setCalendar] = useState<CalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchCalendar = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(
        `${apiBase}/api/calendar?start_date=${startDate}&end_date=${endDate}`
      );
      if (!res.ok) throw new Error(`Calendar fetch failed: ${res.status}`);
      const data = await res.json();
      setCalendar(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  }, [apiBase, startDate, endDate]);

  const addEvent = useCallback(async (args: {
    title: string;
    date: string;
    time?: string;
    duration_hours?: number;
    notes?: string;
  }) => {
    try {
      const res = await fetch(`${apiBase}/api/calendar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
      if (!res.ok) throw new Error(`Add event failed: ${res.status}`);
      await fetchCalendar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Add event failed");
    }
  }, [apiBase, fetchCalendar]);

  const syncSchedule = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/calendar/sync`, { method: "POST" });
      if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
      await fetchCalendar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    }
  }, [apiBase, fetchCalendar]);

  useEffect(() => {
    fetchCalendar();
  }, [fetchCalendar]);

  return { calendar, loading, error, addEvent, syncSchedule, refresh: fetchCalendar };
}
