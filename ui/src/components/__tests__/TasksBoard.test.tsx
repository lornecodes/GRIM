import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock the useTasks hook — we test the component logic in isolation
// ---------------------------------------------------------------------------

const mockUseTasks = {
  board: null as unknown,
  backlog: null as unknown,
  allStories: [] as unknown[],
  projects: [] as { id: string; title: string; domain?: string }[],
  selectedProject: "",
  setSelectedProject: vi.fn(),
  selectedDomain: "",
  setSelectedDomain: vi.fn(),
  loading: false,
  error: null as string | null,
  moving: null as string | null,
  moveStory: vi.fn(),
  createItem: vi.fn(),
  updateItem: vi.fn(),
  dispatchStory: vi.fn(),
  refresh: vi.fn(),
};

vi.mock("@/hooks/useTasks", () => ({
  useTasks: () => mockUseTasks,
}));

// Mock the store for pool jobs / live transcripts
vi.mock("@/store", () => ({
  useStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ poolJobs: {}, liveTranscripts: {} }),
}));

// Import AFTER mock is set up
import { TasksBoard } from "../pages/TasksBoard";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeBoardData(overrides: Record<string, unknown> = {}) {
  return {
    columns: {
      new: [],
      active: [
        {
          id: "story-test-001",
          title: "Test Story Alpha",
          status: "active",
          priority: "high",
          project: "proj-test",
          domain: "projects",
          estimate_days: 2,
          assignee: "code",
          tags: [],
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

function resetMock() {
  mockUseTasks.board = null;
  mockUseTasks.backlog = null;
  mockUseTasks.allStories = [];
  mockUseTasks.projects = [];
  mockUseTasks.selectedProject = "";
  mockUseTasks.setSelectedProject = vi.fn();
  mockUseTasks.selectedDomain = "";
  mockUseTasks.setSelectedDomain = vi.fn();
  mockUseTasks.loading = false;
  mockUseTasks.error = null;
  mockUseTasks.moving = null;
  mockUseTasks.moveStory = vi.fn();
  mockUseTasks.createItem = vi.fn();
  mockUseTasks.updateItem = vi.fn();
  mockUseTasks.dispatchStory = vi.fn();
  mockUseTasks.refresh = vi.fn();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  resetMock();
});

describe("TasksBoard", () => {
  describe("loading state", () => {
    it("shows loading indicator when loading", () => {
      mockUseTasks.loading = true;
      render(<TasksBoard />);
      expect(screen.getByText("Loading...")).toBeDefined();
    });

    it("does not show loading when not loading", () => {
      mockUseTasks.loading = false;
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.queryByText("Loading...")).toBeNull();
    });
  });

  describe("error state", () => {
    it("shows error message when error is set", () => {
      mockUseTasks.error = "Board fetch failed: 500";
      render(<TasksBoard />);
      expect(screen.getByText(/Board fetch failed/)).toBeDefined();
    });

    it("shows retry button on error", () => {
      mockUseTasks.error = "Something broke";
      render(<TasksBoard />);
      const retryBtn = screen.getByText("retry");
      expect(retryBtn).toBeDefined();
      fireEvent.click(retryBtn);
      expect(mockUseTasks.refresh).toHaveBeenCalled();
    });
  });

  describe("empty state", () => {
    it("shows empty message when board has no active stories", () => {
      mockUseTasks.board = {
        columns: { new: [], active: [], in_progress: [], resolved: [], closed: [] },
        total_stories: 0,
      };
      render(<TasksBoard />);
      expect(screen.getByText(/No active stories on the board/)).toBeDefined();
    });
  });

  describe("board with data", () => {
    it("renders stories on the board", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.getByText("Test Story Alpha")).toBeDefined();
    });

    it("renders column headers", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.getByText("New")).toBeDefined();
      expect(screen.getByText("Active")).toBeDefined();
      expect(screen.getByText("In Progress")).toBeDefined();
      expect(screen.getByText("Resolved")).toBeDefined();
    });

    it("shows story count", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.getByText(/1 active stories/)).toBeDefined();
    });

    it("renders assignee badge for stories with agents", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.getByText("Code")).toBeDefined();
    });
  });

  describe("null safety — Object.entries crash prevention", () => {
    it("does not crash when board is null", () => {
      mockUseTasks.board = null;
      expect(() => render(<TasksBoard />)).not.toThrow();
    });

    it("does not crash when board.columns is undefined", () => {
      mockUseTasks.board = { total_stories: 0 };
      expect(() => render(<TasksBoard />)).not.toThrow();
    });

    it("does not crash when board.columns is null", () => {
      mockUseTasks.board = { columns: null, total_stories: 0 };
      expect(() => render(<TasksBoard />)).not.toThrow();
    });

    it("does not crash with empty columns object", () => {
      mockUseTasks.board = { columns: {}, total_stories: 0 };
      expect(() => render(<TasksBoard />)).not.toThrow();
    });
  });

  describe("filter bar", () => {
    it("renders domain and project dropdowns", () => {
      mockUseTasks.projects = [
        { id: "proj-grim", title: "GRIM" },
        { id: "proj-kronos", title: "Kronos" },
      ];
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);

      expect(screen.getByDisplayValue("All Domains")).toBeDefined();
      expect(screen.getByDisplayValue("All Projects")).toBeDefined();
    });

    it("calls setSelectedProject when project changes", () => {
      mockUseTasks.projects = [
        { id: "proj-grim", title: "GRIM" },
      ];
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);

      const select = screen.getByDisplayValue("All Projects");
      fireEvent.change(select, { target: { value: "proj-grim" } });
      expect(mockUseTasks.setSelectedProject).toHaveBeenCalledWith("proj-grim");
    });

    it("calls setSelectedDomain when domain changes", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);

      const select = screen.getByDisplayValue("All Domains");
      fireEvent.change(select, { target: { value: "projects" } });
      expect(mockUseTasks.setSelectedDomain).toHaveBeenCalledWith("projects");
    });
  });

  describe("tabs", () => {
    it("renders Board and Backlog tabs", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      expect(screen.getByText("Board")).toBeDefined();
      expect(screen.getByText("Backlog")).toBeDefined();
    });

    it("switches to backlog tab", () => {
      mockUseTasks.board = makeBoardData();
      mockUseTasks.allStories = [];
      render(<TasksBoard />);

      fireEvent.click(screen.getByText("Backlog"));
      expect(screen.getByText(/No stories in the backlog/)).toBeDefined();
    });
  });

  describe("closed stories and archive", () => {
    it("shows archive button when closed stories exist", () => {
      mockUseTasks.board = makeBoardData({
        columns: {
          new: [],
          active: [],
          in_progress: [],
          resolved: [],
          closed: [
            {
              id: "story-closed-001",
              title: "Closed Story",
              status: "closed",
              priority: "medium",
              project: "proj-test",
              domain: "projects",
            },
          ],
        },
        total_stories: 1,
      });
      render(<TasksBoard />);
      expect(screen.getByText(/Archive/)).toBeDefined();
    });
  });

  describe("refresh button", () => {
    it("renders refresh button", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      const btn = screen.getByText("Refresh");
      expect(btn).toBeDefined();
    });

    it("calls refresh when clicked", () => {
      mockUseTasks.board = makeBoardData();
      render(<TasksBoard />);
      fireEvent.click(screen.getByText("Refresh"));
      expect(mockUseTasks.refresh).toHaveBeenCalled();
    });
  });
});
