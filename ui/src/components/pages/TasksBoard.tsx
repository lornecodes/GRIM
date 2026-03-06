"use client";

import { useState, useMemo, useEffect, useCallback, useRef } from "react";
import { IconTasks } from "@/components/icons/NavIcons";
import { useTasks, type TaskItem, type ProjectInfo } from "@/hooks/useTasks";
import { useGrimStore } from "@/store";
import type { GrimStore } from "@/store";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BOARD_COLUMNS = [
  { key: "new", label: "New", color: "#8888a0" },
  { key: "active", label: "Active", color: "#60a5fa" },
  { key: "in_progress", label: "In Progress", color: "#fbbf24" },
  { key: "resolved", label: "Resolved", color: "#4ade80" },
] as const;

const ALL_STATUSES = [
  ...BOARD_COLUMNS,
  { key: "closed", label: "Closed", color: "#7c6fef" },
] as const;

const PRIORITY_COLORS: Record<string, string> = {
  critical: "bg-red-500/20 text-red-400 border-red-500/30",
  high: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  medium: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  low: "bg-green-500/20 text-green-400 border-green-500/30",
};

const PRIORITY_DOT: Record<string, string> = {
  critical: "bg-red-400",
  high: "bg-orange-400",
  medium: "bg-yellow-400",
  low: "bg-green-400",
};

const ASSIGNEE_LABELS: Record<string, { label: string; color: string }> = {
  code: { label: "Code", color: "bg-blue-500/20 text-blue-400" },
  research: { label: "Research", color: "bg-purple-500/20 text-purple-400" },
  audit: { label: "Audit", color: "bg-orange-500/20 text-orange-400" },
  plan: { label: "Plan", color: "bg-teal-500/20 text-teal-400" },
};

const VAULT_DOMAINS = [
  "ai-systems", "computing", "interests", "journal", "media",
  "modelling", "notes", "people", "personal", "physics", "projects", "tools",
];

type TabId = "board" | "backlog";

// ---------------------------------------------------------------------------
// Shared: useEscapeKey hook
// ---------------------------------------------------------------------------

function useEscapeKey(onEscape: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onEscape();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onEscape]);
}

// ---------------------------------------------------------------------------
// Shared: useClickOutside hook
// ---------------------------------------------------------------------------

function useClickOutside(ref: React.RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [ref, onClose]);
}

// ---------------------------------------------------------------------------
// Toast — non-blocking feedback for operations
// ---------------------------------------------------------------------------

