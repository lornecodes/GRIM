"use client";

import { useRef, useCallback, useEffect, useState } from "react";
import { useGrimStore } from "@/store";
import { useGrimSocket } from "@/hooks/useGrimSocket";
import { useSessions } from "@/hooks/useSessions";
import { ChatArea } from "./ChatArea";
import { ChatPanelHeader } from "./ChatPanelHeader";

const MIN_WIDTH = 280;
const MAX_WIDTH = 800;
const DEFAULT_WIDTH = 400;

export function ChatPanel() {
  const chatPanelOpen = useGrimStore((s) => s.chatPanelOpen);
  const activeSessionId = useGrimStore((s) => s.activeSessionId);

  const { sessions, activeId, newSession, switchSession, deleteSession } =
    useSessions();
  const { send } = useGrimSocket(activeSessionId);

  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(DEFAULT_WIDTH);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startWidth.current = panelWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [panelWidth]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      // Panel is on the right, so dragging left = larger
      const delta = startX.current - e.clientX;
      const newW = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setPanelWidth(newW);
    };
    const onMouseUp = () => {
      if (dragging.current) {
        dragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  return (
    <div
      className={`${
        chatPanelOpen ? "" : "w-0 overflow-hidden"
      } border-l border-grim-border bg-grim-surface flex flex-col shrink-0 transition-[width] duration-200 relative`}
      style={chatPanelOpen ? { width: panelWidth } : undefined}
    >
      {/* Resize handle */}
      {chatPanelOpen && (
        <div
          onMouseDown={onMouseDown}
          className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-grim-accent/30 active:bg-grim-accent/50 z-10 transition-colors"
        />
      )}
      {chatPanelOpen && (
        <>
          <ChatPanelHeader
            sessions={sessions}
            activeId={activeId}
            onSwitch={switchSession}
            onNew={newSession}
            onDelete={deleteSession}
          />
          <ChatArea onSend={send} />
        </>
      )}
    </div>
  );
}
