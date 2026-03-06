import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useGrimStore } from "@/store";
import type { ChatMessage, TraceEvent } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock react-force-graph-2d (Canvas component that doesn't work in JSDOM)
vi.mock("react-force-graph-2d", () => ({
  __esModule: true,
  default: vi.fn(({ graphData, onNodeClick }: any) => (
    <div data-testid="force-graph">
      {graphData?.nodes?.map((n: any) => (
        <button
          key={n.id}
          data-testid={`node-${n.id}`}
          onClick={() => onNodeClick?.(n)}
        >
          {n.name}
        </button>
      ))}
    </div>
  )),
}));

// Mock next/dynamic to render synchronously
vi.mock("next/dynamic", () => ({
  __esModule: true,
  default: (loader: () => Promise<any>) => {
    // Resolve the dynamic import synchronously for tests
    const Component = vi.fn((props: any) => {
      const Mod = require("react-force-graph-2d").default;
      return <Mod {...props} />;
    });
    return Component;
  },
}));

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Mock ResizeObserver
global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const MOCK_TOPOLOGY = {
  nodes: {
    identity: {
      id: "identity", name: "Identity", role: "preprocessor",
      description: "Injects system prompt", tools: [], color: "#8888a0",
      tier: "grim", toggleable: false, node_type: "preprocessing",
      enabled: true, col: 0, row: 0,
    },
    companion: {
      id: "companion", name: "Companion", role: "thinker",
      description: "Primary conversational agent", tools: ["kronos_search"],
      color: "#7c6fef", tier: "grim", toggleable: false, node_type: "companion",
      enabled: true, col: 6, row: -1,
    },
    audit: {
      id: "audit", name: "Audit", role: "review",
      description: "Staging review", tools: ["staging_read"],
      color: "#f97316", tier: "grim", toggleable: true, node_type: "agent",
      enabled: true, col: 8, row: 1,
      routing_rules: [
        { condition: "IronClaw artifacts", target: "audit" },
        { condition: "skip", target: "integrate" },
      ],
    },
  },
  edges: [
    { source: "identity", target: "companion", type: "static" },
  ],
  node_count: 3,
  edge_count: 1,
};

const MOCK_SESSIONS = { active: 2, session_ids: ["s1", "s2"] };

function setupFetchMock() {
  mockFetch.mockImplementation((url: string) => {
    if (url.includes("/api/graph/topology")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_TOPOLOGY),
      });
    }
    if (url.includes("/api/graph/sessions")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_SESSIONS),
      });
    }
    if (url.includes("/api/agents/")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ enabled: false }),
      });
    }
    return Promise.resolve({ ok: false, status: 404 });
  });
}

// ---------------------------------------------------------------------------
// Imports (after mocks)
// ---------------------------------------------------------------------------

