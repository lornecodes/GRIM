"use client";

import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { ChatMessage, Session, ConnectionStatus, UICommand } from "@/lib/types";
import type { PoolJob, PoolMetrics, PoolStatus, TranscriptEntry } from "@/lib/poolTypes";

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

type DashboardTab = "overview" | "pool";
type AgentsTab = "team" | "jobs" | "studio" | "graph";

interface UISlice {
  chatPanelOpen: boolean;
  activePage: string;
  sidebarCollapsed: boolean;
  dashboardTab: DashboardTab;
  agentsTab: AgentsTab;
  _commandHandlers: Set<UICommandHandler>;
  // Actions
  setChatPanelOpen: (v: boolean) => void;
  toggleChatPanel: () => void;
  setActivePage: (id: string) => void;
  toggleSidebar: () => void;
  setDashboardTab: (tab: DashboardTab) => void;
  setAgentsTab: (tab: AgentsTab) => void;
  subscribeUICommand: (handler: UICommandHandler) => () => void;
  dispatchUICommand: (cmd: UICommand) => void;
}

// ── Pool slice ──

interface PoolSlice {
  poolStatus: PoolStatus | null;
  poolEnabled: boolean;
  poolMetrics: PoolMetrics | null;
  poolJobs: PoolJob[];
  liveTranscripts: Record<string, TranscriptEntry[]>;
  selectedJobId: string | null;
  // Actions
  setPoolStatus: (s: PoolStatus | null) => void;
  setPoolEnabled: (v: boolean) => void;
  setPoolMetrics: (m: PoolMetrics | null) => void;
  setPoolJobs: (jobs: PoolJob[]) => void;
  upsertPoolJob: (job: Partial<PoolJob> & { id: string }) => void;
  appendTranscriptEntry: (jobId: string, entry: TranscriptEntry) => void;
  clearTranscript: (jobId: string) => void;
  setSelectedJobId: (id: string | null) => void;
  navigateToJob: (jobId: string) => void;
}

export type GrimStore = ChatSlice & UISlice & PoolSlice;

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
      activePage: "dashboard",
      sidebarCollapsed: false,
      dashboardTab: "overview" as DashboardTab,
      agentsTab: "team" as AgentsTab,
      _commandHandlers: new Set(),

      setChatPanelOpen: (chatPanelOpen) => set({ chatPanelOpen }),
      toggleChatPanel: () => set((s) => ({ chatPanelOpen: !s.chatPanelOpen })),
      setActivePage: (activePage) => set({ activePage }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setDashboardTab: (dashboardTab) => set({ dashboardTab }),
      setAgentsTab: (agentsTab) => set({ agentsTab }),

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
              set({ activePage: cmd.payload.widget });
            }
            break;
        }
      },
      // ── Pool state ──
      poolStatus: null,
      poolEnabled: true,
      poolMetrics: null,
      poolJobs: [],
      liveTranscripts: {},
      selectedJobId: null,

      setPoolStatus: (poolStatus) => set({ poolStatus }),
      setPoolEnabled: (poolEnabled) => set({ poolEnabled }),
      setPoolMetrics: (poolMetrics) => set({ poolMetrics }),
      setPoolJobs: (poolJobs) => set({ poolJobs }),
      upsertPoolJob: (patch) =>
        set((s) => {
          const idx = s.poolJobs.findIndex((j) => j.id === patch.id);
          if (idx >= 0) {
            const updated = [...s.poolJobs];
            updated[idx] = { ...updated[idx], ...patch };
            return { poolJobs: updated };
          }
          // New job — add to front (requires full PoolJob; ignore partial inserts)
          return s;
        }),
      appendTranscriptEntry: (jobId, entry) =>
        set((s) => ({
          liveTranscripts: {
            ...s.liveTranscripts,
            [jobId]: [...(s.liveTranscripts[jobId] || []), entry],
          },
        })),
      clearTranscript: (jobId) =>
        set((s) => {
          const { [jobId]: _, ...rest } = s.liveTranscripts;
          return { liveTranscripts: rest };
        }),
      setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
      navigateToJob: (jobId) => set({ activePage: "agents", agentsTab: "studio" as AgentsTab, selectedJobId: jobId }),
    }),
    { name: "grim-store" }
  )
);
