import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

vi.mock("@/hooks/useBridgeApi", () => ({
  useBridgeApi: () => ({ summary: null, recent: [], loading: false, error: false }),
}));

vi.mock("@/hooks/useActiveAgents", () => ({
  useActiveAgents: () => [],
}));

vi.mock("@/hooks/useGrimMemory", () => ({
  useGrimMemory: () => ({ memory: null, loading: false, error: false }),
}));

vi.mock("@/hooks/useSkills", () => ({
  useSkills: () => ({ skills: [], loading: false }),
}));

vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({ models: [], routing: null, loading: false }),
}));

vi.mock("@/hooks/useGrimConfig", () => ({
  useGrimConfig: () => ({ config: null, loading: true }),
}));

vi.mock("@/components/ui/KnowledgeGraph", () => ({
  KnowledgeGraph: () => <div data-testid="knowledge-graph" />,
  DOMAIN_COLORS: {},
}));

vi.mock("@/components/GrimTypingSprite", () => ({
  GrimTypingSprite: () => <span data-testid="typing-sprite" />,
}));

vi.mock("@/components/pages/mission/MetricsBar", () => ({
  MetricsBar: () => <div data-testid="metrics-bar">MetricsBar</div>,
}));

vi.mock("@/components/pages/mission/SlotGrid", () => ({
  SlotGrid: () => <div data-testid="slot-grid">SlotGrid</div>,
}));

vi.mock("@/components/pages/mission/JobKanban", () => ({
  JobKanban: () => <div data-testid="job-kanban">JobKanban</div>,
}));

vi.mock("@/components/pages/mission/SubmitJobDialog", () => ({
  SubmitJobDialog: () => <div data-testid="submit-dialog" />,
}));

// Import AFTER mocks
import { DashboardHome } from "../pages/DashboardHome";

beforeEach(() => {
  mockFetch.mockResolvedValue({ ok: false });
  useGrimStore.setState({
    activePage: "dashboard",
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

describe("DashboardHome — tabs", () => {
  it("renders Dashboard title", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it("renders Overview and Pool tab buttons", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Pool")).toBeInTheDocument();
  });

  it("shows overview tab content by default", () => {
    render(<DashboardHome />);
    expect(screen.getByText("System Health")).toBeInTheDocument();
    expect(screen.getByText("Execution Pool")).toBeInTheDocument();
  });

  it("switches to pool tab on click", () => {
    render(<DashboardHome />);
    fireEvent.click(screen.getByText("Pool"));
    expect(useGrimStore.getState().dashboardTab).toBe("pool");
  });

  it("shows pool tab content when dashboardTab is pool", () => {
    useGrimStore.setState({ dashboardTab: "pool" });
    render(<DashboardHome />);
    expect(screen.getByTestId("metrics-bar")).toBeInTheDocument();
    expect(screen.getByTestId("slot-grid")).toBeInTheDocument();
    expect(screen.getByTestId("job-kanban")).toBeInTheDocument();
  });

  it("hides overview content when on pool tab", () => {
    useGrimStore.setState({ dashboardTab: "pool" });
    render(<DashboardHome />);
    expect(screen.queryByText("System Health")).not.toBeInTheDocument();
  });

  it("pool tab shows offline message when pool disabled", () => {
    useGrimStore.setState({ dashboardTab: "pool", poolEnabled: false });
    render(<DashboardHome />);
    expect(screen.getByText("Pool Offline")).toBeInTheDocument();
  });

  it("pool tab shows submit button when pool enabled", () => {
    useGrimStore.setState({
      dashboardTab: "pool",
      poolEnabled: true,
      poolStatus: { running: true, slots: [], max_slots: 4, queue_size: 0 } as any,
    });
    render(<DashboardHome />);
    expect(screen.getByText("Submit Job")).toBeInTheDocument();
  });
});

describe("DashboardHome — overview tiles", () => {
  it("renders health widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("System Health")).toBeInTheDocument();
  });

  it("renders pool widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Execution Pool")).toBeInTheDocument();
  });

  it("renders memory widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Working Memory")).toBeInTheDocument();
  });

  it("renders skills widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Skills")).toBeInTheDocument();
  });

  it("renders models widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Models")).toBeInTheDocument();
  });

  it("renders settings widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("renders knowledge graph widget tile", () => {
    render(<DashboardHome />);
    expect(screen.getByText("Knowledge Graph")).toBeInTheDocument();
  });

  it("does NOT render IronClaw or Engine tile", () => {
    render(<DashboardHome />);
    expect(screen.queryByText("IronClaw")).not.toBeInTheDocument();
    expect(screen.queryByText("Engine")).not.toBeInTheDocument();
  });

  it("pool widget shows pool offline when disabled", () => {
    useGrimStore.setState({ poolEnabled: false });
    render(<DashboardHome />);
    expect(screen.getByText("Pool offline")).toBeInTheDocument();
  });

  it("pool widget Open Pool button switches to pool tab", () => {
    useGrimStore.setState({
      poolEnabled: true,
      poolStatus: { running: true, slots: [], max_slots: 4, queue_size: 0 } as any,
    });
    render(<DashboardHome />);
    fireEvent.click(screen.getByText("Open Pool"));
    expect(useGrimStore.getState().dashboardTab).toBe("pool");
  });
});

describe("DashboardHome — no ironclaw", () => {
  it("health tile does not show IronClaw row", () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: "ok", env: "test", vault: "/v", graph: true }),
    });
    render(<DashboardHome />);
    // IronClaw row would have text "IronClaw" — it should not be present
    expect(screen.queryByText("IronClaw")).not.toBeInTheDocument();
  });

  it("no claw text anywhere in dashboard", () => {
    render(<DashboardHome />);
    const container = document.body;
    expect(container.textContent).not.toContain("claw");
    expect(container.textContent).not.toContain("ironclaw");
  });
});
