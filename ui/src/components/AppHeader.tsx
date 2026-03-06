"use client";

import { useGrimStore } from "@/store";
import { StatusDot } from "./StatusDot";
import { GrimSprite } from "./GrimSprite";
import { GRIM_VERSION } from "@/config/version";

export function AppHeader() {
  const wsStatus = useGrimStore((s) => s.wsStatus);
  const activeSessionId = useGrimStore((s) => s.activeSessionId);
  const chatPanelOpen = useGrimStore((s) => s.chatPanelOpen);
  const toggleChatPanel = useGrimStore((s) => s.toggleChatPanel);

  return (
    <header className="flex items-center justify-between px-5 py-2.5 border-b border-grim-border bg-grim-surface shrink-0">
      <div className="flex items-center gap-2.5">
        <GrimSprite size="sm" />
        <h1 className="text-[15px] font-semibold tracking-[2px]">GRIM</h1>
        <span className="text-[9px] text-grim-accent/60 font-mono ml-0.5">v{GRIM_VERSION}</span>
        <span className="text-[10px] text-grim-text-dim ml-1">Agent Companion</span>
      </div>
      <div className="flex items-center gap-4">
        <StatusDot status={wsStatus} sessionId={activeSessionId} />
        <button
          onClick={toggleChatPanel}
          className={`bg-transparent border border-grim-border rounded-md px-2.5 py-1 text-[11px] cursor-pointer transition-all ${
            chatPanelOpen
              ? "border-grim-accent text-grim-accent bg-grim-accent/10"
              : "text-grim-text-dim hover:border-grim-accent hover:text-grim-accent"
          }`}
        >
          chat
        </button>
      </div>
    </header>
  );
}
