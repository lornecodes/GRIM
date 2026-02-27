import type { ChatMessage } from "./types";

const MSG_PREFIX = "grim-msg-";

export function saveMessages(sessionId: string, messages: ChatMessage[]) {
  if (!sessionId || messages.length === 0) return;
  try {
    // Strip traces to keep storage lean, only save completed messages
    const slim = messages
      .filter((m) => !m.streaming)
      .map(({ traces, ...rest }) => ({ ...rest, traces: [] as ChatMessage["traces"] }));
    if (slim.length === 0) return;
    localStorage.setItem(MSG_PREFIX + sessionId, JSON.stringify(slim));
  } catch {
    // Storage full — silently ignore
  }
}

export function loadMessages(sessionId: string): ChatMessage[] {
  if (!sessionId) return [];
  try {
    const stored = localStorage.getItem(MSG_PREFIX + sessionId);
    if (!stored) return [];
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) return [];
    return parsed;
  } catch {
    return [];
  }
}

export function deleteMessages(sessionId: string) {
  try {
    localStorage.removeItem(MSG_PREFIX + sessionId);
  } catch {
    // Ignore
  }
}
