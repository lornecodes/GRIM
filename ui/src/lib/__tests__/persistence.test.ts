import { describe, it, expect, beforeEach } from "vitest";
import { saveMessages, loadMessages, deleteMessages } from "../persistence";
import type { ChatMessage } from "../types";

function makeUserMsg(content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: "user",
    content,
    traces: [],
  };
}

function makeGrimMsg(content: string, streaming = false): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: "grim",
    content,
    traces: [
      { type: "trace", cat: "node", text: "→ companion", ms: 10 },
      { type: "trace", cat: "llm", text: "LLM call started", ms: 50 },
    ],
    meta: {
      mode: "companion",
      knowledge_count: 3,
      skills: [],
      fdo_ids: ["pac-framework"],
      total_ms: 500,
    },
    streaming,
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe("saveMessages", () => {
  it("saves messages under the session key", () => {
    const msgs = [makeUserMsg("hello"), makeGrimMsg("hi there")];
    saveMessages("abc123", msgs);
    const raw = localStorage.getItem("grim-msg-abc123");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed).toHaveLength(2);
  });

  it("strips traces but keeps meta", () => {
    const msgs = [makeUserMsg("hello"), makeGrimMsg("hi there")];
    saveMessages("abc123", msgs);
    const parsed = JSON.parse(localStorage.getItem("grim-msg-abc123")!);
    // Traces stripped
    expect(parsed[1].traces).toEqual([]);
    // Meta preserved
    expect(parsed[1].meta.mode).toBe("companion");
    expect(parsed[1].meta.total_ms).toBe(500);
  });

  it("filters out streaming messages", () => {
    const msgs = [
      makeUserMsg("hello"),
      makeGrimMsg("partial...", true), // still streaming
    ];
    saveMessages("abc123", msgs);
    const parsed = JSON.parse(localStorage.getItem("grim-msg-abc123")!);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].role).toBe("user");
  });

  it("does nothing with empty session ID", () => {
    saveMessages("", [makeUserMsg("hello")]);
    expect(localStorage.length).toBe(0);
  });

  it("does nothing with empty messages", () => {
    saveMessages("abc123", []);
    expect(localStorage.getItem("grim-msg-abc123")).toBeNull();
  });

  it("does not save if only streaming messages exist", () => {
    saveMessages("abc123", [makeGrimMsg("...", true)]);
    expect(localStorage.getItem("grim-msg-abc123")).toBeNull();
  });
});

describe("loadMessages", () => {
  it("returns saved messages", () => {
    const msgs = [makeUserMsg("hello"), makeGrimMsg("hi there")];
    saveMessages("abc123", msgs);
    const loaded = loadMessages("abc123");
    expect(loaded).toHaveLength(2);
    expect(loaded[0].content).toBe("hello");
    expect(loaded[1].content).toBe("hi there");
  });

  it("returns empty array for unknown session", () => {
    expect(loadMessages("unknown")).toEqual([]);
  });

  it("returns empty array for empty session ID", () => {
    expect(loadMessages("")).toEqual([]);
  });

  it("returns empty array for corrupted data", () => {
    localStorage.setItem("grim-msg-bad", "not json{{{");
    expect(loadMessages("bad")).toEqual([]);
  });

  it("returns empty array for non-array JSON", () => {
    localStorage.setItem("grim-msg-obj", JSON.stringify({ not: "array" }));
    expect(loadMessages("obj")).toEqual([]);
  });

  it("preserves message roles and content", () => {
    const msgs = [
      makeUserMsg("What is PAC?"),
      makeGrimMsg("PAC stands for..."),
    ];
    saveMessages("s1", msgs);
    const loaded = loadMessages("s1");
    expect(loaded[0].role).toBe("user");
    expect(loaded[0].content).toBe("What is PAC?");
    expect(loaded[1].role).toBe("grim");
    expect(loaded[1].content).toBe("PAC stands for...");
  });
});

describe("deleteMessages", () => {
  it("removes saved messages", () => {
    saveMessages("abc123", [makeUserMsg("hello")]);
    expect(loadMessages("abc123")).toHaveLength(1);
    deleteMessages("abc123");
    expect(loadMessages("abc123")).toEqual([]);
  });
});

describe("session switching flow", () => {
  it("saves session A, switches to B, restores A", () => {
    // Chat in session A
    const sessionA = "session-a";
    const msgsA = [
      makeUserMsg("What is PAC?"),
      makeGrimMsg("PAC is a framework for..."),
    ];
    saveMessages(sessionA, msgsA);

    // Switch to session B (new session, no messages)
    const sessionB = "session-b";
    const loadedB = loadMessages(sessionB);
    expect(loadedB).toEqual([]);

    // Chat in session B
    const msgsB = [
      makeUserMsg("Tell me about GRIM"),
      makeGrimMsg("GRIM is an AI companion..."),
    ];
    saveMessages(sessionB, msgsB);

    // Switch back to session A
    const restoredA = loadMessages(sessionA);
    expect(restoredA).toHaveLength(2);
    expect(restoredA[0].content).toBe("What is PAC?");
    expect(restoredA[1].content).toBe("PAC is a framework for...");

    // Session B also intact
    const restoredB = loadMessages(sessionB);
    expect(restoredB).toHaveLength(2);
    expect(restoredB[0].content).toBe("Tell me about GRIM");
  });

  it("multiple turns accumulate in a session", () => {
    const sid = "multi-turn";

    // Turn 1
    const turn1 = [makeUserMsg("hello"), makeGrimMsg("hi")];
    saveMessages(sid, turn1);

    // Turn 2 — append to existing messages
    const turn2 = [
      ...turn1,
      makeUserMsg("how are you?"),
      makeGrimMsg("doing well"),
    ];
    saveMessages(sid, turn2);

    const loaded = loadMessages(sid);
    expect(loaded).toHaveLength(4);
    expect(loaded[2].content).toBe("how are you?");
    expect(loaded[3].content).toBe("doing well");
  });
});
