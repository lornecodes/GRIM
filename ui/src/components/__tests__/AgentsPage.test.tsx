import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useGrimStore } from "@/store";

// ── Mocks ──

const mockFetch = vi.fn();
global.fetch = mockFetch as any;

vi.mock("@/hooks/useGrimSocket", () => ({
  useGrimSocket: () => ({ status: "connected" as const, send: vi.fn() }),
}));

vi.mock("@/hooks/usePoolSocket", () => ({
  usePoolSocket: () => {},
  poolSubscribe: vi.fn(),
  poolUnsubscribe: vi.fn(),
  poolSubscribeAll: vi.fn(),
}));

vi.mock("@/hooks/useActiveAgents", () => ({
  useActiveAgents: () => [],
}));

vi.mock("@/hooks/usePoolStatus", () => ({
  usePoolStatus: () => ({
    poolStatus: null,
    poolEnabled: true,
    jobs: [],
    jobsByType: {},
    fetchJobsByType: vi.fn().mockResolvedValue([]),
  }),
}));

vi.mock("@/hooks/useJobDetail", () => ({
  useJobDetail: (jobId: string | null) => ({
    job: jobId ? { id: jobId, status: "running", job_type: "code", instructions: "test", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), transcript: [], priority: "normal", retry_count: 0 } : null,
    transcript: [],
    isLive: jobId ? true : false,
    loading: false,
    diff: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/components/graph/GraphStudio", () => ({
  GraphStudio: () => <div data-testid="graph-studio">GraphStudio</div>,
}));

vi.mock("@/components/pages/mission/JobKanban", () => ({
  JobKanban: () => <div data-testid="job-kanban">JobKanban</div>,
}));

vi.mock("@/components/pages/mission/SubmitJobDialog", () => ({
  SubmitJobDialog: ({ open }: { open: boolean }) => open ? <div data-testid="submit-dialog">SubmitDialog</div> : null,
}));

vi.mock("@/components/pages/studio/StudioHeader", () => ({
  StudioHeader: ({ job }: { job: any }) => <div data-testid="studio-header">StudioHeader: {job.id}</div>,
}));

vi.mock("@/components/pages/studio/LiveTranscript", () => ({
  LiveTranscript: () => <div data-testid="live-transcript">LiveTranscript</div>,
}));

vi.mock("@/components/pages/studio/DiffViewer", () => ({
  DiffViewer: () => <div data-testid="diff-viewer">DiffViewer</div>,
}));

vi.mock("@/components/pages/studio/WorkspaceBrowser", () => ({
  WorkspaceBrowser: () => <div data-testid="workspace-browser">WorkspaceBrowser</div>,
}));

vi.mock("@/components/pages/studio/AuditPanel", () => ({
  AuditPanel: () => <div data-testid="audit-panel">AuditPanel</div>,
}));

vi.mock("@/components/GrimTypingSprite", () => ({
  GrimTypingSprite: () => <span data-testid="typing-sprite" />,
}));

// Import AFTER mocks
import { AgentTeam } from "../pages/AgentTeam";

const MOCK_AGENTS = [
  { id: "companion", name: "Companion", role: "thinker", description: "Primary agent", tools: ["kronos_search"], color: "#7c6fef", toggleable: false, enabled: true },
  { id: "code", name: "Coder", role: "developer", description: "Writes code", tools: ["file_write"], color: "#22c55e", toggleable: true, enabled: true },
];

beforeEach(() => {
  mockFetch.mockImplementation((url: string) => {
    if (url.includes("/api/agents")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ agents: MOCK_AGENTS }),
      });
    }
    return Promise.resolve({ ok: false });
  });

  useGrimStore.setState({
    activePage: "agents",
    dashboardTab: "overview",
    agentsTab: "team",
    poolEnabled: true,
    poolStatus: null,
    poolMetrics: null,
    poolJobs: [],
    liveTranscripts: {},
    selectedJobId: null,
    chatPanelOpen: true,
    sidebarCollapsed: false,
    messages: [],
    isStreaming: false,
    wsStatus: "disconnected",
    sessions: [],
    activeSessionId: "",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgentTeam — tabs", () => {
  it("renders Agents title", () => {
    render(<AgentTeam />);
    expect(screen.getByText("Agents")).toBeInTheDocument();
  });

  it("shows 4 tab buttons", () => {
    render(<AgentTeam />);
    expect(screen.getByText("Team")).toBeInTheDocument();
    expect(screen.getByText("Jobs")).toBeInTheDocument();
    expect(screen.getByText("Studio")).toBeInTheDocument();
    expect(screen.getByText("Graph")).toBeInTheDocument();
  });

  it("team tab is active by default", () => {
    render(<AgentTeam />);
    const teamBtn = screen.getByText("Team");
    expect(teamBtn.className).toContain("border-grim-accent");
  });

  it("switches to jobs tab on click", () => {
    render(<AgentTeam />);
    fireEvent.click(screen.getByText("Jobs"));
    expect(useGrimStore.getState().agentsTab).toBe("jobs");
  });

  it("switches to studio tab on click", () => {
    render(<AgentTeam />);
    fireEvent.click(screen.getByText("Studio"));
    expect(useGrimStore.getState().agentsTab).toBe("studio");
  });

  it("switches to graph tab on click", () => {
    render(<AgentTeam />);
    fireEvent.click(screen.getByText("Graph"));
    expect(useGrimStore.getState().agentsTab).toBe("graph");
  });
});

