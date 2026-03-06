import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { useGrimStore } from "@/store";
import { pages } from "@/components/pages/PageRegistry";

// Mock heavy dependencies
vi.mock("@/hooks/useGrimSocket", () => ({
  useGrimSocket: () => ({ status: "connected" as const, send: vi.fn() }),
}));

vi.mock("@/hooks/usePoolSocket", () => ({
  usePoolSocket: () => {},
  poolSubscribe: vi.fn(),
  poolUnsubscribe: vi.fn(),
  poolSubscribeAll: vi.fn(),
}));

beforeEach(() => {
  useGrimStore.setState({
    activePage: "dashboard",
    dashboardTab: "overview",
    agentsTab: "team",
    chatPanelOpen: true,
    sidebarCollapsed: false,
    poolEnabled: true,
    poolStatus: null,
    poolMetrics: null,
    poolJobs: [],
    liveTranscripts: {},
    selectedJobId: null,
    messages: [],
    isStreaming: false,
    wsStatus: "disconnected",
    sessions: [],
    activeSessionId: "",
  });
});

describe("PageRegistry", () => {
  it("has exactly 11 main pages + 1 system page", () => {
    const mainPages = pages.filter((p) => p.section === "main");
    const systemPages = pages.filter((p) => p.section === "system");
    expect(mainPages).toHaveLength(10);
    expect(systemPages).toHaveLength(1);
  });

  it("does not contain engine page", () => {
    expect(pages.find((p) => p.id === "engine")).toBeUndefined();
  });

  it("does not contain eval page", () => {
    expect(pages.find((p) => p.id === "eval")).toBeUndefined();
  });

  it("does not contain mission-control page", () => {
    expect(pages.find((p) => p.id === "mission-control")).toBeUndefined();
  });

  it("does not contain agent-studio page", () => {
    expect(pages.find((p) => p.id === "agent-studio")).toBeUndefined();
  });

  it("has agents page labeled 'Agents' not 'Agent Team'", () => {
    const agentsPage = pages.find((p) => p.id === "agents");
    expect(agentsPage?.label).toBe("Agents");
  });

  it("has dashboard page", () => {
    expect(pages.find((p) => p.id === "dashboard")).toBeDefined();
  });

  it("has settings as system page", () => {
    const settings = pages.find((p) => p.id === "settings");
    expect(settings?.section).toBe("system");
  });

  it("includes all expected main pages", () => {
    const expectedIds = [
      "dashboard", "tokens", "vault", "agents", "skills",
      "models", "tasks", "calendar", "memory", "evolution",
    ];
    for (const id of expectedIds) {
      expect(pages.find((p) => p.id === id)).toBeDefined();
    }
  });

  it("every page has a component", () => {
    for (const page of pages) {
      expect(page.component).toBeDefined();
    }
  });

  it("every page has an icon", () => {
    for (const page of pages) {
      expect(page.icon).toBeDefined();
    }
  });
});

describe("Store navigation actions", () => {
  it("navigateToJob sets activePage to agents", () => {
    useGrimStore.getState().navigateToJob("job-1");
    expect(useGrimStore.getState().activePage).toBe("agents");
  });

  it("navigateToJob does NOT set agent-studio", () => {
    useGrimStore.getState().navigateToJob("job-1");
    expect(useGrimStore.getState().activePage).not.toBe("agent-studio");
  });

  it("navigateToJob sets agentsTab to studio", () => {
    useGrimStore.getState().navigateToJob("job-1");
    expect(useGrimStore.getState().agentsTab).toBe("studio");
  });

  it("setDashboardTab updates dashboardTab", () => {
    useGrimStore.getState().setDashboardTab("pool");
    expect(useGrimStore.getState().dashboardTab).toBe("pool");
  });

  it("setAgentsTab updates agentsTab", () => {
    useGrimStore.getState().setAgentsTab("jobs");
    expect(useGrimStore.getState().agentsTab).toBe("jobs");
  });

  it("no ironclawStatus in store", () => {
    expect((useGrimStore.getState() as any).ironclawStatus).toBeUndefined();
  });
});

describe("Dead page ID strings eliminated", () => {
  it("no page has id mission-control", () => {
    expect(pages.some((p) => p.id === "mission-control")).toBe(false);
  });

  it("no page has id agent-studio", () => {
    expect(pages.some((p) => p.id === "agent-studio")).toBe(false);
  });

  it("no page has id engine", () => {
    expect(pages.some((p) => p.id === "engine")).toBe(false);
  });

  it("no page has id eval", () => {
    expect(pages.some((p) => p.id === "eval")).toBe(false);
  });
});
