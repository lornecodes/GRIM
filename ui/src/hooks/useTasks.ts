"use client";

import { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TaskItem {
  id: string;
  title: string;
  status: string;
  priority?: string;
  estimate_days?: number;
  description?: string;
  notes?: string;
  assignee?: string;
  tasks?: TaskItem[];
  task_count?: number;
  tasks_done?: number;
  tags?: string[];
  created?: string;
  updated?: string;
  feature?: string;
  project?: string;
  acceptance_criteria?: string[];
  log?: string[];
}

export interface BoardData {
  columns: Record<string, TaskItem[]>;
  total_stories?: number;
  last_synced?: string;
}

export interface BacklogData {
  backlog: TaskItem[];
  count: number;
}

export interface ProjectInfo {
  id: string;
  title: string;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTasks() {
  const [board, setBoard] = useState<BoardData | null>(null);
  const [backlog, setBacklog] = useState<BacklogData | null>(null);
  const [allStories, setAllStories] = useState<TaskItem[]>([]);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  // ── Fetch projects list ─────────────────────────────────────────────────
  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/projects`);
      if (res.ok) {
        const data = await res.json();
        setProjects(data.projects || []);
      }
    } catch {
      // non-fatal
    }
  }, [apiBase]);

  // ── Fetch board (filtered by project) ───────────────────────────────────
  const fetchBoard = useCallback(async () => {
    try {
      setLoading(true);
      const projectParam = selectedProject ? `?project_id=${selectedProject}` : "";
      const [boardRes, backlogRes, listRes] = await Promise.all([
        fetch(`${apiBase}/api/tasks/board${projectParam}`),
        fetch(`${apiBase}/api/tasks/backlog${projectParam ? `?project_id=${selectedProject}` : ""}`),
        fetch(`${apiBase}/api/tasks/list${projectParam ? `?project_id=${selectedProject}` : ""}`),
      ]);
      if (!boardRes.ok) throw new Error(`Board fetch failed: ${boardRes.status}`);
      const boardData = await boardRes.json();
      setBoard(boardData);
      if (backlogRes.ok) {
        setBacklog(await backlogRes.json());
      }
      if (listRes.ok) {
        const listData = await listRes.json();
        setAllStories(listData.stories || []);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load board");
    } finally {
      setLoading(false);
    }
  }, [apiBase, selectedProject]);

  // ── Move story between board columns ────────────────────────────────────
  const moveStory = useCallback(async (storyId: string, column: string) => {
    setMoving(storyId);
    try {
      const res = await fetch(`${apiBase}/api/tasks/${storyId}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ column }),
      });
      if (!res.ok) throw new Error(`Move failed: ${res.status}`);
      await fetchBoard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Move failed");
    } finally {
      setMoving(null);
    }
  }, [apiBase, fetchBoard]);

  // ── Create story or task ────────────────────────────────────────────────
  const createItem = useCallback(async (args: {
    type: string;
    title: string;
    feat_id?: string;
    story_id?: string;
    priority?: string;
    estimate_days?: number;
    description?: string;
    notes?: string;
    assignee?: string;
  }) => {
    try {
      const res = await fetch(`${apiBase}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
      if (!res.ok) throw new Error(`Create failed: ${res.status}`);
      const data = await res.json();
      await fetchBoard();
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
      return null;
    }
  }, [apiBase, fetchBoard]);

  // ── Update story or task fields ─────────────────────────────────────────
  const updateItem = useCallback(async (itemId: string, fields: Record<string, unknown>) => {
    try {
      const res = await fetch(`${apiBase}/api/tasks/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!res.ok) throw new Error(`Update failed: ${res.status}`);
      await fetchBoard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }, [apiBase, fetchBoard]);

  // ── Update task status (quick move) ─────────────────────────────────────
  const updateTaskStatus = useCallback(async (taskId: string, status: string) => {
    await updateItem(taskId, { status });
  }, [updateItem]);

  // ── Initial load ────────────────────────────────────────────────────────
  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  useEffect(() => {
    fetchBoard();
  }, [fetchBoard]);

  return {
    board,
    backlog,
    allStories,
    projects,
    selectedProject,
    setSelectedProject,
    loading,
    error,
    moving,
    moveStory,
    createItem,
    updateItem,
    updateTaskStatus,
    refresh: fetchBoard,
  };
}
