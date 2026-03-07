"use client";

import { useEffect, useRef, useCallback } from "react";
import type { TraceEvent, ResponseMeta } from "@/lib/types";
import { uuid } from "@/lib/uuid";
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

interface FileAttachment {
  name: string;
  type: string;
  size: number;
  content: string;
}

export function useGrimSocket(sessionId: string): {
  send: (msg: string, files?: FileAttachment[]) => void;
  cancel: () => void;
} {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Ephemeral per-turn state (not in store)
  const currentResponseId = useRef<string>("");
  const currentTraces = useRef<TraceEvent[]>([]);
  const firstMessageSent = useRef(false);
  // Per-step bubble tracking
  const currentNode = useRef<string>("");
  const stepBubbleIds = useRef<Map<string, string>>(new Map());
  // Separate bubbles: create new bubble after tool calls
  const needsNewBubble = useRef(false);
  // Pool job inline following — job_id → bubble_id
  const poolJobBubbles = useRef<Map<string, string>>(new Map());

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

            // Tag every trace with the active node so we can filter later
            const taggedTrace = { ...trace, _activeNode: currentNode.current } as TraceEvent & { _activeNode: string };

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

            // Tool call = next text block should be a new bubble
            if (trace.cat === "tool" && trace.action === "call") {
              needsNewBubble.current = true;
            }

            currentTraces.current.push(taggedTrace);
            // Attach traces to the main response bubble
            state.updateMessage(id, { traces: [...currentTraces.current] });

            // Also attach traces to the relevant step bubble (for AgentLogBlock)
            const activeNode = currentNode.current;
            if (activeNode) {
              const stepId = stepBubbleIds.current.get(activeNode);
              if (stepId) {
                const nodeTraces = currentTraces.current.filter(
                  (t) => {
                    const an = (t as TraceEvent & { _activeNode?: string })._activeNode;
                    return an === activeNode;
                  }
                );
                state.updateMessage(stepId, { traces: [...nodeTraces] });
              }
            }
            break;
          }
          case "stream": {
            if (!id) break;
            const nodeName = data.node || currentNode.current || "";

            // SDK/companion text — check if we need a new bubble after tool calls
            if (nodeName === "sdk" || nodeName === "companion" || nodeName === "") {
              if (needsNewBubble.current) {
                needsNewBubble.current = false;
                // Finalize the current response bubble
                const currentId = currentResponseId.current;
                if (currentId) {
                  state.updateMessage(currentId, { streaming: false });
                }
                // Create a new bubble for this text block
                const newId = uuid();
                currentResponseId.current = newId;
                state.appendMessage({
                  id: newId,
                  role: "grim",
                  content: "",
                  traces: [],
                  streaming: true,
                });
              }

              const targetId = currentResponseId.current;
              const msgs = store.getState().messages;
              const msg = msgs.find((m) => m.id === targetId);
              if (msg) {
                state.updateMessage(targetId, { content: msg.content + data.token });
              }
              break;
            }

            // Other non-integrate nodes get step bubbles
            let targetId = id;
            if (nodeName && nodeName !== "integrate") {
              if (!stepBubbleIds.current.has(nodeName)) {
                const stepId = uuid();
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
          case "stream_clear": {
            // Companion made tool calls — the text just streamed was "thinking".
            // Clear the bubble so the final answer starts fresh.
            if (!id) break;
            state.updateMessage(id, { content: "", thinkingText: data.thinking || "" });
            break;
          }
          case "response": {
            if (!id) break;
            const meta = data.meta as ResponseMeta;

            // Finalize step bubbles (non-companion nodes that streamed)
            stepBubbleIds.current.forEach((stepId) => {
              state.updateMessage(stepId, { streaming: false });
            });

            // Finalize the current response bubble — DON'T overwrite content
            // with data.content because it contains ALL text from the entire
            // turn, and we've already split text into separate bubbles via
            // streaming. Only set meta/traces/streaming.
            const finalId = currentResponseId.current;
            state.updateMessage(finalId, {
              meta,
              traces: [...currentTraces.current],
              streaming: false,
            });

            state.setStreaming(false);
            state.setQueuedCount(0);
            currentResponseId.current = "";
            currentNode.current = "";
            stepBubbleIds.current.clear();
            needsNewBubble.current = false;
            poolJobBubbles.current.clear();
            break;
          }
          case "cancelled": {
            // Response was cancelled — finalize current bubble as partial
            const cancelId = currentResponseId.current;
            if (cancelId) {
              state.updateMessage(cancelId, {
                streaming: false,
                cancelled: true,
              });
            }
            stepBubbleIds.current.forEach((stepId) => {
              state.updateMessage(stepId, { streaming: false });
            });
            state.setStreaming(false);
            state.setQueuedCount(0);
            currentResponseId.current = "";
            currentNode.current = "";
            stepBubbleIds.current.clear();
            needsNewBubble.current = false;
            break;
          }
          case "queued": {
            // Message was queued on the backend — show indicator
            state.setQueuedCount(data.position || 1);
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
                id: uuid(),
                role: "grim",
                content: data.content,
                traces: [],
                error: true,
              });
            }
            state.setStreaming(false);
            state.setQueuedCount(0);
            currentResponseId.current = "";
            currentNode.current = "";
            stepBubbleIds.current.clear();
            needsNewBubble.current = false;
            break;
          }
          case "memory_notification": {
            // Compact "memory updated" pill — creates a step bubble for evolve
            // without the full memory content dump
            if (!id) break;
            const notifId = stepBubbleIds.current.get("evolve") || uuid();
            if (!stepBubbleIds.current.has("evolve")) {
              stepBubbleIds.current.set("evolve", notifId);
              state.appendMessage({
                id: notifId,
                role: "grim",
                content: data.summary || "Working memory updated",
                traces: [],
                streaming: false,
                isStep: true,
                node: "memory_update",
              });
            } else {
              state.updateMessage(notifId, {
                content: data.summary || "Working memory updated",
                streaming: false,
              });
            }
            break;
          }
          case "ui_command": {
            state.dispatchUICommand(data);
            break;
          }
          case "pool_follow": {
            // Inline following of dispatched pool jobs
            const jobId = data.job_id as string;
            const eventType = data.event_type as string;
            if (!jobId) break;

            // Create bubble on first event for this job
            if (!poolJobBubbles.current.has(jobId)) {
              const bubbleId = uuid();
              poolJobBubbles.current.set(jobId, bubbleId);
              state.appendMessage({
                id: bubbleId,
                role: "grim",
                content: "",
                traces: [],
                streaming: true,
                isStep: true,
                node: "pool_job",
                poolJobId: jobId,
              });
            }

            const bubbleId = poolJobBubbles.current.get(jobId)!;

            if (eventType === "agent_output") {
              const blockType = data.block_type as string;
              if (blockType === "text") {
                // Append agent text to the bubble content
                const msgs = store.getState().messages;
                const msg = msgs.find((m) => m.id === bubbleId);
                if (msg) {
                  state.updateMessage(bubbleId, {
                    content: msg.content + (data.text || ""),
                  });
                }
              } else if (blockType === "tool_use") {
                // Add as a tool trace
                const msgs = store.getState().messages;
                const msg = msgs.find((m) => m.id === bubbleId);
                if (msg) {
                  const trace: TraceEvent = {
                    type: "trace",
                    cat: "tool",
                    action: "call",
                    text: `${data.name}`,
                    tool: data.name as string,
                    input: data.input,
                    ms: 0,
                  };
                  state.updateMessage(bubbleId, {
                    traces: [...msg.traces, trace],
                  });
                }
              }
            } else if (eventType === "agent_tool_result") {
              // Add output preview to the last tool trace
              const msgs = store.getState().messages;
              const msg = msgs.find((m) => m.id === bubbleId);
              if (msg && msg.traces.length > 0) {
                const traces = [...msg.traces];
                const lastTool = traces.findLast((t) => t.cat === "tool");
                if (lastTool) {
                  const content = data.content;
                  let preview = "";
                  if (typeof content === "string") {
                    preview = content.slice(0, 200);
                  } else if (Array.isArray(content)) {
                    const textBlock = content.find((b: Record<string, unknown>) => b.type === "text");
                    if (textBlock) preview = (textBlock.text as string || "").slice(0, 200);
                  }
                  lastTool.output_preview = preview;
                  state.updateMessage(bubbleId, { traces });
                }
              }
            } else if (
              eventType === "job_complete" ||
              eventType === "job_failed" ||
              eventType === "job_cancelled"
            ) {
              // Finalize the bubble
              state.updateMessage(bubbleId, { streaming: false });
              poolJobBubbles.current.delete(jobId);
            } else if (eventType === "job_blocked") {
              // Approval request or clarification — show in bubble
              const question = data.question as string || "";
              const msgs = store.getState().messages;
              const msg = msgs.find((m) => m.id === bubbleId);
              if (msg) {
                let label = "Waiting for input...";
                try {
                  const parsed = JSON.parse(question);
                  if (parsed.approval_required) {
                    const domain = parsed.domain || "?";
                    const fdo = parsed.proposed?.id || "?";
                    label = `🔒 Approval needed: ${parsed.proposed?.action || "write"} ${fdo} (${domain})`;
                  }
                } catch {
                  if (question) label = question.slice(0, 200);
                }
                state.updateMessage(bubbleId, {
                  content: (msg.content ? msg.content + "\n\n" : "") + label,
                });
              }
            } else if (eventType === "job_started") {
              // No-op — bubble already created
            }
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
    (text: string, files?: FileAttachment[]) => {
      if (!text.trim()) return;
      const state = store.getState();
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;

      // Allow sending while streaming — backend will queue it
      const isQueuing = state.isStreaming;

      const userId = uuid();
      state.appendMessage({
        id: userId,
        role: "user",
        content: text,
        traces: [],
        queued: isQueuing,
        files: files?.map((f) => ({ name: f.name, type: f.type, size: f.size })),
      });

      if (!isQueuing) {
        // Start a fresh response bubble
        const responseId = uuid();
        currentResponseId.current = responseId;
        currentTraces.current = [];
        currentNode.current = "";
        stepBubbleIds.current.clear();
        needsNewBubble.current = false;

        state.appendMessage({
          id: responseId,
          role: "grim",
          content: "",
          traces: [],
          streaming: true,
        });
        state.setStreaming(true);
      }

      const payload: Record<string, unknown> = { message: text };
      if (files && files.length > 0) {
        payload.files = files;
      }
      wsRef.current.send(JSON.stringify(payload));

      // Update session title with first message
      if (!firstMessageSent.current) {
        firstMessageSent.current = true;
        const title = text.length > 50 ? text.slice(0, 50) + "..." : text;
        state.upsertSession(state.activeSessionId, title);
      }
    },
    [store]
  );

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ cancel: true }));
    }
  }, []);

  // Reset firstMessageSent when session changes
  useEffect(() => {
    firstMessageSent.current = false;
  }, [sessionId]);

  return { send, cancel };
}
