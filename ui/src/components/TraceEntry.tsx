"use client";

import { useState } from "react";
import type { TraceEvent } from "@/lib/types";

interface TraceEntryProps {
  trace: TraceEvent;
}

const catColors: Record<string, string> = {
  node: "text-trace-node",
  llm: "text-trace-llm",
  tool: "text-trace-tool",
  graph: "text-trace-graph",
};

export function TraceEntry({ trace }: TraceEntryProps) {
  const [expanded, setExpanded] = useState(false);

  // Build detail payload
  const detail: Record<string, unknown> = {};
  if (trace.detail) Object.assign(detail, trace.detail);
  if (trace.input) detail.input = trace.input;
  if (trace.output_preview) detail.output_preview = trace.output_preview;
  if (trace.duration_ms != null) detail.duration_ms = trace.duration_ms;
  const hasDetail = Object.keys(detail).length > 0;

  return (
    <div
      className="flex gap-2 py-0.5 px-1 rounded text-[11px] leading-relaxed hover:bg-white/[0.03] cursor-default"
      onClick={() => hasDetail && setExpanded(!expanded)}
    >
      {/* Timestamp */}
      <span className="text-grim-text-dim text-[10px] tabular-nums min-w-[48px] text-right shrink-0 opacity-70">
        {trace.ms != null ? `+${trace.ms}ms` : ""}
      </span>

      {/* Category badge */}
      <span
        className={`text-[9px] font-bold uppercase tracking-wide min-w-[36px] shrink-0 pt-px ${catColors[trace.cat] || "text-grim-text-dim"}`}
      >
        {trace.cat}
      </span>

      {/* Body */}
      <div className="flex-1 min-w-0">
        <span className="text-grim-text break-words">{trace.text}</span>

        {/* Expandable detail */}
        {hasDetail && expanded && (
          <div className="mt-1 px-2 py-1.5 bg-white/[0.02] rounded text-[10px] text-grim-text-dim whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
            {JSON.stringify(detail, null, 2)}
          </div>
        )}
      </div>

      {/* Expand arrow */}
      {hasDetail && (
        <span className="text-grim-text-dim text-[10px] opacity-40 hover:opacity-100 shrink-0 select-none">
          {expanded ? "\u25BE" : "\u25B8"}
        </span>
      )}
    </div>
  );
}
