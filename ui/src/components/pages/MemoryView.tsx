"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { IconMemory } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useGrimMemory } from "@/hooks/useGrimMemory";
import { useGrimStore } from "@/store";
import { loadMessages } from "@/lib/persistence";
import type { ChatMessage, Session } from "@/lib/types";

const STORAGE_KEY = "grim-sessions";

// ---------------------------------------------------------------------------
// Memory View — two tabs: Working Memory + Session History
// ---------------------------------------------------------------------------

export function MemoryView() {
  const [tab, setTab] = useState<"memory" | "sessions">("memory");

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconMemory size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">Memory</h2>
          <p className="text-xs text-grim-text-dim">
            Persistent working memory &amp; session history
          </p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 bg-grim-bg border border-grim-border rounded-lg p-1">
        <TabButton active={tab === "memory"} onClick={() => setTab("memory")}>
          Working Memory
        </TabButton>
        <TabButton active={tab === "sessions"} onClick={() => setTab("sessions")}>
          Session History
        </TabButton>
      </div>

      {tab === "memory" ? <WorkingMemoryTab /> : <SessionHistoryTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 text-xs font-medium py-1.5 px-3 rounded-md transition-colors ${
        active
          ? "bg-grim-surface text-grim-text"
          : "text-grim-text-dim hover:text-grim-text"
      }`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tab 1: Working Memory — rendered sections + raw editor
// ---------------------------------------------------------------------------

function WorkingMemoryTab() {
  const { memory, loading, error, refresh, save, saving } = useGrimMemory();
  const [editMode, setEditMode] = useState(false);
  const [editContent, setEditContent] = useState("");

  useEffect(() => {
    if (memory?.content) setEditContent(memory.content);
  }, [memory?.content]);

  const handleSave = async () => {
    await save(editContent);
    setEditMode(false);
  };

  if (loading) {
    return (
      <div className="text-xs text-grim-text-dim py-12 text-center">
        Loading working memory...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-xs text-red-400 py-12 text-center">
        {error}
        <button onClick={refresh} className="ml-2 text-grim-accent hover:underline">
          retry
        </button>
      </div>
    );
  }

  const sections = memory?.sections || {};
  const isEmpty = Object.keys(sections).length === 0;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
        <button
          onClick={() => setEditMode(!editMode)}
          className={`text-[10px] px-2 py-1 rounded border transition-colors ${
            editMode
              ? "bg-grim-accent/15 border-grim-accent/30 text-grim-accent"
              : "bg-grim-surface border-grim-border text-grim-text-dim hover:text-grim-text"
          }`}
        >
          {editMode ? "View Mode" : "Edit Raw"}
        </button>
        {editMode && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="text-[10px] px-2 py-1 rounded bg-grim-accent/20 border border-grim-accent/30 text-grim-accent hover:bg-grim-accent/30 transition-colors disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        )}
      </div>

      {editMode ? (
        /* Raw markdown editor */
        <div className="bg-grim-bg border border-grim-border rounded-lg overflow-hidden">
          <div className="px-3 py-1.5 border-b border-grim-border/50 text-[10px] text-grim-text-dim font-mono">
            kronos-vault/memory.md
          </div>
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full bg-transparent text-xs text-grim-text font-mono p-3 outline-none resize-none min-h-[400px] leading-relaxed"
            spellCheck={false}
          />
        </div>
      ) : isEmpty ? (
        <div className="text-xs text-grim-text-dim py-12 text-center">
          Working memory is empty. GRIM will populate it as you interact.
        </div>
      ) : (
        /* Rendered section cards */
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Active Objectives — full width, prominent */}
          {sections["Active Objectives"] && (
            <div className="md:col-span-2">
              <MemorySection
                title="Active Objectives"
                content={sections["Active Objectives"]}
                icon="target"
                accentColor="#34d399"
              />
            </div>
          )}

          {/* Recent Topics */}
          {sections["Recent Topics"] && (
            <MemorySection
              title="Recent Topics"
              content={sections["Recent Topics"]}
              icon="clock"
              accentColor="#3b82f6"
            />
          )}

          {/* Future Goals */}
          {sections["Future Goals"] && (
            <MemorySection
              title="Future Goals"
              content={sections["Future Goals"]}
              icon="rocket"
              accentColor="#8b5cf6"
            />
          )}

          {/* User Preferences */}
          {sections["User Preferences"] && (
            <MemorySection
              title="User Preferences"
              content={sections["User Preferences"]}
              icon="user"
              accentColor="#f59e0b"
            />
          )}

          {/* Key Learnings */}
          {sections["Key Learnings"] && (
            <MemorySection
              title="Key Learnings"
              content={sections["Key Learnings"]}
              icon="lightbulb"
              accentColor="#ec4899"
            />
          )}

          {/* Session Notes — full width */}
          {sections["Session Notes"] && (
            <div className="md:col-span-2">
              <MemorySection
                title="Session Notes"
                content={sections["Session Notes"]}
                icon="notes"
                accentColor="#6b7280"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Memory Section Card
// ---------------------------------------------------------------------------

const SECTION_ICONS: Record<string, string> = {
  target: "\u25C9",   // ◉
  clock: "\u25F7",    // ◷
  rocket: "\u2B06",   // ⬆
  user: "\u2B24",     // ⬤
  lightbulb: "\u2605", // ★
  notes: "\u2630",    // ☰
};

function MemorySection({
  title,
  content,
  icon,
  accentColor,
}: {
  title: string;
  content: string;
  icon: string;
  accentColor: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const lines = content.split("\n").filter((l) => l.trim());
  const bulletItems = lines.filter((l) => l.trim().startsWith("-"));
  const nonBulletContent = lines.filter((l) => !l.trim().startsWith("-")).join("\n");

  return (
    <div
      className="bg-grim-surface border border-grim-border rounded-lg overflow-hidden"
      style={{ borderLeftWidth: 3, borderLeftColor: accentColor }}
    >
      {/* Section header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-grim-surface-hover transition-colors"
      >
        <span className="text-xs" style={{ color: accentColor }}>
          {SECTION_ICONS[icon] || "\u25CF"}
        </span>
        <span className="text-xs font-semibold text-grim-text flex-1 text-left">
          {title}
        </span>
        {bulletItems.length > 0 && (
          <span className="text-[10px] text-grim-text-dim tabular-nums">
            {bulletItems.length} item{bulletItems.length !== 1 ? "s" : ""}
          </span>
        )}
        <span className="text-[10px] text-grim-text-dim">
          {expanded ? "\u25BC" : "\u25B6"}
        </span>
      </button>

      {/* Section content */}
      {expanded && (
        <div className="px-3 pb-3 space-y-1">
          {bulletItems.map((item, i) => {
            const text = item.replace(/^-\s*/, "").trim();
            // Parse objective-style items: [status] description
            const statusMatch = text.match(/^\[(\w+)\]\s*(.*)$/);
            // Parse bold items: **id**: description
            const boldMatch = text.match(/^\*\*(.+?)\*\*[:\s]*(.*)$/);

            if (statusMatch) {
              const [, status, desc] = statusMatch;
              return (
                <div key={i} className="flex items-start gap-2 py-0.5">
                  <StatusPill status={status} />
                  <span className="text-[11px] text-grim-text leading-relaxed">
                    {desc}
                  </span>
                </div>
              );
            }

            if (boldMatch) {
              const [, key, value] = boldMatch;
              return (
                <div key={i} className="flex items-start gap-2 py-0.5">
                  <span className="text-[11px] font-semibold text-grim-text shrink-0">
                    {key}
                  </span>
                  {value && (
                    <span className="text-[11px] text-grim-text-dim leading-relaxed">
                      {value}
                    </span>
                  )}
                </div>
              );
            }

            return (
              <div key={i} className="flex items-start gap-1.5 py-0.5">
                <span className="text-grim-text-dim text-[10px] mt-0.5 shrink-0">
                  \u2022
                </span>
                <span className="text-[11px] text-grim-text leading-relaxed">
                  {text}
                </span>
              </div>
            );
          })}
          {nonBulletContent.trim() && bulletItems.length > 0 && (
            <div className="text-[11px] text-grim-text-dim leading-relaxed mt-1 whitespace-pre-wrap">
              {nonBulletContent.trim()}
            </div>
          )}
          {nonBulletContent.trim() && bulletItems.length === 0 && (
            <div className="text-[11px] text-grim-text leading-relaxed whitespace-pre-wrap">
              {nonBulletContent.trim()}
            </div>
          )}
          {lines.length === 0 && (
            <div className="text-[10px] text-grim-text-dim italic py-1">
              (empty)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const colors: Record<string, string> = {
    active: "bg-grim-success/15 text-grim-success",
    completed: "bg-grim-accent/15 text-grim-accent",
    stalled: "bg-grim-warning/15 text-grim-warning",
  };
  return (
    <span
      className={`text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0 ${
        colors[status] || "bg-grim-border/30 text-grim-text-dim"
      }`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tab 2: Session History (existing session browser)
// ---------------------------------------------------------------------------

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

interface SessionSummary {
  session: Session;
  messages: ChatMessage[];
  userCount: number;
  grimCount: number;
  lastMessage: string;
  modes: string[];
  fdoCount: number;
  totalMs: number;
}

function summarizeSession(session: Session): SessionSummary {
  const messages = loadMessages(session.id);
  const userMsgs = messages.filter((m) => m.role === "user");
  const grimMsgs = messages.filter((m) => m.role === "grim" && !m.isStep);

  const modes = new Set<string>();
  const fdoIds = new Set<string>();
  let totalMs = 0;

  for (const m of grimMsgs) {
    if (m.meta) {
      if (m.meta.mode) modes.add(m.meta.mode);
      m.meta.fdo_ids?.forEach((id) => fdoIds.add(id));
      totalMs += m.meta.total_ms || 0;
    }
  }

  const last = grimMsgs[grimMsgs.length - 1];
  const lastMessage = last?.content
    ? last.content.slice(0, 100) + (last.content.length > 100 ? "..." : "")
    : "";

  return {
    session,
    messages,
    userCount: userMsgs.length,
    grimCount: grimMsgs.length,
    lastMessage,
    modes: Array.from(modes),
    fdoCount: fdoIds.size,
    totalMs,
  };
}

function SessionHistoryTab() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<SessionSummary[]>([]);

  const store = useGrimStore();
  const activeSessionId = store.activeSessionId;
  const setActiveSessionId = store.setActiveSessionId;
  const setMessages = store.setMessages;
  const setChatPanelOpen = store.setChatPanelOpen;

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (!stored) return;
      const sessions: Session[] = JSON.parse(stored);
      const sorted = sessions.sort((a, b) => b.updatedAt - a.updatedAt);
      setSummaries(sorted.map(summarizeSession));
    } catch {
      // Ignore
    }
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return summaries;
    const q = search.toLowerCase();
    return summaries.filter((s) => {
      if (s.session.title.toLowerCase().includes(q)) return true;
      return s.messages.some(
        (m) => m.content && m.content.toLowerCase().includes(q)
      );
    });
  }, [summaries, search]);

  const selected = useMemo(
    () => (selectedId ? summaries.find((s) => s.session.id === selectedId) : null),
    [selectedId, summaries]
  );

  const handleSwitch = useCallback(
    (id: string) => {
      if (activeSessionId && store.messages.length > 0) {
        const { saveMessages } = require("@/lib/persistence");
        saveMessages(activeSessionId, store.messages);
      }
      setActiveSessionId(id);
      const restored = loadMessages(id);
      setMessages(restored);
      setChatPanelOpen(true);
    },
    [activeSessionId, store.messages, setActiveSessionId, setMessages, setChatPanelOpen]
  );

  const handleDelete = useCallback(
    (id: string) => {
      const { deleteMessages } = require("@/lib/persistence");
      deleteMessages(id);
      store.deleteSessionById(id);
      setSummaries((prev) => prev.filter((s) => s.session.id !== id));
      if (selectedId === id) setSelectedId(null);
    },
    [store, selectedId]
  );

  return (
    <div className="space-y-3">
      {/* Search */}
      <div className="flex items-center gap-2">
        <div className="flex-1 flex items-center gap-2 bg-grim-bg border border-grim-border rounded-lg px-3 py-2">
          <span className="text-grim-text-dim text-xs">search</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by title or message content..."
            className="flex-1 bg-transparent outline-none text-xs text-grim-text font-mono placeholder:text-grim-text-dim/50"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="text-grim-text-dim hover:text-grim-text text-xs"
            >
              clear
            </button>
          )}
        </div>
        <span className="text-[10px] text-grim-text-dim tabular-nums shrink-0">
          {filtered.length} session{filtered.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Session list + detail split */}
      <div className="flex gap-4">
        <div className="flex-1 space-y-2 max-h-[600px] overflow-y-auto">
          {filtered.length === 0 && (
            <div className="text-xs text-grim-text-dim py-8 text-center">
              {search ? "No matching sessions" : "No sessions yet. Start a conversation!"}
            </div>
          )}
          {filtered.map((s) => (
            <SessionCard
              key={s.session.id}
              summary={s}
              isActive={s.session.id === activeSessionId}
              isSelected={s.session.id === selectedId}
              onSelect={() => setSelectedId(selectedId === s.session.id ? null : s.session.id)}
              onSwitch={() => handleSwitch(s.session.id)}
              onDelete={() => handleDelete(s.session.id)}
            />
          ))}
        </div>

        {selected && (
          <div className="w-80 shrink-0 bg-grim-surface border border-grim-border rounded-xl p-3 max-h-[600px] overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-grim-text">
                {selected.session.title}
              </span>
              <button
                onClick={() => setSelectedId(null)}
                className="text-grim-text-dim hover:text-grim-text text-xs"
              >
                close
              </button>
            </div>

            <div className="grid grid-cols-3 gap-2 mb-3">
              <MiniStat label="Messages" value={selected.userCount + selected.grimCount} />
              <MiniStat label="FDOs" value={selected.fdoCount} />
              <MiniStat label="Time" value={`${(selected.totalMs / 1000).toFixed(1)}s`} />
            </div>

            {selected.modes.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-3">
                {selected.modes.map((mode) => (
                  <span
                    key={mode}
                    className="text-[9px] px-1.5 py-0.5 rounded bg-grim-accent/10 text-grim-accent font-mono"
                  >
                    {mode}
                  </span>
                ))}
              </div>
            )}

            <div className="space-y-1.5">
              <span className="text-[10px] text-grim-text-dim uppercase tracking-wider">
                Messages
              </span>
              <div className="space-y-1 max-h-80 overflow-y-auto">
                {selected.messages
                  .filter((m) => !m.isStep)
                  .map((m, i) => (
                    <div
                      key={i}
                      className={`text-[11px] px-2 py-1.5 rounded ${
                        m.role === "user"
                          ? "bg-grim-user-bg text-grim-text"
                          : "bg-grim-bg text-grim-text-dim"
                      }`}
                    >
                      <span className="text-[9px] text-grim-text-dim uppercase mr-1.5">
                        {m.role === "user" ? "you" : "grim"}
                      </span>
                      {m.content
                        ? m.content.slice(0, 150) + (m.content.length > 150 ? "..." : "")
                        : "(empty)"}
                    </div>
                  ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SessionCard({
  summary,
  isActive,
  isSelected,
  onSelect,
  onSwitch,
  onDelete,
}: {
  summary: SessionSummary;
  isActive: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onSwitch: () => void;
  onDelete: () => void;
}) {
  const { session, userCount, grimCount, modes, fdoCount, totalMs, lastMessage } = summary;

  return (
    <div
      onClick={onSelect}
      className={`bg-grim-surface border rounded-lg p-3 cursor-pointer transition-colors ${
        isSelected
          ? "border-grim-accent/50"
          : "border-grim-border hover:border-grim-border/80 hover:bg-grim-surface-hover"
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-medium text-grim-text truncate flex-1">
          {session.title}
        </span>
        {isActive && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-accent/15 text-grim-accent shrink-0">
            active
          </span>
        )}
        <span className="text-[10px] text-grim-text-dim tabular-nums shrink-0">
          {timeAgo(session.updatedAt)}
        </span>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-grim-text-dim mb-1">
        <span>{userCount + grimCount} messages</span>
        {modes.length > 0 && (
          <>
            <span>&middot;</span>
            <span>{modes.join(", ")}</span>
          </>
        )}
        {fdoCount > 0 && (
          <>
            <span>&middot;</span>
            <span>{fdoCount} FDOs</span>
          </>
        )}
        {totalMs > 0 && (
          <>
            <span>&middot;</span>
            <span>{(totalMs / 1000).toFixed(1)}s</span>
          </>
        )}
      </div>

      {lastMessage && (
        <div className="text-[10.5px] text-grim-text-dim truncate">
          {lastMessage}
        </div>
      )}

      {isSelected && (
        <div className="flex gap-2 mt-2 pt-2 border-t border-grim-border/30">
          <button
            onClick={(e) => { e.stopPropagation(); onSwitch(); }}
            className="text-[10px] px-2 py-1 rounded bg-grim-accent/15 text-grim-accent hover:bg-grim-accent/25 transition-colors"
          >
            Open in Chat
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="text-[10px] px-2 py-1 rounded bg-grim-error/10 text-grim-error hover:bg-grim-error/20 transition-colors"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="text-center">
      <div className="text-[10px] text-grim-text-dim uppercase">{label}</div>
      <div className="text-sm font-semibold text-grim-text">{value}</div>
    </div>
  );
}
