"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import type { ChatMessage, TraceEvent, ResponseMeta } from "@/lib/types";
import { saveMessages, loadMessages } from "@/lib/persistence";
import { useGrimSocket } from "@/hooks/useGrimSocket";
import { useSessions } from "@/hooks/useSessions";
import { Sidebar } from "./Sidebar";
import { ChatArea } from "./ChatArea";
import { StatusDot } from "./StatusDot";

export function ChatApp() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const { sessions, activeId, updateSession, newSession, switchSession, deleteSession } =
    useSessions();

  // Track current turn for trace accumulation
  const currentResponseId = useRef<string>("");
  const currentTraces = useRef<TraceEvent[]>([]);
  const firstMessageSent = useRef(false);

  // Persist messages when a turn completes (not during streaming)
  useEffect(() => {
    if (activeId && !isStreaming && messages.length > 0) {
      saveMessages(activeId, messages);
    }
  }, [messages, isStreaming, activeId]);

  // ── WebSocket callbacks ──

  const handleTrace = useCallback((trace: TraceEvent) => {
    const id = currentResponseId.current;
    if (!id) return;
    currentTraces.current.push(trace);
    const traces = [...currentTraces.current];
    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === id);
      if (idx === -1) return prev;
      const updated = [...prev];
      updated[idx] = { ...updated[idx], traces };
      return updated;
    });
  }, []);

  const handleStream = useCallback((token: string) => {
    const id = currentResponseId.current;
    if (!id) return;
    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === id);
      if (idx === -1) return prev;
      const updated = [...prev];
      updated[idx] = {
        ...updated[idx],
        content: updated[idx].content + token,
      };
      return updated;
    });
  }, []);

  const handleResponse = useCallback(
    (content: string, meta: ResponseMeta) => {
      const id = currentResponseId.current;
      if (!id) return;
      const traces = [...currentTraces.current];
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id
            ? { ...m, content, meta, traces, streaming: false }
            : m
        )
      );
      setIsStreaming(false);
      currentResponseId.current = "";
    },
    []
  );

  const handleError = useCallback((message: string) => {
    const id = currentResponseId.current;
    if (id) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id
            ? { ...m, content: message, streaming: false, error: true }
            : m
        )
      );
    } else {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "grim",
          content: message,
          traces: [],
          error: true,
        },
      ]);
    }
    setIsStreaming(false);
    currentResponseId.current = "";
  }, []);

  const { status, send } = useGrimSocket({
    sessionId: activeId,
    onTrace: handleTrace,
    onStream: handleStream,
    onResponse: handleResponse,
    onError: handleError,
  });

  // ── Send message ──

  const handleSend = useCallback(
    (text: string) => {
      if (!text.trim() || isStreaming) return;

      const userId = crypto.randomUUID();
      const responseId = crypto.randomUUID();
      currentResponseId.current = responseId;
      currentTraces.current = [];

      setMessages((prev) => [
        ...prev,
        { id: userId, role: "user", content: text, traces: [] },
        {
          id: responseId,
          role: "grim",
          content: "",
          traces: [],
          streaming: true,
        },
      ]);

      setIsStreaming(true);
      send(text);

      // Update session title with first message
      if (!firstMessageSent.current) {
        firstMessageSent.current = true;
        const title =
          text.length > 50 ? text.slice(0, 50) + "..." : text;
        updateSession(activeId, title);
      }
    },
    [isStreaming, send, activeId, updateSession]
  );

  // ── Session management ──

  const handleNewSession = useCallback(() => {
    // Save current session before switching
    if (activeId && messages.length > 0) {
      saveMessages(activeId, messages);
    }
    newSession();
    setMessages([]);
    firstMessageSent.current = false;
  }, [newSession, activeId, messages]);

  const handleSwitchSession = useCallback(
    (id: string) => {
      // Save current session before switching
      if (activeId && messages.length > 0) {
        saveMessages(activeId, messages);
      }
      switchSession(id);
      // Load target session's messages
      const restored = loadMessages(id);
      setMessages(restored);
      firstMessageSent.current = restored.length > 0;
    },
    [switchSession, activeId, messages]
  );

  return (
    <div className="h-screen flex flex-col bg-grim-bg font-mono">
      {/* Header */}
      <header className="flex items-center justify-between px-5 py-2.5 border-b border-grim-border bg-grim-surface shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-md bg-gradient-to-br from-grim-accent to-grim-accent-dim flex items-center justify-center text-white text-sm font-bold">
            G
          </div>
          <h1 className="text-[15px] font-semibold tracking-[2px]">GRIM</h1>
        </div>
        <div className="flex items-center gap-4">
          <StatusDot status={status} sessionId={activeId} />
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className={`bg-transparent border border-grim-border rounded-md px-2.5 py-1 text-[11px] cursor-pointer transition-all ${
              sidebarOpen
                ? "border-grim-accent text-grim-accent bg-grim-accent/10"
                : "text-grim-text-dim hover:border-grim-accent hover:text-grim-accent"
            }`}
          >
            sessions
          </button>
        </div>
      </header>

      {/* Main */}
      <div className="flex-1 flex overflow-hidden">
        {sidebarOpen && (
          <Sidebar
            sessions={sessions}
            activeId={activeId}
            onSwitch={handleSwitchSession}
            onNew={handleNewSession}
            onDelete={deleteSession}
          />
        )}
        <ChatArea
          messages={messages}
          isStreaming={isStreaming}
          onSend={handleSend}
          connected={status === "connected"}
        />
      </div>
    </div>
  );
}
