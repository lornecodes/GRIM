"use client";

import { useState, useEffect, useCallback } from "react";
import type { Session } from "@/lib/types";

const STORAGE_KEY = "grim-sessions";

function generateId(): string {
  return crypto.randomUUID().slice(0, 8);
}

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string>("");

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) setSessions(JSON.parse(stored));
    } catch {
      // Ignore corrupted storage
    }
    setActiveId(generateId());
  }, []);

  // Persist to localStorage
  useEffect(() => {
    if (sessions.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    }
  }, [sessions]);

  const updateSession = useCallback((id: string, title: string) => {
    setSessions((prev) => {
      const existing = prev.find((s) => s.id === id);
      if (existing) {
        return prev.map((s) =>
          s.id === id ? { ...s, title, updatedAt: Date.now() } : s
        );
      }
      return [{ id, title, updatedAt: Date.now() }, ...prev];
    });
  }, []);

  const newSession = useCallback(() => {
    const id = generateId();
    setActiveId(id);
    return id;
  }, []);

  const switchSession = useCallback((id: string) => {
    setActiveId(id);
  }, []);

  const deleteSession = useCallback(
    (id: string) => {
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (id === activeId) {
        const newId = generateId();
        setActiveId(newId);
      }
    },
    [activeId]
  );

  return { sessions, activeId, updateSession, newSession, switchSession, deleteSession };
}
