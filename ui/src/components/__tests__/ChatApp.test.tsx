import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { saveMessages, loadMessages } from "@/lib/persistence";
import { useGrimStore } from "@/store";
import type { ChatMessage } from "@/lib/types";

// Mock the WebSocket hook — we just need to test session/persistence logic
vi.mock("@/hooks/useGrimSocket", () => ({
  useGrimSocket: () => ({
    status: "connected" as const,
    send: vi.fn(),
  }),
}));

// Import AFTER mocks are set up
import { ChatApp } from "../ChatApp";

function makeMessages(content: string): ChatMessage[] {
  return [
    { id: "u1", role: "user", content, traces: [] },
    {
      id: "g1",
      role: "grim",
      content: `Response to: ${content}`,
      traces: [],
      meta: {
        mode: "companion",
        knowledge_count: 0,
        skills: [],
        fdo_ids: [],
        total_ms: 100,
      },
    },
  ];
}

beforeEach(() => {
  localStorage.clear();
  // Reset Zustand store between tests
  useGrimStore.setState({
    messages: [],
    isStreaming: false,
    wsStatus: "disconnected",
    sessions: [],
    activeSessionId: "",
    chatPanelOpen: true,
    activeDashboardWidget: "tokens",
  });
});

describe("ChatApp", () => {
  it("renders without crashing", () => {
    render(<ChatApp />);
    expect(screen.getByText("GRIM")).toBeInTheDocument();
  });

  it("shows empty state initially", () => {
    render(<ChatApp />);
    expect(screen.getByText("GRIM is ready.")).toBeInTheDocument();
  });

  it("shows the chat toggle button", () => {
    render(<ChatApp />);
    expect(screen.getByText("chat")).toBeInTheDocument();
  });

  it("shows Mission Control label", () => {
    render(<ChatApp />);
    expect(screen.getByText("Mission Control")).toBeInTheDocument();
  });

  it("loadMessages returns correct data for pre-saved sessions", () => {
    const msgs = makeMessages("test");
    saveMessages("s1", msgs);
    const loaded = loadMessages("s1");
    expect(loaded).toHaveLength(2);
    expect(loaded[0].content).toBe("test");
    expect(loaded[1].content).toBe("Response to: test");
  });
});