describe("AgentTeam — team tab", () => {
  it("renders agent roster cards", async () => {
    render(<AgentTeam />);
    await waitFor(() => {
      expect(screen.getByText("Companion")).toBeInTheDocument();
      expect(screen.getByText("Coder")).toBeInTheDocument();
    });
  });

  it("shows agent roles", async () => {
    render(<AgentTeam />);
    await waitFor(() => {
      expect(screen.getByText("thinker")).toBeInTheDocument();
      expect(screen.getByText("developer")).toBeInTheDocument();
    });
  });
});

describe("AgentTeam — jobs tab", () => {
  it("shows kanban when on jobs tab", () => {
    useGrimStore.setState({ agentsTab: "jobs" });
    render(<AgentTeam />);
    expect(screen.getByTestId("job-kanban")).toBeInTheDocument();
  });

  it("shows submit button on jobs tab", () => {
    useGrimStore.setState({ agentsTab: "jobs" });
    render(<AgentTeam />);
    expect(screen.getByText("Submit Job")).toBeInTheDocument();
  });

  it("still shows kanban when store poolEnabled false (hook controls rendering)", () => {
    useGrimStore.setState({ agentsTab: "jobs", poolEnabled: false });
    render(<AgentTeam />);
    // usePoolStatus hook is mocked to always return poolEnabled: true
    expect(screen.getByTestId("job-kanban")).toBeInTheDocument();
  });
});

describe("AgentTeam — studio tab", () => {
  it("shows select a job message when no job selected", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: null });
    render(<AgentTeam />);
    expect(screen.getByText("No job selected. Pick one from the Jobs tab.")).toBeInTheDocument();
  });

  it("shows View Jobs button when no job selected", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: null });
    render(<AgentTeam />);
    expect(screen.getByText("View Jobs")).toBeInTheDocument();
  });

  it("View Jobs navigates to jobs tab", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: null });
    render(<AgentTeam />);
    fireEvent.click(screen.getByText("View Jobs"));
    expect(useGrimStore.getState().agentsTab).toBe("jobs");
  });

  it("shows studio header when job is selected", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: "job-abc" });
    render(<AgentTeam />);
    expect(screen.getByTestId("studio-header")).toBeInTheDocument();
  });

  it("shows transcript by default when job selected", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: "job-abc" });
    render(<AgentTeam />);
    expect(screen.getByTestId("live-transcript")).toBeInTheDocument();
  });

  it("has studio sub-tabs (Transcript, Diff, Audit)", () => {
    useGrimStore.setState({ agentsTab: "studio", selectedJobId: "job-abc" });
    render(<AgentTeam />);
    expect(screen.getByText("Transcript")).toBeInTheDocument();
    expect(screen.getByText("Diff")).toBeInTheDocument();
    expect(screen.getByText("Audit")).toBeInTheDocument();
  });
});

describe("AgentTeam — graph tab", () => {
  it("lazy-loads graph studio", async () => {
    useGrimStore.setState({ agentsTab: "graph" });
    render(<AgentTeam />);
    await waitFor(() => {
      expect(screen.getByTestId("graph-studio")).toBeInTheDocument();
    });
  });
});

describe("AgentTeam — navigateToJob integration", () => {
  it("navigateToJob sets agents page and studio tab", () => {
    useGrimStore.getState().navigateToJob("job-xyz");
    expect(useGrimStore.getState().activePage).toBe("agents");
    expect(useGrimStore.getState().agentsTab).toBe("studio");
    expect(useGrimStore.getState().selectedJobId).toBe("job-xyz");
  });
});
