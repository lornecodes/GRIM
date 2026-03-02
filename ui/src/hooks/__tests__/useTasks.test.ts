import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useTasks } from "../useTasks";

// ---------------------------------------------------------------------------
// Mock fetch
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockBoardResponse(overrides: Record<string, unknown> = {}) {
  return {
    columns: {
      new: [],
      active: [
        {
          id: "story-test-001",
          title: "Test Story",
          status: "active",
          priority: "high",
          feature: "feat-test",
          project: "proj-test",
          tasks: [],
          task_count: 0,
          tasks_done: 0,
        },
      ],
      in_progress: [],
      resolved: [],
      closed: [],
    },
    total_stories: 1,
    last_synced: "2026-03-01T12:00:00",
    ...overrides,
  };
}

function mockBacklogResponse() {
  return { backlog: [], count: 0 };
}

function mockListResponse() {
  return { stories: [], count: 0 };
}

function mockProjectsResponse() {
  return {
    projects: [
      { id: "proj-test", title: "Test Project" },
      { id: "proj-other", title: "Other Project" },
    ],
  };
}

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  } as Response;
}

function errorJson(data: unknown, status = 500): Response {
  return {
    ok: false,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

function setupSuccessfulFetch() {
  mockFetch.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
    if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
    if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
    if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
    return Promise.resolve(okJson({}));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_GRIM_API", "");
  mockFetch.mockReset();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("useTasks", () => {
  describe("initial load", () => {
    it("fetches board data on mount", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.board).not.toBeNull();
      expect(result.current.board?.columns).toBeDefined();
      expect(result.current.error).toBeNull();
    });

    it("fetches projects on mount", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());

      await waitFor(() => expect(result.current.projects.length).toBeGreaterThan(0));
      expect(result.current.projects).toHaveLength(2);
      expect(result.current.projects[0].id).toBe("proj-test");
    });

    it("sets loading true initially", () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());
      expect(result.current.loading).toBe(true);
    });

    it("sets loading false after fetch completes", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
    });
  });

  describe("error handling", () => {
    it("sets error when board fetch returns HTTP 500", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board"))
          return Promise.resolve(errorJson({ error: "MCP call kronos_board_view timed out" }));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.error).toBe("MCP call kronos_board_view timed out");
    });

    it("does not set board state when response has no columns", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        // Return 200 but with error body (legacy behavior before fix)
        if (url.includes("/api/tasks/board"))
          return Promise.resolve(okJson({ error: "No MCP session" }));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      // Board should remain null since response had no columns
      expect(result.current.board).toBeNull();
      // Error message comes from the error field in the response
      expect(result.current.error).toBe("No MCP session");
    });

    it("sets error when network request fails", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.reject(new Error("Network error"));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.error).toBe("Network error");
    });

    it("ignores backlog errors gracefully", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog"))
          return Promise.resolve(errorJson({ error: "backlog failed" }));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      // Board should still be set even though backlog failed
      expect(result.current.board).not.toBeNull();
      expect(result.current.error).toBeNull();
    });

    it("ignores list errors gracefully", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list"))
          return Promise.resolve(errorJson({ error: "list failed" }));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.board).not.toBeNull();
      expect(result.current.error).toBeNull();
    });

    it("handles projects fetch failure silently", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.reject(new Error("nope"));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      // Projects should be empty, no error displayed
      expect(result.current.projects).toEqual([]);
      expect(result.current.board).not.toBeNull();
    });
  });

  describe("project selection", () => {
    it("refetches board when project changes", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());

      await waitFor(() => expect(result.current.loading).toBe(false));
      const callsBefore = mockFetch.mock.calls.length;

      act(() => {
        result.current.setSelectedProject("proj-test");
      });

      await waitFor(() => expect(result.current.loading).toBe(false));
      // Should have made new fetch calls
      expect(mockFetch.mock.calls.length).toBeGreaterThan(callsBefore);
    });

    it("includes project_id in board fetch URL", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => {
        result.current.setSelectedProject("proj-test");
      });

      await waitFor(() => expect(result.current.loading).toBe(false));
      const boardCalls = mockFetch.mock.calls.filter(
        (c: [string, ...unknown[]]) => typeof c[0] === "string" && c[0].includes("/api/tasks/board")
      );
      const lastBoardCall = boardCalls[boardCalls.length - 1];
      expect(lastBoardCall[0]).toContain("project_id=proj-test");
    });
  });

  describe("mutations", () => {
    it("moveStory calls POST and refreshes", async () => {
      setupSuccessfulFetch();
      mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
        if (opts?.method === "POST" && url.includes("/move"))
          return Promise.resolve(okJson({ moved: "story-001", to: "active" }));
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.moveStory("story-001", "active");
      });

      expect(result.current.moving).toBeNull();
    });

    it("moveStory sets error on failure", async () => {
      setupSuccessfulFetch();
      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Override to fail on move
      mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
        if (opts?.method === "POST" && url.includes("/move"))
          return Promise.resolve(errorJson({ error: "Story not found" }));
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      await act(async () => {
        await result.current.moveStory("story-bad", "active");
      });

      expect(result.current.error).toBe("Story not found");
    });

    it("createItem returns data on success", async () => {
      mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
        if (opts?.method === "POST" && !url.includes("/move"))
          return Promise.resolve(okJson({ created: "story-new-001", feature: "feat-test" }));
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      let data: unknown;
      await act(async () => {
        data = await result.current.createItem({
          type: "story",
          title: "New story",
          feat_id: "feat-test",
        });
      });

      expect(data).toEqual({ created: "story-new-001", feature: "feat-test" });
    });

    it("createItem returns null on failure", async () => {
      mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
        if (opts?.method === "POST" && !url.includes("/move"))
          return Promise.resolve(errorJson({ error: "feat_id required" }));
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      let data: unknown;
      await act(async () => {
        data = await result.current.createItem({ type: "story", title: "Bad" });
      });

      expect(data).toBeNull();
      expect(result.current.error).toBeTruthy();
    });

    it("updateItem refreshes board on success", async () => {
      setupSuccessfulFetch();
      mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
        if (opts?.method === "PUT")
          return Promise.resolve(okJson({ updated: "story-001", fields_changed: ["title"] }));
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.updateItem("story-001", { title: "Updated" });
      });

      expect(result.current.error).toBeNull();
    });
  });

  describe("data shape validation", () => {
    it("handles board response with null columns gracefully", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board"))
          return Promise.resolve(okJson({ columns: null, total_stories: 0 }));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.board).toBeNull();
      expect(result.current.error).toBe("Invalid board response");
    });

    it("handles empty stories list response", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog")) return Promise.resolve(okJson(mockBacklogResponse()));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson({ stories: null }));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      // stories should default to empty array
      expect(result.current.allStories).toEqual([]);
    });

    it("handles backlog response with error field", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return Promise.resolve(okJson(mockProjectsResponse()));
        if (url.includes("/api/tasks/board")) return Promise.resolve(okJson(mockBoardResponse()));
        if (url.includes("/api/tasks/backlog"))
          return Promise.resolve(okJson({ error: "something broke" }));
        if (url.includes("/api/tasks/list")) return Promise.resolve(okJson(mockListResponse()));
        return Promise.resolve(okJson({}));
      });

      const { result } = renderHook(() => useTasks());
      await waitFor(() => expect(result.current.loading).toBe(false));
      // Should not crash, backlog stays null
      expect(result.current.board).not.toBeNull();
    });
  });
});
