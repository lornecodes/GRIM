"use client";

import { useState } from "react";
import type { TranscriptEntry } from "@/lib/poolTypes";

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "..." : s;
}

export function TranscriptLine({ entry }: { entry: TranscriptEntry }) {
  const [expanded, setExpanded] = useState(false);

  switch (entry.type) {
    case "text":
      return (
        <div className="flex gap-2 py-0.5">
          <span className="text-green-400 shrink-0 select-none">&gt;</span>
          <span className="text-grim-text text-[11px] whitespace-pre-wrap break-words">
            {entry.text}
          </span>
        </div>
      );

    case "tool_use":
      return (
        <div className="py-0.5">
          <div className="flex gap-2 items-start">
            <span className="text-cyan-400 shrink-0 select-none">$</span>
            <span className="text-cyan-300 text-[11px] font-medium">{entry.toolName}</span>
            {entry.toolInput != null && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="text-[9px] text-grim-text-dim hover:text-grim-text transition-colors"
              >
                {expanded ? "[hide]" : "[show]"}
              </button>
            )}
          </div>
          {expanded && entry.toolInput != null && (
            <pre className="text-[10px] text-grim-text-dim ml-5 mt-0.5 overflow-x-auto max-h-[200px] overflow-y-auto">
              {truncate(JSON.stringify(entry.toolInput, null, 2), 2000)}
            </pre>
          )}
        </div>
      );

    case "tool_result":
      return (
        <div className="flex gap-2 py-0.5 ml-3">
          <span className="text-gray-500 shrink-0 select-none">&larr;</span>
          <span className="text-[10px] text-grim-text-dim line-clamp-2">
            {truncate(entry.outputPreview ?? "", 500)}
          </span>
        </div>
      );

    case "result":
      return (
        <div className="flex gap-2 py-1 border-t border-grim-border/30 mt-1">
          <span className="text-grim-accent shrink-0 select-none">&bull;</span>
          <span className="text-[10px] text-grim-text-dim">
            Done{entry.numTurns != null ? ` (${entry.numTurns} turns)` : ""}
            {entry.costUsd != null ? ` — $${entry.costUsd.toFixed(4)}` : ""}
          </span>
        </div>
      );

    default:
      return null;
  }
}
