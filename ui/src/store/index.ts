"use client";

import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { ChatMessage, Session, ConnectionStatus, UICommand } from "@/lib/types";

// ── Chat slice ──

interface ChatSlice {
  messages: ChatMessage[];
  isStreaming: boolean;
  wsStatus: ConnectionStatus;
  sessions: Session[];
  activeSessionId: string;
  // Actions
  setMessages: (msgs: ChatMessage[]) => void;
  appendMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  deleteMessage: (id: string) => void;
  setStreaming: (v: boolean) => void;
  setWsStatus: (s: ConnectionStatus) => void;
  setSessions: (s: Session[]) => void;
  setActiveSessionId: (id: string) => void;
  upsertSession: (id: string, title: string) => void;
  deleteSessionById: (id: string) => void;
}

// ── UI slice ──

type UICommandHandler = (cmd: UICommand) => void;

interface UISlice {
  chatPanelOpen: boolean;
  activeDashboardWidget: string;
  _commandHandlers: Set<UICommandHandler>;
  // Actions
  setChatPanelOpen: (v: boolean) => void;
  toggleChatPanel: () => void;
  setActiveDashboardWidget: (name: string) => void;
  subscribeUICommand: (handler: UICommandHandler) => () => void;
  dispatchUICommand: (cmd: UICommand) => void;
}

export type GrimStore = ChatSlice & UISlice;

export const useGrimStore = create<GrimStore>()(
  devtools(
    (set, get) => ({
      // ── Chat state ──
      messages: [],
      isStreaming: false,
      wsStatus: "disconnected" as ConnectionStatus,
      sessions: [],
      activeSessionId: "",

      setMessages: (messages) => set({ messages }),
      appendMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
      updateMessage: (id, patch) =>
        set((s) => ({
          messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
        })),
      deleteMessage: (id) =>
        set((s) => ({
          messages: s.messages.filter((m) => m.id !== id),
        })),
      setStreaming: (isStreaming) => set({ isStreaming }),
      setWsStatus: (wsStatus) => set({ wsStatus }),
      setSessions: (sessions) => set({ sessions }),
      setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
      upsertSession: (id, title) =>
        set((s) => {
          const existing = s.sessions.find((x) => x.id === id);
          if (existing) {
            return {
              sessions: s.sessions.map((x) =>
                x.id === id ? { ...x, title, updatedAt: Date.now() } : x
              ),
            };
          }
          return {
            sessions: [{ id, title, updatedAt: Date.now() }, ...s.sessions],
          };
        }),
      deleteSessionById: (id) =>
        set((s) => ({
          sessions: s.sessions.filter((x) => x.id !== id),
        })),

      // ── UI state ──
      chatPanelOpen: true,
      activeDashboardWidget: "tokens",
      _commandHandlers: new Set(),

      setChatPanelOpen: (chatPanelOpen) => set({ chatPanelOpen }),
      toggleChatPanel: () => set((s) => ({ chatPanelOpen: !s.chatPanelOpen })),
      setActiveDashboardWidget: (activeDashboardWidget) =>
        set({ activeDashboardWidget }),

      subscribeUICommand: (handler) => {
        get()._commandHandlers.add(handler);
        return () => {
          get()._commandHandlers.delete(handler);
        };
      },
      dispatchUICommand: (cmd) => {
        get()._commandHandlers.forEach((h) => h(cmd));
        switch (cmd.command) {
          case "open_chat":
            set({ chatPanelOpen: true });
            break;
          case "close_chat":
            set({ chatPanelOpen: false });
            break;
          case "navigate_dashboard":
            if (cmd.payload?.widget && typeof cmd.payload.widget === "string") {
              set({ activeDashboardWidget: cmd.payload.widget });
            }
            break;
        }
      },
    }),
    { name: "grim-store" }
  )
);
