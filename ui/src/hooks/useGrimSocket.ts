"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { TraceEvent, ResponseMeta, ConnectionStatus } from "@/lib/types";

interface UseGrimSocketOptions {
  sessionId: string;
  onTrace: (trace: TraceEvent) => void;
  onStream: (token: string) => void;
  onResponse: (content: string, meta: ResponseMeta) => void;
  onError: (message: string) => void;
}

interface UseGrimSocketReturn {
  status: ConnectionStatus;
  send: (message: string) => void;
}

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

export function useGrimSocket(
  options: UseGrimSocketOptions
): UseGrimSocketReturn {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const connect = useCallback((sid: string) => {
    const url = getWsUrl(sid);
    if (!url) return;

    setStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
    };

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;
      // Auto-reconnect after 3s
      reconnectTimer.current = setTimeout(() => connect(sid), 3000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        switch (data.type) {
          case "trace":
            optionsRef.current.onTrace(data as TraceEvent);
            break;
          case "stream":
            optionsRef.current.onStream(data.token);
            break;
          case "response":
            optionsRef.current.onResponse(data.content, data.meta);
            break;
          case "error":
            optionsRef.current.onError(data.content);
            break;
        }
      } catch {
        // Ignore malformed messages
      }
    };
  }, []);

  useEffect(() => {
    connect(options.sessionId);
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // Don't auto-reconnect on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [options.sessionId, connect]);

  const send = useCallback((message: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ message }));
    }
  }, []);

  return { status, send };
}
