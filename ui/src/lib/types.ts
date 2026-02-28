// ── WebSocket protocol types (matches server/app.py) ──

export type TraceCategory = "node" | "llm" | "tool" | "claw" | "graph";

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
  step_content?: string; // LLM output for this node (future per-step bubbles)
  sandboxed?: boolean;   // true = executed through IronClaw sandbox
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

export interface StreamClearEvent {
  type: "stream_clear";
  node: string;
  thinking?: string;  // the intermediate text that was cleared
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

export type ServerEvent = TraceEvent | StreamEvent | StreamClearEvent | ResponseEvent | ErrorEvent | UICommand;

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

