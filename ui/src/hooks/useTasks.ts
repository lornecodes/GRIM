"use client";

import { useState, useEffect, useCallback, useRef } from "react";

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
  assignee?: string;
  job_id?: string;
  domain?: string;
  tags?: string[];
  created?: string;
  updated?: string;
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
  domain?: string;
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
  const [selectedDomain, setSelectedDomain] = useState<string>("");
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
        if (data && !data.error) {
          setProjects(data.projects || []);
        }
      }
    } catch {
      // non-fatal
    }
  }, [apiBase]);

  // ── Fetch board (filtered by project and/or domain) ───────────────────
  const abortRef = useRef<AbortController | null>(null);

  const fetchBoard = useCallback(async () => {
    // Cancel any in-flight request (e.g. when filter changes quickly)
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const signal = controller.signal;

    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (selectedProject) params.set("project_id", selectedProject);
      if (selectedDomain) params.set("domain", selectedDomain);
      const qs = params.toString() ? `?${params.toString()}` : "";

      const [boardRes, backlogRes, listRes] = await Promise.all([
        fetch(`${apiBase}/api/tasks/board${qs}`, { signal }),
        fetch(`${apiBase}/api/tasks/backlog${qs}`, { signal }),
        fetch(`${apiBase}/api/tasks/list${qs}`, { signal }),
      ]);

      if (!boardRes.ok) {
        const errData = await boardRes.json().catch(() => ({}));
        throw new Error(errData.error || `Board fetch failed: ${boardRes.status}`);
      }
      const boardData = await boardRes.json();
      if (boardData && boardData.columns && typeof boardData.columns === "object") {
        setBoard(boardData);
      } else {
        throw new Error(boardData?.error || "Invalid board response");
      }

      if (backlogRes.ok) {
        const backlogData = await backlogRes.json();
        if (backlogData && !backlogData.error) {
          setBacklog(backlogData);
        }
      }
      if (listRes.ok) {
        const listData = await listRes.json();
        if (listData && !listData.error) {
          setAllStories(listData.stories || []);
        }
      }
      setError(null);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Failed to load board");
    } finally {
      setLoading(false);
    }
  }, [apiBase, selectedProject, selectedDomain]);

  // ── Move story between board columns ────────────────────────────────────
  const moveStory = useCallback(async (storyId: string, column: string) => {
    setMoving(storyId);
    try {
      const res = await fetch(`${apiBase}/api/tasks/${storyId}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ column }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Move failed: ${res.status}`);
      }
      await fetchBoard();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Move failed");
    } finally {
      setMoving(null);
    }
  }, [apiBase, fetchBoard]);

  // ── Create story ──────────────────────────────────────────────────────────
  const createItem = useCallback(async (args: {
    title: string;
    proj_id: string;
    priority?: string;
    estimate_days?: number;
    description?: string;
    assignee?: string;
  }) => {
    try {
      const res = await fetch(`${apiBase}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Create failed: ${res.status}`);
      }
      const data = await res.json();
      await fetchBoard();
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
      return null;
    }
  }, [apiBase, fetchBoard]);

  // ── Update story fields ───────────────────────────────────────────────────
  const updateItem = useCallback(async (itemId: string, fields: Record<string, unknown>) => {
    try {
      const res = await fetch(`${apiBase}/api/tasks/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Update failed: ${res.status}`);
      }
      await fetchBoard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }, [apiBase, fetchBoard]);

  // ── Dispatch story to pool ────────────────────────────────────────────────
  const dispatchStory = useCallback(async (storyId: string, overrideAssignee?: string) => {
    try {
      const body: Record<string, string> = {};
      if (overrideAssignee) body.override_assignee = overrideAssignee;
      const res = await fetch(`${apiBase}/api/tasks/${storyId}/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Dispatch failed: ${res.status}`);
      }
      const data = await res.json();
      await fetchBoard();
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dispatch failed");
      return null;
    }
  }, [apiBase, fetchBoard]);

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
    selectedDomain,
    setSelectedDomain,
    loading,
    error,
    moving,
    moveStory,
    createItem,
    updateItem,
    dispatchStory,
    refresh: fetchBoard,
  };
}
