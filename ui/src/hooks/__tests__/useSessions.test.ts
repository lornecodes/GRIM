import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessions } from "../useSessions";
import { useGrimStore } from "@/store";

beforeEach(() => {
  localStorage.clear();
  // Reset Zustand store between tests
  useGrimStore.setState({
    messages: [],
    isStreaming: false,
    sessions: [],
    activeSessionId: "",
  });
});

describe("useSessions", () => {
  it("initializes with a random activeId", () => {
    const { result } = renderHook(() => useSessions());
    expect(result.current.activeId).toBeTruthy();
    expect(result.current.activeId.length).toBe(8);
  });

  it("starts with empty sessions list", () => {
    const { result } = renderHook(() => useSessions());
    expect(result.current.sessions).toEqual([]);
  });

  it("creates a new session via updateSession", () => {
    const { result } = renderHook(() => useSessions());
    act(() => {
      result.current.updateSession("abc123", "hello world");
    });
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0].id).toBe("abc123");
    expect(result.current.sessions[0].title).toBe("hello world");
  });

  it("newSession changes the activeId", () => {
    const { result } = renderHook(() => useSessions());
    const oldId = result.current.activeId;
    act(() => {
      result.current.newSession();
    });
    expect(result.current.activeId).not.toBe(oldId);
    expect(result.current.activeId.length).toBe(8);
  });

  it("switchSession sets the activeId", () => {
    const { result } = renderHook(() => useSessions());
    act(() => {
      result.current.switchSession("target-id");
    });
    expect(result.current.activeId).toBe("target-id");
  });

  it("persists sessions to localStorage", async () => {
    const { result } = renderHook(() => useSessions());
    act(() => {
      result.current.updateSession("s1", "first session");
    });
    // Wait for the useEffect that persists to localStorage
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    const stored = localStorage.getItem("grim-sessions");
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].id).toBe("s1");
  });

  it("restores sessions from localStorage on mount", () => {
    // Pre-populate localStorage
    const sessions = [
      { id: "old1", title: "old session", updatedAt: Date.now() },
    ];
    localStorage.setItem("grim-sessions", JSON.stringify(sessions));

    const { result } = renderHook(() => useSessions());
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0].id).toBe("old1");
  });

  it("deleteSession removes the session", async () => {
    const { result } = renderHook(() => useSessions());
    act(() => {
      result.current.updateSession("s1", "session one");
    });
    act(() => {
      result.current.updateSession("s2", "session two");
    });
    expect(result.current.sessions).toHaveLength(2);
    act(() => {
      result.current.deleteSession("s1");
    });
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0].id).toBe("s2");
  });

  it("deleteSession of active session creates a new one", () => {
    const { result } = renderHook(() => useSessions());
    const currentId = result.current.activeId;
    act(() => {
      result.current.updateSession(currentId, "my session");
    });
    act(() => {
      result.current.deleteSession(currentId);
    });
    // Should have a new activeId
    expect(result.current.activeId).not.toBe(currentId);
  });
});
