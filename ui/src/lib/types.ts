// ── WebSocket protocol types (matches server/app.py) ──

export type TraceCategory = "node" | "llm" | "tool" | "graph" | "sdk";

export interface TraceEvent {
  type: "trace";
  cat: TraceCategory;
  text: string;
  ms: number;
  node?: string;
  tool?: string;
  action?: "start" | "end" | "call";
  duration_ms?: number;
  detail?: Record<string, unknown>;
  input?: unknown;
  output_preview?: string;
  step_content?: string; // LLM output for this node (future per-step bubbles)
}

export interface StreamEvent {
  type: "stream";
  token: string;
  node?: string;
}

export interface ResponseMeta {
  mode: string;
  knowledge_count: number;
  session_knowledge_count?: number;
  skills: string[];
  fdo_ids: string[];
  total_ms: number;
  stop_reason?: string;  // set when response was stopped (cancel, timeout, loop)
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

export interface StreamClearEvent {
  type: "stream_clear";
  node: string;
  thinking?: string;  // the intermediate text that was cleared
}

// ── Memory notification (compact evolve node feedback) ──

export interface MemoryNotificationEvent {
  type: "memory_notification";
  updated: boolean;
  summary: string;
  duration_ms?: number;
}

// ── Queue / Cancel events ──

export interface QueuedEvent {
  type: "queued";
  content: string;
  position: number;
}

export interface CancelledEvent {
  type: "cancelled";
}

// ── UI command types (future GRIM UI control) ──

export type UICommandType =
  | "open_chat"
  | "close_chat"
  | "navigate_dashboard"
  | "show_widget";

export interface UICommand {
  type: "ui_command";
  command: UICommandType;
  payload?: Record<string, unknown>;
}

export type ServerEvent = TraceEvent | StreamEvent | StreamClearEvent | ResponseEvent | ErrorEvent | MemoryNotificationEvent | QueuedEvent | CancelledEvent | UICommand;

// ── Chat state types ──

export interface ChatMessage {
  id: string;
  role: "user" | "grim";
  content: string;
  node?: string;    // graph node name (for step bubbles)
  isStep?: boolean;  // true = per-node step bubble
  thinkingText?: string;  // intermediate text before tool calls (e.g. "Let me look into that")
  meta?: ResponseMeta;
  traces: TraceEvent[];
  streaming?: boolean;
  error?: boolean;
  cancelled?: boolean;
  queued?: boolean;
  /** Attached files (displayed as pills on user messages) */
  files?: { name: string; type: string; size: number }[];
  /** Pool job ID — when this bubble is following a dispatched pool job */
  poolJobId?: string;
}

export interface Session {
  id: string;
  title: string;
  updatedAt: number;
}

// ── Hook types ──

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

// ── Dashboard / Bridge API types ──

export interface TokenSummary {
  period_days: number;
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_create_tokens: number;
    total_tokens: number;
    calls: number;
  };
  by_caller: Record<string, { input_tokens: number; output_tokens: number; calls: number }>;
  by_model: Record<string, { input_tokens: number; output_tokens: number; calls: number }>;
}

export interface TokenDayEntry {
  date: string;
  input_tokens: number;
  output_tokens: number;
  calls: number;
}

export interface TokenRecentEntry {
  id: number;
  timestamp: string;
  caller_id: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
  total_tokens: number;
}

