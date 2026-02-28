"use client";

import { useEffect, useRef, useCallback } from "react";
import type { TraceEvent, ResponseMeta } from "@/lib/types";
import { useGrimStore } from "@/store";

function getWsUrl(sessionId: string): string {
  if (typeof window === "undefined") return "";

  const apiUrl = process.env.NEXT_PUBLIC_GRIM_API;
  if (apiUrl) {
    const url = new URL(apiUrl);
    const protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${url.host}/ws/${sessionId}`;
  }

  // Production: same origin
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/${sessionId}`;
}

export function useGrimSocket(sessionId: string): { send: (msg: string) => void } {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Ephemeral per-turn state (not in store)
  const currentResponseId = useRef<string>("");
  const currentTraces = useRef<TraceEvent[]>([]);
  const firstMessageSent = useRef(false);
  // Per-step bubble tracking
  const currentNode = useRef<string>("");
  const stepBubbleIds = useRef<Map<string, string>>(new Map());

  const store = useGrimStore;

  const connect = useCallback((sid: string) => {
    const url = getWsUrl(sid);
    if (!url) return;

    store.getState().setWsStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      store.getState().setWsStatus("connected");
    };

    ws.onclose = () => {
      store.getState().setWsStatus("disconnected");
      wsRef.current = null;
      reconnectTimer.current = setTimeout(() => connect(sid), 3000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const state = store.getState();
        const id = currentResponseId.current;

        switch (data.type) {
          case "trace": {
            if (!id) break;
            const trace = data as TraceEvent;

            // Track current node for step bubbles
            if (trace.cat === "node" && trace.action === "start" && trace.node) {
              currentNode.current = trace.node;
            }

            // When a node ends with step_content, finalize its step bubble
            if (trace.cat === "node" && trace.action === "end" && trace.node && trace.step_content) {
              const stepId = stepBubbleIds.current.get(trace.node);
              if (stepId) {
                state.updateMessage(stepId, {
                  content: trace.step_content,
                  streaming: false,
                });
              }
            }

            currentTraces.current.push(trace);
            // Attach traces to the main response bubble
            state.updateMessage(id, { traces: [...currentTraces.current] });
            break;
          }
          case "stream": {
            if (!id) break;
            const nodeName = data.node || currentNode.current || "";

            // Determine which bubble to stream into
            let targetId = id;
            if (nodeName && nodeName !== "integrate") {
              // Non-final nodes get their own step bubble
              if (!stepBubbleIds.current.has(nodeName)) {
                const stepId = crypto.randomUUID();
                stepBubbleIds.current.set(nodeName, stepId);
                state.appendMessage({
                  id: stepId,
                  role: "grim",
                  content: "",
                  traces: [],
                  streaming: true,
                  isStep: true,
                  node: nodeName,
                });
              }
              targetId = stepBubbleIds.current.get(nodeName)!;
            }

            const msgs = store.getState().messages;
            const msg = msgs.find((m) => m.id === targetId);
            if (msg) {
              state.updateMessage(targetId, { content: msg.content + data.token });
            }
            break;
          }
          case "response": {
            if (!id) break;
            const meta = data.meta as ResponseMeta;
            const hasStepBubbles = stepBubbleIds.current.size > 0;

            if (hasStepBubbles) {
              // Step bubbles already have the streamed content — don't duplicate.
              // Finalize all step bubbles, attach meta+traces to the last one.
              let lastStepId = "";
              stepBubbleIds.current.forEach((stepId) => {
                state.updateMessage(stepId, { streaming: false });
                lastStepId = stepId;
              });
              if (lastStepId) {
                state.updateMessage(lastStepId, {
                  meta,
                  traces: [...currentTraces.current],
                });
              }
              // Remove the empty main placeholder
              state.deleteMessage(id);
            } else {
              // No step bubbles — single response bubble (original behavior)
              state.updateMessage(id, {
                content: data.content,
                meta,
                traces: [...currentTraces.current],
                streaming: false,
              });
            }

            state.setStreaming(false);
            currentResponseId.current = "";
            currentNode.current = "";
            stepBubbleIds.current.clear();
            break;
          }
          case "error": {
            if (id) {
              state.updateMessage(id, {
                content: data.content,
                streaming: false,
                error: true,
              });
            } else {
              state.appendMessage({
                id: crypto.randomUUID(),
                role: "grim",
                content: data.content,
                traces: [],
                error: true,
              });
            }
            state.setStreaming(false);
            currentResponseId.current = "";
            currentNode.current = "";
            stepBubbleIds.current.clear();
            break;
          }
          case "ui_command": {
            state.dispatchUICommand(data);
            break;
          }
        }
      } catch {
        // Ignore malformed messages
      }
    };
  }, [store]);

  useEffect(() => {
    connect(sessionId);
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [sessionId, connect]);

  const send = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      const state = store.getState();
      if (state.isStreaming) return;
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;

      const userId = crypto.randomUUID();
      const responseId = crypto.randomUUID();
      currentResponseId.current = responseId;
      currentTraces.current = [];
      currentNode.current = "";
      stepBubbleIds.current.clear();

      state.appendMessage({ id: userId, role: "user", content: text, traces: [] });
      state.appendMessage({
        id: responseId,
        role: "grim",
        content: "",
        traces: [],
        streaming: true,
      });
      state.setStreaming(true);

      wsRef.current.send(JSON.stringify({ message: text }));

      // Update session title with first message
      if (!firstMessageSent.current) {
        firstMessageSent.current = true;
        const title = text.length > 50 ? text.slice(0, 50) + "..." : text;
        state.upsertSession(state.activeSessionId, title);
      }
    },
    [store]
  );

  // Reset firstMessageSent when session changes
  useEffect(() => {
    firstMessageSent.current = false;
  }, [sessionId]);

  return { send };
}
