"use client";

import { useState, useEffect, useRef } from "react";
import type { TraceEvent } from "@/lib/types";
import { TraceEntry } from "./TraceEntry";

interface TraceLogProps {
  traces: TraceEvent[];
  defaultExpanded: boolean;
}

export function TraceLog({ traces, defaultExpanded }: TraceLogProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-expand while streaming, collapse when done
  useEffect(() => {
    setExpanded(defaultExpanded);
  }, [defaultExpanded]);

  // Auto-scroll trace log when new entries arrive
  useEffect(() => {
    if (expanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [traces.length, expanded]);

  // Count by category
  const counts = traces.reduce(
    (acc, t) => {
      acc[t.cat] = (acc[t.cat] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  const totalMs = traces
    .filter((t) => t.cat === "graph" && t.duration_ms)
    .reduce((max, t) => Math.max(max, t.duration_ms || 0), 0);

  return (
    <div className="mt-1.5">
      {/* Toggle header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-[10px] text-grim-text-dim hover:text-grim-text transition-colors py-1 px-1 w-full"
      >
        <span className="transition-transform" style={{ transform: expanded ? "rotate(90deg)" : "none" }}>
          &#9656;
        </span>
        <span>
          Agent Trace ({traces.length} event{traces.length !== 1 ? "s" : ""})
        </span>
        {totalMs > 0 && (
          <span className="text-grim-text-dim">{totalMs}ms</span>
        )}
        <div className="flex gap-1.5 ml-auto">
          {counts.node && (
            <span className="text-trace-node">{counts.node} nodes</span>
          )}
          {counts.tool && (
            <span className="text-trace-tool">{counts.tool} tools</span>
          )}
          {counts.claw && (
            <span className="text-orange-400">{counts.claw} claw</span>
          )}
          {counts.llm && (
            <span className="text-trace-llm">{counts.llm} llm</span>
          )}
        </div>
      </button>

      {/* Trace entries */}
      {expanded && (
        <div
          ref={scrollRef}
          className="max-h-48 overflow-y-auto bg-grim-trace-bg rounded-lg border border-grim-border p-2 mt-1"
        >
          {traces.map((trace, i) => (
            <TraceEntry key={i} trace={trace} />
          ))}
        </div>
      )}
    </div>
  );
}
