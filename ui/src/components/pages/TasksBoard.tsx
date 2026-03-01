"use client";

import { useState, useMemo } from "react";
import { IconTasks } from "@/components/icons/NavIcons";
import { useTasks, type TaskItem, type ProjectInfo } from "@/hooks/useTasks";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TASK_COLUMNS = [
  { key: "new", label: "New", color: "#8888a0" },
  { key: "active", label: "Active", color: "#60a5fa" },
  { key: "in_progress", label: "In Progress", color: "#fbbf24" },
  { key: "resolved", label: "Resolved", color: "#4ade80" },
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

type TabId = "board" | "backlog";

// ---------------------------------------------------------------------------
// Task chip — sits in a status column on the sprint board
// ---------------------------------------------------------------------------

function TaskChip({
  task,
  onStatusChange,
  onEdit,
}: {
  task: TaskItem;
  onStatusChange: (status: string) => void;
  onEdit: () => void;
}) {
  const [showMenu, setShowMenu] = useState(false);

  return (
    <div className="relative group">
      <button
        onClick={onEdit}
        className="w-full text-left text-[9px] px-2 py-1.5 rounded-md bg-grim-surface border border-grim-border/60 text-grim-text hover:border-grim-accent/40 transition-all truncate"
        title={task.title}
      >
        {task.title}
      </button>
      {/* Quick-move dot */}
      <button
        onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu); }}
        className="absolute -right-1 -top-1 w-3 h-3 rounded-full bg-grim-border hover:bg-grim-accent text-[6px] text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
        title="Move task"
      >
        &gt;
      </button>
      {showMenu && (
        <div className="absolute right-0 top-4 z-30 bg-grim-surface border border-grim-border rounded-lg shadow-lg py-1 min-w-[90px]">
          {TASK_COLUMNS.filter((c) => c.key !== task.status).map((col) => (
            <button
              key={col.key}
              onClick={(e) => { e.stopPropagation(); onStatusChange(col.key); setShowMenu(false); }}
              className="w-full text-left text-[8px] px-3 py-1 text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover transition-colors flex items-center gap-1.5"
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: col.color }} />
              {col.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Story row — horizontal: story info left, tasks in columns right
// ---------------------------------------------------------------------------

function StoryRow({
  story,
  onMoveStory,
  onEditStory,
  onEditTask,
  onUpdateTaskStatus,
  onAddTask,
  moving,
}: {
  story: TaskItem;
  onMoveStory: (column: string) => void;
  onEditStory: () => void;
  onEditTask: (task: TaskItem) => void;
  onUpdateTaskStatus: (taskId: string, status: string) => void;
  onAddTask: () => void;
  moving: boolean;
}) {
  const tasks = story.tasks ?? [];
  const tasksDone = tasks.filter((t) => t.status === "resolved" || t.status === "closed").length;
  const progress = tasks.length > 0 ? Math.round((tasksDone / tasks.length) * 100) : 0;
  const priorityDot = PRIORITY_DOT[story.priority ?? "medium"] ?? PRIORITY_DOT.medium;

  // Group tasks by status
  const tasksByStatus = useMemo(() => {
    const map: Record<string, TaskItem[]> = {};
    for (const col of TASK_COLUMNS) map[col.key] = [];
    for (const t of tasks) {
      const status = t.status ?? "new";
      if (map[status]) map[status].push(t);
      else (map["new"] ??= []).push(t);
    }
    return map;
  }, [tasks]);

  return (
    <div className={`flex border-b border-grim-border/30 hover:bg-grim-surface-hover/30 transition-colors ${moving ? "opacity-50" : ""}`}>
      {/* Story info — left column */}
      <div className="w-[220px] shrink-0 p-2 border-r border-grim-border/30">
        <div className="flex items-start gap-1.5">
          <span className={`w-2 h-2 rounded-full mt-1 shrink-0 ${priorityDot}`} title={story.priority} />
          <button
            onClick={onEditStory}
            className="text-[10px] font-medium text-grim-text text-left leading-tight hover:text-grim-accent transition-colors line-clamp-2 flex-1"
          >
            {story.title}
          </button>
        </div>
        <div className="flex items-center gap-2 mt-1.5 pl-3.5">
          <span className="text-[8px] text-grim-text-dim font-mono">{story.id}</span>
          {story.estimate_days ? (
            <span className="text-[8px] text-grim-text-dim">{story.estimate_days}d</span>
          ) : null}
        </div>
        {/* Progress bar */}
        {tasks.length > 0 && (
          <div className="mt-1.5 pl-3.5 pr-1">
            <div className="h-1 bg-grim-border/40 rounded-full overflow-hidden">
              <div className="h-full bg-grim-accent/60 rounded-full transition-all" style={{ width: `${progress}%` }} />
            </div>
            <div className="text-[7px] text-grim-text-dim mt-0.5">{tasksDone}/{tasks.length} tasks</div>
          </div>
        )}
        {/* Add task button */}
        <button
          onClick={onAddTask}
          className="mt-1 ml-3 text-[8px] text-grim-text-dim hover:text-grim-accent transition-colors"
        >
          + task
        </button>
      </div>

      {/* Task columns */}
      {TASK_COLUMNS.map((col) => (
        <div key={col.key} className="flex-1 min-w-[120px] p-1.5 border-r border-grim-border/20 last:border-r-0">
          <div className="space-y-1">
            {tasksByStatus[col.key]?.map((task) => (
              <TaskChip
                key={task.id}
                task={task}
                onStatusChange={(status) => onUpdateTaskStatus(task.id, status)}
                onEdit={() => onEditTask(task)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feature group header
// ---------------------------------------------------------------------------

function FeatureHeader({ featureId, storyCount }: { featureId: string; storyCount: number }) {
  const label = featureId.replace("feat-", "").replace(/-/g, " ");
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-grim-accent/5 border-b border-grim-border/30">
      <span className="text-[9px] font-semibold text-grim-accent uppercase tracking-wider">
        {label}
      </span>
      <span className="text-[8px] text-grim-text-dim">{storyCount} stories</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit modal (story or task)
// ---------------------------------------------------------------------------

function EditModal({
  item,
  type,
  onSave,
  onClose,
}: {
  item: TaskItem;
  type: "story" | "task";
  onSave: (fields: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [status, setStatus] = useState(item.status);
  const [priority, setPriority] = useState(item.priority ?? "medium");
  const [estimate, setEstimate] = useState(item.estimate_days?.toString() ?? "");
  const [description, setDescription] = useState(item.description ?? "");
  const [notes, setNotes] = useState(item.notes ?? "");

  const handleSave = () => {
    const fields: Record<string, unknown> = {};
    if (title !== item.title) fields.title = title;
    if (status !== item.status) fields.status = status;
    if (type === "story" && priority !== item.priority) fields.priority = priority;
    if (estimate && parseFloat(estimate) !== item.estimate_days) fields.estimate_days = parseFloat(estimate);
    if (type === "story" && description !== (item.description ?? "")) fields.description = description;
    if (type === "task" && notes !== (item.notes ?? "")) fields.notes = notes;
    if (Object.keys(fields).length > 0) onSave(fields);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-md w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-grim-text">Edit {type === "story" ? "Story" : "Task"}</h3>
            <p className="text-[9px] text-grim-text-dim font-mono mt-0.5">{item.id}</p>
          </div>
          <button onClick={onClose} className="text-grim-text-dim hover:text-grim-text text-lg">x</button>
        </div>

        <div className="space-y-3">
          {/* Title */}
          <div>
            <label className="text-[9px] text-grim-text-dim block mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            />
          </div>

          {/* Status + Priority row */}
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="text-[9px] text-grim-text-dim block mb-1">Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
              >
                {TASK_COLUMNS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
            </div>
            {type === "story" && (
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
            )}
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

          {/* Description / Notes */}
          <div>
            <label className="text-[9px] text-grim-text-dim block mb-1">
              {type === "story" ? "Description" : "Notes"}
            </label>
            <textarea
              value={type === "story" ? description : notes}
              onChange={(e) => type === "story" ? setDescription(e.target.value) : setNotes(e.target.value)}
              rows={3}
              className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent resize-none"
            />
          </div>

          {/* Nested tasks (read-only in story edit) */}
          {type === "story" && item.tasks && item.tasks.length > 0 && (
            <div>
              <label className="text-[9px] text-grim-text-dim block mb-1">Tasks ({item.tasks.length})</label>
              <div className="space-y-1">
                {item.tasks.map((t) => (
                  <div key={t.id} className="flex items-center gap-2 text-[9px] px-2 py-1 bg-grim-bg rounded">
                    <span className={`w-2 h-2 rounded-sm ${t.status === "closed" || t.status === "resolved" ? "bg-grim-accent" : "bg-grim-border"}`} />
                    <span className="flex-1 text-grim-text">{t.title}</span>
                    <span className="text-grim-text-dim">{t.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 justify-end pt-2">
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
  );
}

// ---------------------------------------------------------------------------
// Create modal (story or task)
// ---------------------------------------------------------------------------

function CreateModal({
  type,
  parentId,
  onSave,
  onClose,
}: {
  type: "story" | "task";
  parentId: string; // feat_id for stories, story_id for tasks
  onSave: (args: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState("medium");
  const [estimate, setEstimate] = useState("1");
  const [description, setDescription] = useState("");

  const handleCreate = () => {
    if (!title.trim()) return;
    const args: Record<string, unknown> = {
      type,
      title: title.trim(),
      estimate_days: parseFloat(estimate) || 1,
    };
    if (type === "story") {
      args.feat_id = parentId;
      args.priority = priority;
      if (description) args.description = description;
    } else {
      args.story_id = parentId;
      if (description) args.notes = description;
    }
    onSave(args);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-sm w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-grim-text mb-4">
          New {type === "story" ? "Story" : "Task"}
        </h3>
        <div className="space-y-3">
          <input
            type="text"
            placeholder={type === "story" ? "Story title..." : "Task title..."}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text placeholder-grim-text-dim focus:outline-none focus:border-grim-accent"
            autoFocus
          />
          <div className="flex gap-2">
            {type === "story" && (
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
            )}
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
            placeholder={type === "story" ? "Description..." : "Notes..."}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text placeholder-grim-text-dim focus:outline-none focus:border-grim-accent resize-none"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="text-[10px] px-3 py-1.5 rounded bg-grim-border/30 text-grim-text-dim hover:text-grim-text">Cancel</button>
            <button
              onClick={handleCreate}
              disabled={!title.trim()}
              className="text-[10px] px-3 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim disabled:opacity-30"
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
// Backlog tab — flat list of all stories
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
        No stories found. Create features with stories using the task-manage skill.
      </div>
    );
  }

  return (
    <div className="border border-grim-border rounded-lg overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-[1fr_100px_80px_80px_60px_80px] gap-2 px-3 py-2 bg-grim-surface text-[9px] font-medium text-grim-text-dim border-b border-grim-border">
        <span>Title</span>
        <span>Feature</span>
        <span>Status</span>
        <span>Priority</span>
        <span>Est.</span>
        <span>Tasks</span>
      </div>
      {/* Rows */}
      {stories.map((story) => {
        const tasksDone = (story.tasks ?? []).filter((t) => t.status === "resolved" || t.status === "closed").length;
        const taskCount = story.tasks?.length ?? story.task_count ?? 0;
        const priorityClass = PRIORITY_COLORS[story.priority ?? "medium"] ?? PRIORITY_COLORS.medium;

        return (
          <div
            key={story.id}
            className="grid grid-cols-[1fr_100px_80px_80px_60px_80px] gap-2 px-3 py-2 border-b border-grim-border/30 hover:bg-grim-surface-hover/30 items-center"
          >
            <button
              onClick={() => onEdit(story)}
              className="text-[10px] text-grim-text text-left hover:text-grim-accent truncate transition-colors"
            >
              {story.title}
            </button>
            <span className="text-[8px] text-grim-text-dim truncate">
              {(story.feature ?? "").replace("feat-", "")}
            </span>
            <span className="text-[8px] px-1.5 py-0.5 rounded bg-grim-accent/10 text-grim-accent inline-block w-fit">
              {story.status}
            </span>
            <span className={`text-[8px] px-1.5 py-0.5 rounded border w-fit ${priorityClass}`}>
              {story.priority ?? "medium"}
            </span>
            <span className="text-[8px] text-grim-text-dim">
              {story.estimate_days ?? "-"}d
            </span>
            <div className="flex items-center gap-1">
              <span className="text-[8px] text-grim-text-dim">{tasksDone}/{taskCount}</span>
              <button
                onClick={() => onMoveToBoard(story.id)}
                disabled={moving === story.id}
                className="text-[7px] px-1.5 py-0.5 rounded bg-grim-accent/20 text-grim-accent hover:bg-grim-accent/30 transition-colors disabled:opacity-30 ml-auto"
                title="Add to board"
              >
                + Board
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Epic dropdown
// ---------------------------------------------------------------------------

function EpicSelector({
  projects,
  selected,
  onChange,
}: {
  projects: ProjectInfo[];
  selected: string;
  onChange: (id: string) => void;
}) {
  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
      className="text-lg font-semibold bg-transparent text-grim-text border-none focus:outline-none cursor-pointer hover:text-grim-accent transition-colors appearance-none pr-6"
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238888a0' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 0 center",
      }}
    >
      <option value="">All Projects</option>
      {projects.map((p) => (
        <option key={p.id} value={p.id}>{p.title}</option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TasksBoard() {
  const {
    board, backlog, allStories, projects,
    selectedProject, setSelectedProject,
    loading, error, moving,
    moveStory, createItem, updateItem, updateTaskStatus, refresh,
  } = useTasks();

  const [tab, setTab] = useState<TabId>("board");
  const [editItem, setEditItem] = useState<{ item: TaskItem; type: "story" | "task" } | null>(null);
  const [createModal, setCreateModal] = useState<{ type: "story" | "task"; parentId: string } | null>(null);

  // Group board stories by feature
  const storiesByFeature = useMemo(() => {
    if (!board) return new Map<string, TaskItem[]>();
    const map = new Map<string, TaskItem[]>();
    for (const col of Object.values(board.columns)) {
      for (const story of col) {
        const feat = story.feature || "ungrouped";
        if (!map.has(feat)) map.set(feat, []);
        // Only add if not already present (stories appear in one column)
        if (!map.get(feat)!.some((s) => s.id === story.id)) {
          map.get(feat)!.push(story);
        }
      }
    }
    return map;
  }, [board]);

  const totalStories = board
    ? Object.values(board.columns).reduce((sum, col) => sum + col.length, 0)
    : 0;

  return (
    <div className="max-w-full mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconTasks size={28} className="text-grim-accent" />
        <div className="flex-1">
          <EpicSelector
            projects={projects}
            selected={selectedProject}
            onChange={setSelectedProject}
          />
          <p className="text-xs text-grim-text-dim mt-0.5">
            {tab === "board"
              ? `${totalStories} stories on board`
              : `${allStories.length} total stories`}
            {backlog?.count ? ` / ${backlog.count} in backlog` : ""}
          </p>
        </div>
        <div className="flex gap-1">
          {/* Tab buttons */}
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
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1.5 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
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

      {/* Board tab */}
      {!loading && tab === "board" && board && (
        <>
          {totalStories > 0 ? (
            <div className="border border-grim-border rounded-lg overflow-hidden">
              {/* Column headers */}
              <div className="flex bg-grim-surface border-b border-grim-border">
                <div className="w-[220px] shrink-0 px-3 py-2 text-[9px] font-medium text-grim-text-dim border-r border-grim-border/30">
                  Story
                </div>
                {TASK_COLUMNS.map((col) => (
                  <div key={col.key} className="flex-1 min-w-[120px] px-2 py-2 text-[9px] font-medium text-grim-text-dim text-center border-r border-grim-border/20 last:border-r-0 flex items-center justify-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: col.color }} />
                    {col.label}
                  </div>
                ))}
              </div>

              {/* Feature groups + story rows */}
              {[...storiesByFeature.entries()].map(([feat, stories]) => (
                <div key={feat}>
                  <FeatureHeader featureId={feat} storyCount={stories.length} />
                  {stories.map((story) => (
                    <StoryRow
                      key={story.id}
                      story={story}
                      onMoveStory={(col) => moveStory(story.id, col)}
                      onEditStory={() => setEditItem({ item: story, type: "story" })}
                      onEditTask={(task) => setEditItem({ item: task, type: "task" })}
                      onUpdateTaskStatus={updateTaskStatus}
                      onAddTask={() => setCreateModal({ type: "task", parentId: story.id })}
                      moving={moving === story.id}
                    />
                  ))}
                  {/* Add story to feature */}
                  <div className="px-3 py-1.5 border-b border-grim-border/30">
                    <button
                      onClick={() => setCreateModal({ type: "story", parentId: feat })}
                      className="text-[8px] text-grim-text-dim hover:text-grim-accent transition-colors"
                    >
                      + Add story to {feat.replace("feat-", "")}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <IconTasks size={48} className="text-grim-accent opacity-30" />
              <p className="text-xs text-grim-text-dim">
                No stories on the board. Move stories from the Backlog tab or create new ones.
              </p>
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
          onEdit={(s) => setEditItem({ item: s, type: "story" })}
          onMoveToBoard={(id) => moveStory(id, "new")}
          moving={moving}
        />
      )}

      {/* Edit modal */}
      {editItem && (
        <EditModal
          item={editItem.item}
          type={editItem.type}
          onSave={(fields) => updateItem(editItem.item.id, fields)}
          onClose={() => setEditItem(null)}
        />
      )}

      {/* Create modal */}
      {createModal && (
        <CreateModal
          type={createModal.type}
          parentId={createModal.parentId}
          onSave={(args) => createItem(args as Parameters<typeof createItem>[0])}
          onClose={() => setCreateModal(null)}
        />
      )}
    </div>
  );
}
