import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { saveMessages, loadMessages } from "@/lib/persistence";
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
});

describe("ChatApp session switching", () => {
  it("renders without crashing", () => {
    render(<ChatApp />);
    expect(screen.getByText("GRIM")).toBeInTheDocument();
  });

  it("shows empty state initially", () => {
    render(<ChatApp />);
    expect(screen.getByText("Ready when you are.")).toBeInTheDocument();
  });

  it("shows the sessions button", () => {
    render(<ChatApp />);
    expect(screen.getByText("sessions")).toBeInTheDocument();
  });

  it("pre-saved messages are restored when switching to a session", () => {
    // Pre-populate a session in localStorage
    const msgs = makeMessages("What is PAC?");
    saveMessages("test-session", msgs);

    // Also set up the sessions list so it appears in the sidebar
    const sessions = [
      { id: "test-session", title: "What is PAC?", updatedAt: Date.now() },
    ];
    localStorage.setItem("grim-sessions", JSON.stringify(sessions));

    render(<ChatApp />);

    // The session should appear in sidebar
    const sessionItem = screen.getByText("What is PAC?");
    expect(sessionItem).toBeInTheDocument();

    // Click the session
    act(() => {
      fireEvent.click(sessionItem);
    });

    // Messages should be restored (check GRIM response — user text also appears in sidebar)
    expect(screen.getByText("Response to: What is PAC?")).toBeInTheDocument();
  });

  it("persistence roundtrip: type → switch away → switch back", async () => {
    // Set up two sessions in localStorage
    const msgsA = makeMessages("Session A question");
    const msgsB = makeMessages("Session B question");
    saveMessages("session-a", msgsA);
    saveMessages("session-b", msgsB);

    const sessions = [
      { id: "session-a", title: "Session A question", updatedAt: Date.now() },
      { id: "session-b", title: "Session B question", updatedAt: Date.now() - 1000 },
    ];
    localStorage.setItem("grim-sessions", JSON.stringify(sessions));

    render(<ChatApp />);

    // Click session A
    act(() => {
      fireEvent.click(screen.getByText("Session A question"));
    });

    // Session A messages should show
    expect(screen.getByText("Response to: Session A question")).toBeInTheDocument();

    // Now click session B
    act(() => {
      fireEvent.click(screen.getByText("Session B question"));
    });

    // Session B messages should show
    expect(screen.getByText("Response to: Session B question")).toBeInTheDocument();
    // Session A messages should NOT show
    expect(screen.queryByText("Response to: Session A question")).not.toBeInTheDocument();

    // Switch back to A
    act(() => {
      fireEvent.click(screen.getByText("Session A question"));
    });

    // Session A should be back
    expect(screen.getByText("Response to: Session A question")).toBeInTheDocument();
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
