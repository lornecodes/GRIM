"use client";

import { useEffect, useCallback } from "react";
import { useGrimStore } from "@/store";
import { saveMessages, loadMessages, deleteMessages } from "@/lib/persistence";
import { uuid } from "@/lib/uuid";

const STORAGE_KEY = "grim-sessions";

function generateId(): string {
  return uuid().slice(0, 8);
}

export function useSessions() {
  const store = useGrimStore();

  // Load sessions from localStorage on mount + resume most recent or start fresh
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const sessions = JSON.parse(stored);
        store.setSessions(sessions);

        // Resume the most recent session if it has messages
        if (Array.isArray(sessions) && sessions.length > 0) {
          const sorted = [...sessions].sort(
            (a: { updatedAt?: number }, b: { updatedAt?: number }) =>
              (b.updatedAt || 0) - (a.updatedAt || 0)
          );
          const recent = sorted[0];
          if (recent?.id) {
            const msgs = loadMessages(recent.id);
            if (msgs.length > 0) {
              store.setActiveSessionId(recent.id);
              store.setMessages(msgs);
              return; // resumed — skip generating a new session
            }
          }
        }
      }
    } catch {
      // Ignore corrupted storage
    }
    store.setActiveSessionId(generateId());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist sessions to localStorage
  useEffect(() => {
    if (store.sessions.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store.sessions));
    }
  }, [store.sessions]);

  // Auto-save messages when streaming completes
  useEffect(() => {
    if (store.activeSessionId && !store.isStreaming && store.messages.length > 0) {
      saveMessages(store.activeSessionId, store.messages);
    }
  }, [store.messages, store.isStreaming, store.activeSessionId]);

  const newSession = useCallback(() => {
    if (store.activeSessionId && store.messages.length > 0) {
      saveMessages(store.activeSessionId, store.messages);
    }
    const id = generateId();
    store.setActiveSessionId(id);
    store.setMessages([]);
    return id;
  }, [store]);

  const switchSession = useCallback(
    (id: string) => {
      if (store.activeSessionId && store.messages.length > 0) {
        saveMessages(store.activeSessionId, store.messages);
      }
      store.setActiveSessionId(id);
      const restored = loadMessages(id);
      store.setMessages(restored);
    },
    [store]
  );

  const deleteSession = useCallback(
    (id: string) => {
      store.deleteSessionById(id);
      deleteMessages(id);
      if (id === store.activeSessionId) {
        const newId = generateId();
        store.setActiveSessionId(newId);
        store.setMessages([]);
      }
    },
    [store]
  );

  return {
    sessions: store.sessions,
    activeId: store.activeSessionId,
    updateSession: store.upsertSession,
    newSession,
    switchSession,
    deleteSession,
  };
}
