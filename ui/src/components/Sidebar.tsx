"use client";

import type { Session } from "@/lib/types";

interface SidebarProps {
  sessions: Session[];
  activeId: string;
  onSwitch: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

function timeAgo(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function Sidebar({
  sessions,
  activeId,
  onSwitch,
  onNew,
  onDelete,
}: SidebarProps) {
  return (
    <div className="w-64 border-r border-grim-border bg-grim-surface flex flex-col shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-grim-border">
        <span className="text-xs font-semibold text-grim-text-dim uppercase tracking-wider">
          Sessions
        </span>
        <button
          onClick={onNew}
          className="text-xs text-grim-accent hover:text-grim-accent-dim transition-colors px-2 py-0.5 rounded border border-grim-accent/30 hover:bg-grim-accent/10"
        >
          + new
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto py-1">
        {sessions.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-grim-text-dim">
            No sessions yet.
            <br />
            Start chatting to create one.
          </div>
        )}
        {sessions
          .sort((a, b) => b.updatedAt - a.updatedAt)
          .map((session) => (
            <div
              key={session.id}
              onClick={() => onSwitch(session.id)}
              className={`group flex items-start gap-2 px-3 py-2.5 cursor-pointer transition-colors ${
                session.id === activeId
                  ? "bg-grim-accent/10 border-r-2 border-grim-accent"
                  : "hover:bg-grim-surface-hover"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="text-xs text-grim-text truncate">
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
                className="opacity-0 group-hover:opacity-100 text-grim-text-dim hover:text-grim-error text-xs transition-opacity px-1"
                title="Delete session"
              >
                x
              </button>
            </div>
          ))}
      </div>
    </div>
  );
}
