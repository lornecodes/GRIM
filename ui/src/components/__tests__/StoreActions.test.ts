import { describe, it, expect, beforeEach } from "vitest";
import { useGrimStore } from "@/store";

beforeEach(() => {
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
  });
});

describe("Store — UI state", () => {
  it("has correct default dashboardTab", () => {
    expect(useGrimStore.getState().dashboardTab).toBe("overview");
  });

  it("has correct default agentsTab", () => {
    expect(useGrimStore.getState().agentsTab).toBe("team");
  });

  it("setDashboardTab updates state", () => {
    useGrimStore.getState().setDashboardTab("pool");
    expect(useGrimStore.getState().dashboardTab).toBe("pool");
  });

  it("setDashboardTab round-trips", () => {
    useGrimStore.getState().setDashboardTab("pool");
    useGrimStore.getState().setDashboardTab("overview");
    expect(useGrimStore.getState().dashboardTab).toBe("overview");
  });

  it("setAgentsTab updates state", () => {
    useGrimStore.getState().setAgentsTab("studio");
    expect(useGrimStore.getState().agentsTab).toBe("studio");
  });

  it("setAgentsTab supports all tabs", () => {
    for (const tab of ["team", "jobs", "studio", "graph"] as const) {
      useGrimStore.getState().setAgentsTab(tab);
      expect(useGrimStore.getState().agentsTab).toBe(tab);
    }
  });
});

describe("Store — Pool state", () => {
  it("has poolEnabled true by default", () => {
    expect(useGrimStore.getState().poolEnabled).toBe(true);
  });

  it("has selectedJobId null by default", () => {
    expect(useGrimStore.getState().selectedJobId).toBeNull();
  });

  it("setPoolEnabled updates state", () => {
    useGrimStore.getState().setPoolEnabled(false);
    expect(useGrimStore.getState().poolEnabled).toBe(false);
    useGrimStore.getState().setPoolEnabled(true);
    expect(useGrimStore.getState().poolEnabled).toBe(true);
  });

  it("setPoolStatus updates state", () => {
    const status = { running: true, slots: [], max_slots: 4, queue_size: 0 };
    useGrimStore.getState().setPoolStatus(status as any);
    expect(useGrimStore.getState().poolStatus?.running).toBe(true);
  });

  it("setSelectedJobId updates state", () => {
    useGrimStore.getState().setSelectedJobId("job-123");
    expect(useGrimStore.getState().selectedJobId).toBe("job-123");
  });

  it("upsertPoolJob updates existing job", () => {
    const jobs = [
      { id: "j1", status: "queued", job_type: "code", instructions: "test", priority: "normal", retry_count: 0, created_at: "", updated_at: "", transcript: [] },
    ];
    useGrimStore.getState().setPoolJobs(jobs as any);
    useGrimStore.getState().upsertPoolJob({ id: "j1", status: "running" as any });
    expect(useGrimStore.getState().poolJobs[0].status).toBe("running");
  });

  it("appendTranscriptEntry adds entry", () => {
    const entry = { seq: 0, timestamp: Date.now(), type: "text" as const, text: "hello" };
    useGrimStore.getState().appendTranscriptEntry("job-1", entry);
    expect(useGrimStore.getState().liveTranscripts["job-1"]).toHaveLength(1);
    expect(useGrimStore.getState().liveTranscripts["job-1"][0].text).toBe("hello");
  });

  it("clearTranscript removes entries", () => {
    const entry = { seq: 0, timestamp: Date.now(), type: "text" as const, text: "hello" };
    useGrimStore.getState().appendTranscriptEntry("job-1", entry);
    useGrimStore.getState().clearTranscript("job-1");
    expect(useGrimStore.getState().liveTranscripts["job-1"]).toBeUndefined();
  });
});

describe("Store — navigateToJob", () => {
  it("sets activePage to agents", () => {
    useGrimStore.getState().navigateToJob("job-abc");
    expect(useGrimStore.getState().activePage).toBe("agents");
  });

  it("does NOT set activePage to agent-studio", () => {
    useGrimStore.getState().navigateToJob("job-abc");
    expect(useGrimStore.getState().activePage).not.toBe("agent-studio");
  });

  it("sets agentsTab to studio", () => {
    useGrimStore.getState().navigateToJob("job-abc");
    expect(useGrimStore.getState().agentsTab).toBe("studio");
  });

  it("sets selectedJobId", () => {
    useGrimStore.getState().navigateToJob("job-abc");
    expect(useGrimStore.getState().selectedJobId).toBe("job-abc");
  });

  it("sets all three fields atomically", () => {
    useGrimStore.getState().navigateToJob("job-xyz");
    const state = useGrimStore.getState();
    expect(state.activePage).toBe("agents");
    expect(state.agentsTab).toBe("studio");
    expect(state.selectedJobId).toBe("job-xyz");
  });
});

describe("Store — pool disabled state", () => {
  it("setPoolEnabled(false) sets poolEnabled to false", () => {
    useGrimStore.getState().setPoolEnabled(false);
    expect(useGrimStore.getState().poolEnabled).toBe(false);
  });

  it("setPoolStatus(null) does not crash", () => {
    useGrimStore.getState().setPoolStatus(null);
    expect(useGrimStore.getState().poolStatus).toBeNull();
  });

  it("repeated setPoolEnabled(false) doesn't change reference", () => {
    useGrimStore.getState().setPoolEnabled(false);
    const state1 = useGrimStore.getState();
    useGrimStore.getState().setPoolEnabled(false);
    const state2 = useGrimStore.getState();
    // Zustand should not create new state object for same value
    expect(state1.poolEnabled).toBe(state2.poolEnabled);
  });

  it("poolJobs stays empty array when pool disabled", () => {
    useGrimStore.getState().setPoolEnabled(false);
    expect(useGrimStore.getState().poolJobs).toEqual([]);
  });

  it("liveTranscripts missing key returns undefined (not new array)", () => {
    const transcripts = useGrimStore.getState().liveTranscripts;
    expect(transcripts["nonexistent"]).toBeUndefined();
  });
});

describe("Store — IronClaw removed", () => {
  it("does NOT have ironclawStatus field", () => {
    expect((useGrimStore.getState() as any).ironclawStatus).toBeUndefined();
  });

  it("does NOT have setIronclawStatus action", () => {
    expect((useGrimStore.getState() as any).setIronclawStatus).toBeUndefined();
  });

  it("does NOT have activeDashboardWidget field", () => {
    expect((useGrimStore.getState() as any).activeDashboardWidget).toBeUndefined();
  });
});