import { GraphStatusBar } from "../graph/GraphStatusBar";
import { NodeInspector } from "../graph/NodeInspector";

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockFetch.mockReset();
  setupFetchMock();
  useGrimStore.setState({
    messages: [],
    isStreaming: false,
    wsStatus: "disconnected",
    sessions: [],
    activeSessionId: "",
    chatPanelOpen: true,
    activePage: "dashboard",
    sidebarCollapsed: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

// ═══════════════════════════════════════════════════════════════════════════
// GraphStatusBar Tests
// ═══════════════════════════════════════════════════════════════════════════

describe("GraphStatusBar", () => {
  it("renders without crashing", () => {
    render(
      <GraphStatusBar
        activeSessions={0}
        nodeCount={15}
        edgeCount={19}
        isStreaming={false}
      />
    );
  });

  it("shows session count", () => {
    render(
      <GraphStatusBar
        activeSessions={3}
        nodeCount={15}
        edgeCount={19}
        isStreaming={false}
      />
    );
    expect(screen.getByText(/3 active sessions/)).toBeInTheDocument();
  });

  it("shows singular session", () => {
    render(
      <GraphStatusBar
        activeSessions={1}
        nodeCount={15}
        edgeCount={19}
        isStreaming={false}
      />
    );
    expect(screen.getByText(/1 active session$/)).toBeInTheDocument();
  });

  it("shows node and edge counts", () => {
    render(
      <GraphStatusBar
        activeSessions={0}
        nodeCount={15}
        edgeCount={19}
        isStreaming={false}
      />
    );
    expect(screen.getByText("15 nodes")).toBeInTheDocument();
    expect(screen.getByText("19 edges")).toBeInTheDocument();
  });

  it("shows executing indicator when streaming", () => {
    render(
      <GraphStatusBar
        activeSessions={1}
        nodeCount={15}
        edgeCount={19}
        isStreaming={true}
      />
    );
    expect(screen.getByText("Executing")).toBeInTheDocument();
  });

  it("hides executing indicator when not streaming", () => {
    render(
      <GraphStatusBar
        activeSessions={1}
        nodeCount={15}
        edgeCount={19}
        isStreaming={false}
      />
    );
    expect(screen.queryByText("Executing")).toBeNull();
  });

  it("shows zero sessions", () => {
    render(
      <GraphStatusBar
        activeSessions={0}
        nodeCount={0}
        edgeCount={0}
        isStreaming={false}
      />
    );
    expect(screen.getByText(/0 active sessions/)).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// NodeInspector Tests
// ═══════════════════════════════════════════════════════════════════════════

describe("NodeInspector", () => {
  const baseNode = MOCK_TOPOLOGY.nodes.companion;
  const emptyOverlay = {
    isStreaming: false,
    nodes: {},
    edges: {},
    activeNodeId: null,
  };

  it("renders node name", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Companion")).toBeInTheDocument();
  });

  it("renders node description", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(
      screen.getByText("Primary conversational agent")
    ).toBeInTheDocument();
  });

  it("shows idle state when no overlay data", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Idle")).toBeInTheDocument();
  });

  it("shows active state when node is active", () => {
    const overlay = {
      ...emptyOverlay,
      nodes: { companion: { active: true, completed: false, durationMs: 0, path: true } },
    };
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={overlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("shows completed state with duration", () => {
    const overlay = {
      ...emptyOverlay,
      nodes: { companion: { active: false, completed: true, durationMs: 450, path: true } },
    };
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={overlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("450ms")).toBeInTheDocument();
  });

  it("shows tools count and expands", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    const toolsBtn = screen.getByText(/tools/i);
    expect(toolsBtn).toBeInTheDocument();
    fireEvent.click(toolsBtn);
    expect(screen.getByText("kronos_search")).toBeInTheDocument();
  });

  it("shows always-on badge for non-toggleable nodes", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("always on")).toBeInTheDocument();
  });

  it("shows toggle for toggleable agents", () => {
    const auditNode = MOCK_TOPOLOGY.nodes.audit;
    render(
      <NodeInspector
        node={auditNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Enable Agent")).toBeInTheDocument();
  });

  it("calls onToggle when toggle clicked", () => {
    const onToggle = vi.fn();
    const auditNode = MOCK_TOPOLOGY.nodes.audit;
    render(
      <NodeInspector
        node={auditNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={onToggle}
        onClose={vi.fn()}
      />
    );
    const toggleBtn = screen.getByRole("button", { name: "" });
    // The toggle is the last button (close is 'x')
    const buttons = screen.getAllByRole("button");
    const toggle = buttons[buttons.length - 1];
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when close button clicked", () => {
    const onClose = vi.fn();
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={onClose}
      />
    );
    fireEvent.click(screen.getByText("x"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows node_type badge", () => {
    render(
      <NodeInspector
        node={baseNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("companion")).toBeInTheDocument();
  });

  it("shows routing rules for router nodes", () => {
    const auditNode = MOCK_TOPOLOGY.nodes.audit;
    render(
      <NodeInspector
        node={auditNode as any}
        overlay={emptyOverlay}
        toggling={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Routing Rules")).toBeInTheDocument();
    expect(screen.getByText("IronClaw artifacts")).toBeInTheDocument();
  });

  it("disables toggle when toggling is true", () => {
    const auditNode = MOCK_TOPOLOGY.nodes.audit;
    render(
      <NodeInspector
        node={auditNode as any}
        overlay={emptyOverlay}
        toggling={true}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />
    );
    // The toggle button should have opacity-50 class (disabled appearance)
    const buttons = screen.getAllByRole("button");
    const toggle = buttons[buttons.length - 1];
    expect(toggle).toBeDisabled();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// useGraphOverlay Hook Tests
// ═══════════════════════════════════════════════════════════════════════════

describe("useGraphOverlay", () => {
  // Can't directly test hooks without a wrapper, but we test the logic
  // via the store state that feeds it

  it("store supports trace events on messages", () => {
    const trace: TraceEvent = {
      type: "trace",
      cat: "node",
      text: "Node started",
      ms: 100,
      node: "companion",
      action: "start",
    };
    const msg: ChatMessage = {
      id: "m1",
      role: "grim",
      content: "test",
      traces: [trace],
    };
    useGrimStore.setState({ messages: [msg] });
    const state = useGrimStore.getState();
    expect(state.messages[0].traces).toHaveLength(1);
    expect(state.messages[0].traces[0].node).toBe("companion");
  });

  it("multiple traces track node lifecycle", () => {
    const traces: TraceEvent[] = [
      { type: "trace", cat: "node", text: "start", ms: 0, node: "identity", action: "start" },
      { type: "trace", cat: "node", text: "end", ms: 50, node: "identity", action: "end", duration_ms: 50 },
      { type: "trace", cat: "node", text: "start", ms: 51, node: "compress", action: "start" },
      { type: "trace", cat: "node", text: "end", ms: 80, node: "compress", action: "end", duration_ms: 29 },
    ];
    const msg: ChatMessage = { id: "m1", role: "grim", content: "test", traces };
    useGrimStore.setState({ messages: [msg], isStreaming: false });

    // Verify traces are stored correctly
    const state = useGrimStore.getState();
    expect(state.messages[0].traces).toHaveLength(4);
    const nodeTraces = state.messages[0].traces.filter(t => t.cat === "node");
    expect(nodeTraces).toHaveLength(4);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// graphTopology.ts Layout Tests
// ═══════════════════════════════════════════════════════════════════════════

describe("graphTopology layout utils", () => {
  it("toCanvasPos produces correct coordinates", async () => {
    const { toCanvasPos, COL_WIDTH, ROW_HEIGHT, CANVAS_OFFSET_X, CANVAS_OFFSET_Y } =
      await import("@/lib/graphTopology");

    const pos = toCanvasPos(0, 0);
    expect(pos.x).toBe(CANVAS_OFFSET_X);
    expect(pos.y).toBe(CANVAS_OFFSET_Y);
  });

  it("toCanvasPos scales with column and row", async () => {
    const { toCanvasPos, COL_WIDTH, ROW_HEIGHT, CANVAS_OFFSET_X, CANVAS_OFFSET_Y } =
      await import("@/lib/graphTopology");

    const pos = toCanvasPos(3, 2);
    expect(pos.x).toBe(CANVAS_OFFSET_X + 3 * COL_WIDTH);
    expect(pos.y).toBe(CANVAS_OFFSET_Y + 2 * ROW_HEIGHT);
  });

  it("toCanvasPos handles negative rows", async () => {
    const { toCanvasPos, CANVAS_OFFSET_Y, ROW_HEIGHT } =
      await import("@/lib/graphTopology");

    const pos = toCanvasPos(5, -1);
    expect(pos.y).toBe(CANVAS_OFFSET_Y - ROW_HEIGHT);
    expect(pos.y).toBeGreaterThan(0); // should still be positive
  });

  it("COL_WIDTH and ROW_HEIGHT are reasonable", async () => {
    const { COL_WIDTH, ROW_HEIGHT } = await import("@/lib/graphTopology");
    expect(COL_WIDTH).toBeGreaterThanOrEqual(100);
    expect(COL_WIDTH).toBeLessThanOrEqual(200);
    expect(ROW_HEIGHT).toBeGreaterThanOrEqual(80);
    expect(ROW_HEIGHT).toBeLessThanOrEqual(150);
  });

  it("exports all required types", async () => {
    const mod = await import("@/lib/graphTopology");
    expect(mod.toCanvasPos).toBeDefined();
    expect(mod.COL_WIDTH).toBeDefined();
    expect(mod.ROW_HEIGHT).toBeDefined();
    expect(mod.CANVAS_OFFSET_X).toBeDefined();
    expect(mod.CANVAS_OFFSET_Y).toBeDefined();
  });
});
