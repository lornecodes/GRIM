// ── WebSocket protocol types (matches server/app.py) ──

export type TraceCategory = "node" | "llm" | "tool" | "graph";

export interface TraceEvent {
  type: "trace";
  cat: TraceCategory;
  text: string;
  ms: number;
  node?: string;
  tool?: string;
  action?: "start" | "end";
  duration_ms?: number;
  detail?: Record<string, unknown>;
  input?: unknown;
  output_preview?: string;
}

export interface StreamEvent {
  type: "stream";
  token: string;
}

export interface ResponseMeta {
  mode: string;
  knowledge_count: number;
  skills: string[];
  fdo_ids: string[];
  total_ms: number;
}

export interface ResponseEvent {
  type: "response";
  content: string;
  meta: ResponseMeta;
}

export interface ErrorEvent {
  type: "error";
  content: string;
}

export type ServerEvent = TraceEvent | StreamEvent | ResponseEvent | ErrorEvent;

// ── Chat state types ──

export interface ChatMessage {
  id: string;
  role: "user" | "grim";
  content: string;
  meta?: ResponseMeta;
  traces: TraceEvent[];
  streaming?: boolean;
  error?: boolean;
}

export interface Session {
  id: string;
  title: string;
  updatedAt: number;
}

// ── Hook types ──

export type ConnectionStatus = "connecting" | "connected" | "disconnected";
