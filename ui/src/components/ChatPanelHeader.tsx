"use client";

import { useState } from "react";
import type { Session } from "@/lib/types";
import { timeAgo } from "@/lib/format";

interface ChatPanelHeaderProps {
  sessions: Session[];
  activeId: string;
  onSwitch: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export function ChatPanelHeader({
  sessions,
  activeId,
  onSwitch,
  onNew,
  onDelete,
}: ChatPanelHeaderProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const activeSession = sessions.find((s) => s.id === activeId);
  const title = activeSession?.title ?? "New Session";

  return (
    <div className="flex items-center justify-between px-3 py-2 border-b border-grim-border shrink-0 relative">
      <button
        onClick={() => setDropdownOpen(!dropdownOpen)}
        className="flex items-center gap-1.5 text-xs text-grim-text hover:text-grim-accent transition-colors min-w-0"
      >
        <span className="truncate max-w-[220px]">{title}</span>
        <svg
          className={`w-3 h-3 shrink-0 transition-transform ${dropdownOpen ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      <button
        onClick={() => {
          onNew();
          setDropdownOpen(false);
        }}
        className="text-xs text-grim-accent hover:text-grim-accent-dim transition-colors px-2 py-0.5 rounded border border-grim-accent/30 hover:bg-grim-accent/10 shrink-0"
      >
        + new
      </button>

      {/* Session dropdown */}
      {dropdownOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setDropdownOpen(false)}
          />
          <div className="absolute top-full left-0 right-0 z-50 bg-grim-surface border border-grim-border rounded-b-lg shadow-xl max-h-64 overflow-y-auto">
            {sessions.length === 0 && (
              <div className="px-3 py-4 text-center text-[11px] text-grim-text-dim">
                No saved sessions yet.
              </div>
            )}
            {sessions
              .sort((a, b) => b.updatedAt - a.updatedAt)
              .map((session) => (
                <div
                  key={session.id}
                  onClick={() => {
                    onSwitch(session.id);
                    setDropdownOpen(false);
                  }}
                  className={`group flex items-start gap-2 px-3 py-2 cursor-pointer transition-colors ${
                    session.id === activeId
                      ? "bg-grim-accent/10 border-l-2 border-grim-accent"
                      : "hover:bg-grim-surface-hover border-l-2 border-transparent"
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-grim-text truncate">
                      {session.title}
                    </div>
                    <div className="text-[10px] text-grim-text-dim mt-0.5 flex items-center gap-2">
                      <span>{session.id}</span>
                      <span>{timeAgo(session.updatedAt)}</span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(session.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 text-grim-text-dim hover:text-grim-error text-[11px] transition-opacity px-1"
                    title="Delete session"
                  >
                    x
                  </button>
                </div>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