function Toast({ message, type, onDismiss }: { message: string; type: "success" | "error"; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 3000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div className={`fixed bottom-4 right-4 z-[60] text-[10px] px-4 py-2 rounded-lg shadow-lg border animate-fade-in ${
      type === "error"
        ? "bg-red-500/20 border-red-500/40 text-red-300"
        : "bg-green-500/20 border-green-500/40 text-green-300"
    }`}>
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEscapeKey(onCancel);

  return (
    <div className="fixed inset-0 z-[55] flex items-center justify-center bg-black/60" onClick={onCancel}>
      <div className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-xs w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-grim-text mb-2">{title}</h3>
        <p className="text-[10px] text-grim-text-dim mb-4">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="text-[10px] px-3 py-1.5 rounded bg-grim-border/30 text-grim-text-dim hover:text-grim-text transition-colors">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`text-[10px] px-3 py-1.5 rounded text-white transition-colors ${
              danger ? "bg-red-500/80 hover:bg-red-500" : "bg-grim-accent hover:bg-grim-accent-dim"
            }`}
          >
            {confirmLabel ?? "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatusMenu — shared quick-move dropdown for stories
// ---------------------------------------------------------------------------

function StatusMenu({
  currentStatus,
  onSelect,
  onClose,
  includeClose,
}: {
  currentStatus: string;
  onSelect: (status: string) => void;
  onClose: () => void;
  includeClose?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, onClose);

  const columns = includeClose ? ALL_STATUSES : BOARD_COLUMNS;

  return (
    <div ref={ref} className="absolute right-0 top-full mt-1 z-30 bg-grim-surface border border-grim-border rounded-lg shadow-lg py-1 min-w-[100px]">
      {columns.filter((c) => c.key !== currentStatus).map((col) => (
        <button
          key={col.key}
          onClick={(e) => { e.stopPropagation(); onSelect(col.key); onClose(); }}
          className="w-full text-left text-[8px] px-3 py-1.5 text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover transition-colors flex items-center gap-1.5"
        >
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: col.color }} />
          {col.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent indicator — pulsing dot when a pool agent is working on a story
// ---------------------------------------------------------------------------

function AgentIndicator({ jobId }: { jobId: string }) {
  const poolJobs = useGrimStore((s: GrimStore) => s.poolJobs);
  const job = poolJobs.find((j) => j.id ===jobId);
  const isRunning = job?.status === "running";

  if (!isRunning) return null;

  return (
    <span className="inline-flex items-center gap-1 text-[7px] text-green-400" title="Agent working">
      <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
      Agent
    </span>
  );
}

// ---------------------------------------------------------------------------
// Story card — flat row with assignee badge and agent indicator
// ---------------------------------------------------------------------------

function StoryCard({
  story,
  onMoveStory,
  onEditStory,
  moving,
}: {
  story: TaskItem;
  onMoveStory: (column: string) => void;
  onEditStory: () => void;
  moving: boolean;
}) {
  const [showMoveMenu, setShowMoveMenu] = useState(false);
  const priorityDot = PRIORITY_DOT[story.priority ?? "medium"] ?? PRIORITY_DOT.medium;
  const assigneeInfo = story.assignee ? ASSIGNEE_LABELS[story.assignee] : null;
  const hasRunningJob = !!story.job_id;

  return (
    <div className={`flex items-center gap-2 px-3 py-2 border-b border-grim-border/30 hover:bg-grim-surface-hover/30 transition-colors ${
      moving ? "opacity-50 pointer-events-none" : ""
    } ${hasRunningJob ? "border-l-2 border-l-green-500/40" : ""}`}>
      {/* Priority dot */}
      <span className={`w-2 h-2 rounded-full shrink-0 ${priorityDot}`} title={story.priority} />

      {/* Title + ID */}
      <div className="flex-1 min-w-0">
        <button
          onClick={onEditStory}
          className="text-[10px] font-medium text-grim-text text-left leading-tight hover:text-grim-accent transition-colors truncate block w-full"
        >
          {story.title}
        </button>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[7px] text-grim-text-dim font-mono">{story.id}</span>
          {story.domain && (
            <span className="text-[7px] text-grim-text-dim">{story.domain}</span>
          )}
        </div>
      </div>

      {/* Assignee badge */}
      {assigneeInfo && (
        <span className={`text-[7px] px-1.5 py-0.5 rounded ${assigneeInfo.color} shrink-0`}>
          {assigneeInfo.label}
        </span>
      )}

      {/* Agent indicator */}
      {story.job_id && <AgentIndicator jobId={story.job_id} />}

      {/* Estimate */}
      {story.estimate_days ? (
        <span className="text-[8px] text-grim-text-dim shrink-0">{story.estimate_days}d</span>
      ) : null}

      {/* Move button */}
      <div className="relative shrink-0">
        <button
          onClick={() => setShowMoveMenu(!showMoveMenu)}
          className="text-[8px] text-grim-text-dim hover:text-grim-accent transition-colors px-1"
        >
          move
        </button>
        {showMoveMenu && (
          <StatusMenu
            currentStatus={story.status}
            onSelect={onMoveStory}
            onClose={() => setShowMoveMenu(false)}
            includeClose
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit modal — story only, with assignee dropdown and dispatch button
// ---------------------------------------------------------------------------

function EditModal({
  item,
  onSave,
  onDelete,
  onClose,
  onDispatch,
}: {
  item: TaskItem;
  onSave: (fields: Record<string, unknown>) => void;
  onDelete?: () => void;
  onClose: () => void;
  onDispatch?: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [status, setStatus] = useState(item.status);
  const [priority, setPriority] = useState(item.priority ?? "medium");
  const [estimate, setEstimate] = useState(item.estimate_days?.toString() ?? "");
  const [description, setDescription] = useState(item.description ?? "");
  const [assignee, setAssignee] = useState(item.assignee ?? "");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Live transcript for running jobs
  const liveTranscripts = useGrimStore((s: GrimStore) => s.liveTranscripts);
  const poolJobs = useGrimStore((s: GrimStore) => s.poolJobs);
  const transcript = item.job_id ? liveTranscripts[item.job_id] ?? [] : [];
  const job = item.job_id ? poolJobs.find((j) => j.id ===item.job_id) : null;
  const isRunning = job?.status === "running";

  useEscapeKey(onClose);

  const handleSave = () => {
    const fields: Record<string, unknown> = {};
    if (title !== item.title) fields.title = title;
    if (status !== item.status) fields.status = status;
    if (priority !== item.priority) fields.priority = priority;
    if (estimate && parseFloat(estimate) !== item.estimate_days) fields.estimate_days = parseFloat(estimate);
    if (description !== (item.description ?? "")) fields.description = description;
    if (assignee !== (item.assignee ?? "")) fields.assignee = assignee;
    if (Object.keys(fields).length > 0) onSave(fields);
    onClose();
  };

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
        <div className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-md w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-grim-text">Edit Story</h3>
              <p className="text-[9px] text-grim-text-dim font-mono mt-0.5">{item.id}</p>
            </div>
            <button onClick={onClose} className="text-grim-text-dim hover:text-grim-text text-lg leading-none">&times;</button>
          </div>

          <div className="space-y-3">
            <div>
              <label className="text-[9px] text-grim-text-dim block mb-1">Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
              />
            </div>

            <div className="flex gap-2">
              <div className="flex-1">
                <label className="text-[9px] text-grim-text-dim block mb-1">Status</label>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
                >
                  {ALL_STATUSES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
              </div>
              <div className="flex-1">
                <label className="text-[9px] text-grim-text-dim block mb-1">Priority</label>
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
                >
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </div>
              <div className="w-20">
                <label className="text-[9px] text-grim-text-dim block mb-1">Est. days</label>
                <input
                  type="number"
                  step="0.5"
                  value={estimate}
                  onChange={(e) => setEstimate(e.target.value)}
                  className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
                />
              </div>
            </div>

            {/* Assignee dropdown */}
            <div>
              <label className="text-[9px] text-grim-text-dim block mb-1">Assignee (agent type)</label>
              <select
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
              >
                <option value="">Unassigned</option>
                <option value="code">Code</option>
                <option value="research">Research</option>
                <option value="audit">Audit</option>
                <option value="plan">Plan</option>
              </select>
            </div>

            <div>
              <label className="text-[9px] text-grim-text-dim block mb-1">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent resize-none"
              />
            </div>

            {/* Mini live transcript panel for running jobs */}
            {item.job_id && (
              <div>
                <label className="text-[9px] text-grim-text-dim block mb-1">
                  Agent Output {isRunning && <span className="text-green-400 animate-pulse">● live</span>}
                  {!isRunning && job && <span className="text-grim-text-dim"> ({job.status})</span>}
                </label>
                <div className="bg-grim-bg border border-grim-border rounded-lg p-2 max-h-40 overflow-y-auto font-mono text-[9px] text-grim-text-dim">
                  {transcript.length > 0 ? (
                    transcript.slice(-20).map((entry: unknown, i: number) => (
                      <div key={i} className="py-0.5">
                        {typeof entry === "string" ? entry : JSON.stringify(entry)}
                      </div>
                    ))
                  ) : (
                    <span className="text-grim-text-dim/50">
                      {isRunning ? "Waiting for output..." : "No transcript available"}
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 justify-between pt-2">
              <div className="flex gap-2">
                {onDelete && (
                  <button
                    onClick={() => setShowDeleteConfirm(true)}
                    className="text-[10px] px-3 py-1.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
                  >
                    Delete
                  </button>
                )}
                {onDispatch && (item.assignee || assignee) && !item.job_id && (
                  <button
                    onClick={onDispatch}
                    className="text-[10px] px-3 py-1.5 rounded bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 transition-colors"
                  >
                    Dispatch to Pool
                  </button>
                )}
              </div>
              <div className="flex gap-2">
                <button onClick={onClose} className="text-[10px] px-3 py-1.5 rounded bg-grim-border/30 text-grim-text-dim hover:text-grim-text transition-colors">
                  Cancel
                </button>
                <button onClick={handleSave} className="text-[10px] px-3 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim transition-colors">
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Delete confirmation */}
      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete story?"
          message={`This will permanently remove "${item.title}". This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => { setShowDeleteConfirm(false); onDelete!(); onClose(); }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Create modal — story only, with assignee
// ---------------------------------------------------------------------------

function CreateModal({
  parentId,
  onSave,
  onClose,
}: {
  parentId: string;
  onSave: (args: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState("medium");
  const [estimate, setEstimate] = useState("1");
  const [description, setDescription] = useState("");
  const [assignee, setAssignee] = useState("");

  useEscapeKey(onClose);

  const handleCreate = () => {
    if (!title.trim()) return;
    const args: Record<string, unknown> = {
      title: title.trim(),
      proj_id: parentId,
      priority,
      estimate_days: parseFloat(estimate) || 1,
    };
    if (description) args.description = description;
    if (assignee) args.assignee = assignee;
    onSave(args);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-sm w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-grim-text mb-1">New Story</h3>
        <p className="text-[9px] text-grim-text-dim mb-4 font-mono">Project: {parentId}</p>
        <div className="space-y-3">
          <input
            type="text"
            placeholder="Story title..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text placeholder-grim-text-dim focus:outline-none focus:border-grim-accent"
            autoFocus
          />
          <div className="flex gap-2">
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="flex-1 text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            >
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              className="flex-1 text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            >
              <option value="">No agent</option>
              <option value="code">Code</option>
              <option value="research">Research</option>
              <option value="audit">Audit</option>
              <option value="plan">Plan</option>
            </select>
            <input
              type="number"
              step="0.5"
              placeholder="Days"
              value={estimate}
              onChange={(e) => setEstimate(e.target.value)}
              className="w-20 text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            />
          </div>
          <textarea
            placeholder="Description..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text placeholder-grim-text-dim focus:outline-none focus:border-grim-accent resize-none"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="text-[10px] px-3 py-1.5 rounded bg-grim-border/30 text-grim-text-dim hover:text-grim-text transition-colors">Cancel</button>
            <button
              onClick={handleCreate}
              disabled={!title.trim()}
              className="text-[10px] px-3 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim disabled:opacity-30 transition-colors"
            >
              Create
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Backlog tab — flat list with domain column
// ---------------------------------------------------------------------------

function BacklogView({
  stories,
  onEdit,
  onMoveToBoard,
  moving,
}: {
  stories: TaskItem[];
  onEdit: (story: TaskItem) => void;
  onMoveToBoard: (storyId: string) => void;
  moving: string | null;
}) {
  if (stories.length === 0) {
    return (
      <div className="text-xs text-grim-text-dim py-16 text-center">
        No stories in the backlog. All stories are either on the board or haven&apos;t been created yet.
      </div>
    );
  }

  return (
    <div className="border border-grim-border rounded-lg overflow-hidden">
      <div className="grid grid-cols-[1fr_90px_80px_80px_60px_60px_80px] gap-2 px-3 py-2 bg-grim-surface text-[9px] font-medium text-grim-text-dim border-b border-grim-border">
        <span>Title</span>
        <span>Domain</span>
        <span>Status</span>
        <span>Priority</span>
        <span>Agent</span>
        <span>Est.</span>
        <span></span>
      </div>
      {stories.map((story) => {
        const priorityClass = PRIORITY_COLORS[story.priority ?? "medium"] ?? PRIORITY_COLORS.medium;
        const assigneeInfo = story.assignee ? ASSIGNEE_LABELS[story.assignee] : null;

        return (
          <div
            key={story.id}
            className="grid grid-cols-[1fr_90px_80px_80px_60px_60px_80px] gap-2 px-3 py-2 border-b border-grim-border/30 hover:bg-grim-surface-hover/30 items-center"
          >
            <button
              onClick={() => onEdit(story)}
              className="text-[10px] text-grim-text text-left hover:text-grim-accent truncate transition-colors"
            >
              {story.title}
            </button>
            <span className="text-[8px] text-grim-text-dim truncate">
              {story.domain ?? ""}
            </span>
            <span className="text-[8px] px-1.5 py-0.5 rounded bg-grim-accent/10 text-grim-accent inline-block w-fit">
              {story.status}
            </span>
            <span className={`text-[8px] px-1.5 py-0.5 rounded border w-fit ${priorityClass}`}>
              {story.priority ?? "medium"}
            </span>
            <span className="text-[8px] text-grim-text-dim">
              {assigneeInfo ? assigneeInfo.label : "-"}
            </span>
            <span className="text-[8px] text-grim-text-dim">
              {story.estimate_days ?? "-"}d
            </span>
            <button
              onClick={() => onMoveToBoard(story.id)}
              disabled={moving === story.id}
              className="text-[7px] px-1.5 py-0.5 rounded bg-grim-accent/20 text-grim-accent hover:bg-grim-accent/30 transition-colors disabled:opacity-30"
              title="Add to board"
            >
              + Board
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter bar — domain + project dropdowns
// ---------------------------------------------------------------------------

function FilterBar({
  projects,
  selectedProject,
  onProjectChange,
  selectedDomain,
  onDomainChange,
}: {
  projects: ProjectInfo[];
  selectedProject: string;
  onProjectChange: (id: string) => void;
  selectedDomain: string;
  onDomainChange: (domain: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <select
        value={selectedDomain}
        onChange={(e) => onDomainChange(e.target.value)}
        className="text-[11px] px-2 py-1.5 rounded-lg bg-grim-surface border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
      >
        <option value="">All Domains</option>
        {VAULT_DOMAINS.map((d) => (
          <option key={d} value={d}>{d}</option>
        ))}
      </select>
      <select
        value={selectedProject}
        onChange={(e) => onProjectChange(e.target.value)}
        className="text-[11px] px-2 py-1.5 rounded-lg bg-grim-surface border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
      >
        <option value="">All Projects</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>{p.title}</option>
        ))}
      </select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TasksBoard() {
  const {
    board, backlog, allStories, projects,
    selectedProject, setSelectedProject,
    selectedDomain, setSelectedDomain,
    loading, error, moving,
    moveStory, createItem, updateItem, dispatchStory, refresh,
  } = useTasks();

  const [tab, setTab] = useState<TabId>("board");
  const [editItem, setEditItem] = useState<TaskItem | null>(null);
  const [createModal, setCreateModal] = useState<string | null>(null); // proj_id
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  const showToast = useCallback((message: string, type: "success" | "error" = "success") => {
    setToast({ message, type });
  }, []);

  // Flatten board stories (skip closed from display)
  const { activeStories, closedCount } = useMemo(() => {
    if (!board?.columns) return { activeStories: [] as TaskItem[], closedCount: 0 };
    const columns = board.columns ?? {};
    const stories: TaskItem[] = [];
    let closed = 0;
    const seen = new Set<string>();

    for (const col of BOARD_COLUMNS) {
      for (const story of (columns[col.key] ?? [])) {
        if (!seen.has(story.id)) {
          seen.add(story.id);
          stories.push(story);
        }
      }
    }
    closed = (columns["closed"] ?? []).length;
    return { activeStories: stories, closedCount: closed };
  }, [board]);

  const handleArchive = useCallback(async () => {
    setShowArchiveConfirm(false);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_GRIM_API || ""}/api/tasks/archive`, { method: "POST" });
      if (res.ok) {
        showToast(`Archived ${closedCount} closed stories`);
        refresh();
      } else {
        showToast("Archive failed", "error");
      }
    } catch {
      showToast("Archive failed", "error");
    }
  }, [closedCount, showToast, refresh]);

  const handleDelete = useCallback(async (itemId: string) => {
    await updateItem(itemId, { status: "closed" });
    showToast("Story closed");
  }, [updateItem, showToast]);

  const handleCreate = useCallback(async (args: Record<string, unknown>) => {
    const result = await createItem(args as Parameters<typeof createItem>[0]);
    if (result) showToast("Story created");
    else showToast("Create failed", "error");
  }, [createItem, showToast]);

  const handleUpdate = useCallback(async (itemId: string, fields: Record<string, unknown>) => {
    await updateItem(itemId, fields);
    showToast("Updated");
  }, [updateItem, showToast]);

  const handleDispatch = useCallback(async (storyId: string) => {
    const result = await dispatchStory(storyId);
    if (result) showToast("Dispatched to pool");
    else showToast("Dispatch failed", "error");
  }, [dispatchStory, showToast]);

  return (
    <div className="max-w-full mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconTasks size={28} className="text-grim-accent" />
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-grim-text">Stories</h1>
          <p className="text-xs text-grim-text-dim mt-0.5">
            {tab === "board"
              ? `${activeStories.length} active stories`
              : `${allStories.length} total stories`}
            {closedCount > 0 && tab === "board" ? ` / ${closedCount} closed` : ""}
            {backlog?.count ? ` / ${backlog.count} in backlog` : ""}
          </p>
        </div>
        <FilterBar
          projects={projects}
          selectedProject={selectedProject}
          onProjectChange={setSelectedProject}
          selectedDomain={selectedDomain}
          onDomainChange={setSelectedDomain}
        />
        <div className="flex gap-1">
          <button
            onClick={() => setTab("board")}
            className={`text-[10px] px-3 py-1.5 rounded-l-lg border transition-colors ${
              tab === "board"
                ? "bg-grim-accent/20 border-grim-accent/40 text-grim-accent"
                : "bg-grim-surface border-grim-border text-grim-text-dim hover:text-grim-text"
            }`}
          >
            Board
          </button>
          <button
            onClick={() => setTab("backlog")}
            className={`text-[10px] px-3 py-1.5 rounded-r-lg border border-l-0 transition-colors ${
              tab === "backlog"
                ? "bg-grim-accent/20 border-grim-accent/40 text-grim-accent"
                : "bg-grim-surface border-grim-border text-grim-text-dim hover:text-grim-text"
            }`}
          >
            Backlog
          </button>
        </div>
        <div className="flex gap-1">
          {closedCount > 0 && tab === "board" && (
            <button
              onClick={() => setShowArchiveConfirm(true)}
              className="text-[10px] px-2 py-1.5 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-accent transition-colors"
              title={`Archive ${closedCount} closed stories`}
            >
              Archive ({closedCount})
            </button>
          )}
          <button
            onClick={refresh}
            className="text-[10px] px-2 py-1.5 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">Loading...</div>
      )}

      {/* Error */}
      {error && (
        <div className="text-xs text-red-400 py-4 text-center">
          {error}
          <button onClick={refresh} className="ml-2 underline hover:text-red-300">retry</button>
        </div>
      )}

      {/* Board tab — columns with story cards */}
      {!loading && tab === "board" && board && (
        <>
          {activeStories.length > 0 ? (
            <div className="grid grid-cols-4 gap-3">
              {BOARD_COLUMNS.map((col) => {
                const colStories = (board.columns[col.key] ?? []).filter(
                  (s) => s.status !== "closed"
                );
                return (
                  <div key={col.key} className="border border-grim-border rounded-lg overflow-hidden">
                    {/* Column header */}
                    <div className="flex items-center gap-1.5 px-3 py-2 bg-grim-surface border-b border-grim-border">
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: col.color }} />
                      <span className="text-[10px] font-medium text-grim-text">{col.label}</span>
                      <span className="text-[8px] text-grim-text-dim ml-auto">{colStories.length}</span>
                    </div>
                    {/* Stories */}
                    <div className="min-h-[100px]">
                      {colStories.map((story) => (
                        <StoryCard
                          key={story.id}
                          story={story}
                          onMoveStory={(c) => moveStory(story.id, c)}
                          onEditStory={() => setEditItem(story)}
                          moving={moving === story.id}
                        />
                      ))}
                    </div>
                    {/* Add story button */}
                    {col.key === "new" && selectedProject && (
                      <div className="px-3 py-2 border-t border-grim-border/30">
                        <button
                          onClick={() => setCreateModal(selectedProject)}
                          className="text-[8px] text-grim-text-dim hover:text-grim-accent transition-colors"
                        >
                          + Add story
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <IconTasks size={48} className="text-grim-accent opacity-30" />
              <p className="text-xs text-grim-text-dim">
                No active stories on the board.{" "}
                {closedCount > 0
                  ? `${closedCount} closed stories can be archived.`
                  : "Move stories from the Backlog tab or create new ones."}
              </p>
              {selectedProject && (
                <button
                  onClick={() => setCreateModal(selectedProject)}
                  className="text-[10px] px-3 py-1.5 rounded bg-grim-accent/20 text-grim-accent hover:bg-grim-accent/30 transition-colors"
                >
                  + New Story
                </button>
              )}
            </div>
          )}

          {/* Backlog preview */}
          {backlog && backlog.count > 0 && (
            <div className="flex items-center gap-2 px-2 py-2 bg-grim-surface/50 rounded-lg border border-grim-border/30">
              <span className="text-[9px] text-grim-text-dim">
                {backlog.count} stories in backlog
              </span>
              <button
                onClick={() => setTab("backlog")}
                className="text-[9px] text-grim-accent hover:underline"
              >
                View all
              </button>
            </div>
          )}
        </>
      )}

      {/* Backlog tab */}
      {!loading && tab === "backlog" && (
        <BacklogView
          stories={allStories}
          onEdit={(s) => setEditItem(s)}
          onMoveToBoard={(id) => moveStory(id, "new")}
          moving={moving}
        />
      )}

      {/* Edit modal */}
      {editItem && (
        <EditModal
          item={editItem}
          onSave={(fields) => handleUpdate(editItem.id, fields)}
          onDelete={() => handleDelete(editItem.id)}
          onClose={() => setEditItem(null)}
          onDispatch={() => handleDispatch(editItem.id)}
        />
      )}

      {/* Create modal */}
      {createModal && (
        <CreateModal
          parentId={createModal}
          onSave={handleCreate}
          onClose={() => setCreateModal(null)}
        />
      )}

      {/* Archive confirmation */}
      {showArchiveConfirm && (
        <ConfirmDialog
          title="Archive closed stories?"
          message={`This will archive ${closedCount} closed stories, removing them from the board. They'll remain in their project FDOs.`}
          confirmLabel="Archive"
          onConfirm={handleArchive}
          onCancel={() => setShowArchiveConfirm(false)}
        />
      )}

      {/* Toast */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
}
