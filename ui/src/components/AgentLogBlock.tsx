"use client";

import { useState, useEffect } from "react";
import type { TraceEvent } from "@/lib/types";

interface AgentLogBlockProps {
  content: string;
  traces: TraceEvent[];
  streaming?: boolean;
  node?: string;
}

function extractAgentType(traces: TraceEvent[]): string {
  // Look for a node trace that mentions delegation type
  for (const t of traces) {
    if (t.cat === "node" && t.text) {
      const match = t.text.match(/delegat\w*\s+(?:to\s+)?(\w+)/i);
      if (match) return match[1];
    }
    // Check detail for delegation_type
    if (t.detail && typeof t.detail === "object") {
      const dt = (t.detail as Record<string, unknown>).delegation_type;
      if (typeof dt === "string") return dt;
    }
  }
  return "agent";
}

function extractToolCalls(traces: TraceEvent[]): { tool: string; input?: string; output?: string }[] {
  return traces
    .filter((t) => t.cat === "tool" && t.tool)
    .map((t) => ({
      tool: t.tool!,
      input: typeof t.input === "string" ? t.input : t.input ? JSON.stringify(t.input) : undefined,
      output: t.output_preview || undefined,
    }));
}

function extractTotalMs(traces: TraceEvent[]): number {
  if (traces.length === 0) return 0;
  return Math.max(...traces.map((t) => t.ms));
}

function summarizeTools(tools: { tool: string; input?: string }[]): string {
  if (tools.length === 0) return "processing";
  if (tools.length === 1) {
    const t = tools[0];
    if (t.input && t.input.length < 40) return `${t.tool}(${t.input})`;
    return t.tool;
  }
  return `${tools.length} tool calls`;
}

export function AgentLogBlock({ content, traces, streaming, node }: AgentLogBlockProps) {
  const [expanded, setExpanded] = useState(false);

  // Auto-expand while streaming
  useEffect(() => {
    if (streaming) setExpanded(true);
  }, [streaming]);

  // Auto-collapse when streaming ends
  useEffect(() => {
    if (!streaming && expanded) {
      const timer = setTimeout(() => setExpanded(false), 500);
      return () => clearTimeout(timer);
    }
  }, [streaming, expanded]);

  const agentType = extractAgentType(traces);
  const toolCalls = extractToolCalls(traces);
  const totalMs = extractTotalMs(traces);
  const summary = summarizeTools(toolCalls);

  const accentColor = node === "dispatch" ? "#34d399" : "#3e5c72";

  return (
    <div className="animate-fade-in self-start pl-[42px] max-w-[85%]">
      <div
        className="rounded-sm bg-grim-trace-bg border border-grim-border/50 overflow-hidden"
        style={{ borderLeftWidth: 3, borderLeftColor: accentColor }}
      >
        {/* Compact header — always visible */}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-grim-surface-hover transition-colors"
        >
          <span className="text-[11px] text-grim-text-dim select-none">
            {expanded ? "▼" : "▶"}
          </span>
          <span
            className="text-[12px] font-mono font-semibold"
            style={{ color: accentColor }}
          >
            {agentType}
          </span>
          <span className="text-[11px] text-grim-text-dim">·</span>
          <span className="text-[12px] font-mono text-grim-text truncate">
            {summary}
          </span>
          {totalMs > 0 && (
            <>
              <span className="text-[11px] text-grim-text-dim ml-auto">·</span>
              <span className="text-[11px] text-grim-text-dim tabular-nums shrink-0">
                {totalMs}ms
              </span>
            </>
          )}
          {streaming && (
            <span className="text-[10px] text-grim-accent animate-pulse ml-1 shrink-0">
              live
            </span>
          )}
        </button>

        {/* Expanded detail — terminal-style output */}
        {expanded && (
          <div className="border-t border-grim-border/30 px-3 py-2 space-y-1.5 max-h-64 overflow-y-auto">
            {/* Tool calls with terminal > prefix */}
            {toolCalls.map((tc, i) => (
              <div key={i} className="font-mono text-[11px] leading-relaxed">
                <div className="flex gap-1.5">
                  <span className="text-grim-accent select-none">&gt;</span>
                  <span className="text-trace-tool">
                    {tc.tool}
                    {tc.input && (
                      <span className="text-grim-text-dim ml-1 break-all">
                        {tc.input.length > 120 ? tc.input.slice(0, 120) + "…" : tc.input}
                      </span>
                    )}
                  </span>
                </div>
                {tc.output && (
                  <div className="text-grim-text pl-4 whitespace-pre-wrap break-all text-[10.5px]">
                    {tc.output.length > 500 ? tc.output.slice(0, 500) + "\n…" : tc.output}
                  </div>
                )}
              </div>
            ))}

            {/* Agent response text */}
            {content && (
              <div className="font-mono text-[11px] text-grim-text-dim border-t border-grim-border/20 pt-1.5 mt-1">
                <span className="text-[10px] uppercase tracking-wider text-grim-text-dim">
                  response:
                </span>
                <div className="text-grim-text mt-0.5 whitespace-pre-wrap">
                  {content.length > 300 ? content.slice(0, 300) + "…" : content}
                </div>
              </div>
            )}

            {/* Empty state while streaming */}
            {toolCalls.length === 0 && !content && streaming && (
              <div className="flex items-center gap-2 py-1">
                <div className="flex gap-1">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="w-1.5 h-1.5 rounded-full bg-grim-accent animate-pulse-dot"
                      style={{ animationDelay: `${i * 0.15}s` }}
                    />
                  ))}
                </div>
                <span className="text-[11px] text-grim-text-dim font-mono">
                  executing...
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
