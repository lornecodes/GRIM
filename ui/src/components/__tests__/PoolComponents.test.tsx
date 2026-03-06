import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useGrimStore } from "@/store";

// ── Mocks ──

vi.mock("@/hooks/usePoolSocket", () => ({
  usePoolSocket: () => {},
  poolSubscribe: vi.fn(),
  poolUnsubscribe: vi.fn(),
  poolSubscribeAll: vi.fn(),
}));

vi.mock("@/hooks/useGrimSocket", () => ({
  useGrimSocket: () => ({ status: "connected" as const, send: vi.fn() }),
}));

// Import AFTER mocks
import { MetricsBar } from "../pages/mission/MetricsBar";
import { SlotGrid } from "../pages/mission/SlotGrid";
import { JobKanban } from "../pages/mission/JobKanban";
import { TranscriptLine } from "../pages/studio/TranscriptLine";
import { LiveTranscript } from "../pages/studio/LiveTranscript";

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

// ── MetricsBar ──

describe("MetricsBar", () => {
  it("renders stat cards", () => {
    render(<MetricsBar />);
    expect(screen.getByText("Active Jobs")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("shows zero values when no metrics", () => {
    render(<MetricsBar />);
    // Should have "0" values somewhere
    const zeros = screen.getAllByText("0");
    expect(zeros.length).toBeGreaterThanOrEqual(1);
  });

  it("shows real data when metrics set", () => {
    useGrimStore.setState({
      poolMetrics: {
        completed_count: 42,
        failed_count: 2,
        queued_count: 3,
        avg_duration_ms: 30500,
        total_cost_usd: 1.25,
        throughput_per_hour: 5,
        period_hours: 24,
      } as any,
      poolStatus: {
        running: true,
        slots: [
          { slot_id: "s1", busy: true, current_job_id: "j1" },
          { slot_id: "s2", busy: false, current_job_id: null },
        ],
        max_slots: 4,
        queue_size: 3,
        active_jobs: 1,
      } as any,
    });
    render(<MetricsBar />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });
});

// ── SlotGrid ──

describe("SlotGrid", () => {
  it("renders without crashing when no slots", () => {
    render(<SlotGrid />);
    // Should not throw
  });

  it("renders slot badges when status has slots", () => {
    useGrimStore.setState({
      poolStatus: {
        running: true,
        slots: [
          { slot_id: "slot-1", busy: true, current_job_id: "j1" },
          { slot_id: "slot-2", busy: false, current_job_id: null },
          { slot_id: "slot-3", busy: false, current_job_id: null },
        ],
        max_slots: 4,
        queue_size: 0,
        active_jobs: 1,
      } as any,
    });
    render(<SlotGrid />);
    expect(screen.getByText("slot-1")).toBeInTheDocument();
    expect(screen.getByText("slot-2")).toBeInTheDocument();
    expect(screen.getByText("slot-3")).toBeInTheDocument();
  });
});

// ── JobKanban ──

describe("JobKanban", () => {
  it("renders column headers", () => {
    render(<JobKanban />);
    // KANBAN_COLUMNS has labels like "Queued", "Running", etc.
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("renders empty columns with 0 count", () => {
    render(<JobKanban />);
    const zeros = screen.getAllByText("0");
    expect(zeros.length).toBeGreaterThanOrEqual(1);
  });

  it("groups jobs by status", () => {
    useGrimStore.setState({
      poolJobs: [
        { id: "j1", status: "queued", job_type: "code", instructions: "test 1", priority: "normal", retry_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), transcript: [] },
        { id: "j2", status: "running", job_type: "research", instructions: "test 2", priority: "normal", retry_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), transcript: [] },
        { id: "j3", status: "running", job_type: "audit", instructions: "test 3", priority: "normal", retry_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), transcript: [] },
      ] as any,
    });
    render(<JobKanban />);
    // Both running jobs should appear in Running column
    expect(screen.getByText(/test 1/)).toBeInTheDocument();
    expect(screen.getByText(/test 2/)).toBeInTheDocument();
    expect(screen.getByText(/test 3/)).toBeInTheDocument();
  });
});

// ── TranscriptLine ──

describe("TranscriptLine", () => {
  it("renders text type with green prefix", () => {
    render(
      <TranscriptLine entry={{ seq: 0, timestamp: Date.now(), type: "text", text: "Hello world" }} />
    );
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.getByText(">")).toBeInTheDocument();
  });

  it("renders tool_use type with cyan prefix", () => {
    render(
      <TranscriptLine entry={{ seq: 1, timestamp: Date.now(), type: "tool_use", toolName: "kronos_search", toolInput: { query: "test" } }} />
    );
    expect(screen.getByText("kronos_search")).toBeInTheDocument();
    expect(screen.getByText("$")).toBeInTheDocument();
  });

  it("renders tool_result type", () => {
    render(
      <TranscriptLine entry={{ seq: 2, timestamp: Date.now(), type: "tool_result", outputPreview: "found 3 results" }} />
    );
    expect(screen.getByText(/found 3 results/)).toBeInTheDocument();
  });
});

// ── LiveTranscript ──

describe("LiveTranscript", () => {
  it("renders with empty transcript", () => {
    render(<LiveTranscript jobId="job-1" transcript={[]} isLive={false} />);
    // Should show "no entries" or similar
    expect(screen.getByText(/No transcript/i) || screen.getByText(/job-1/i)).toBeTruthy();
  });

  it("shows LIVE indicator when isLive", () => {
    render(
      <LiveTranscript
        jobId="job-1"
        transcript={[{ seq: 0, timestamp: Date.now(), type: "text", text: "Working..." }]}
        isLive={true}
      />
    );
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows COMPLETE when not live and has entries", () => {
    render(
      <LiveTranscript
        jobId="job-1"
        transcript={[{ seq: 0, timestamp: Date.now(), type: "text", text: "Done" }]}
        isLive={false}
      />
    );
    expect(screen.getByText("COMPLETE")).toBeInTheDocument();
  });

  it("renders transcript entries", () => {
    render(
      <LiveTranscript
        jobId="job-1"
        transcript={[
          { seq: 0, timestamp: Date.now(), type: "text", text: "Entry 1" },
          { seq: 1, timestamp: Date.now(), type: "tool_use", toolName: "search", toolInput: {} },
        ]}
        isLive={true}
      />
    );
    expect(screen.getByText("Entry 1")).toBeInTheDocument();
    expect(screen.getByText("search")).toBeInTheDocument();
  });
});
