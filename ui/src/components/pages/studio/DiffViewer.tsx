"use client";

import { useState, useMemo } from "react";
import type { WorkspaceDiff } from "@/hooks/useJobDetail";

interface DiffHunk {
  file: string;
  lines: { type: "add" | "del" | "context" | "header"; text: string }[];
}

function parseDiff(raw: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let current: DiffHunk | null = null;

  for (const line of raw.split("\n")) {
    if (line.startsWith("diff --git")) {
      // Extract filename
      const match = line.match(/b\/(.+)$/);
      current = { file: match?.[1] ?? "unknown", lines: [] };
      hunks.push(current);
    } else if (current) {
      if (line.startsWith("@@")) {
        current.lines.push({ type: "header", text: line });
      } else if (line.startsWith("+") && !line.startsWith("+++")) {
        current.lines.push({ type: "add", text: line.slice(1) });
      } else if (line.startsWith("-") && !line.startsWith("---")) {
        current.lines.push({ type: "del", text: line.slice(1) });
      } else if (!line.startsWith("+++") && !line.startsWith("---") && !line.startsWith("index ")) {
        current.lines.push({ type: "context", text: line.startsWith(" ") ? line.slice(1) : line });
      }
    }
  }

  return hunks;
}

const LINE_COLORS = {
  add: "bg-green-400/10 text-green-300",
  del: "bg-red-400/10 text-red-300",
  context: "text-grim-text-dim",
  header: "text-cyan-400 bg-cyan-400/5",
};

export function DiffViewer({ diff }: { diff: WorkspaceDiff | null }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const hunks = useMemo(() => {
    if (!diff?.full_diff) return [];
    return parseDiff(diff.full_diff);
  }, [diff?.full_diff]);

  if (!diff) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        No workspace diff available
      </div>
    );
  }

  const toggleFile = (file: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  };

  return (
    <div className="space-y-2">
      {/* Changed files summary */}
      {diff.changed_files && diff.changed_files.length > 0 && (
        <div className="flex gap-1.5 flex-wrap mb-3">
          {diff.changed_files.map((f) => (
            <span
              key={f}
              className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-grim-surface border border-grim-border text-grim-text-dim"
            >
              {f}
            </span>
          ))}
        </div>
      )}

      {/* Diff stat */}
      {diff.diff_stat && (
        <pre className="text-[10px] text-grim-text-dim font-mono bg-grim-surface border border-grim-border rounded-md p-2 overflow-x-auto">
          {diff.diff_stat}
        </pre>
      )}

      {/* Diff hunks */}
      {hunks.map((hunk, i) => (
        <div key={i} className="border border-grim-border rounded-md overflow-hidden">
          {/* File header */}
          <button
            onClick={() => toggleFile(hunk.file)}
            className="w-full flex items-center gap-2 px-3 py-1.5 bg-grim-surface hover:bg-grim-surface-hover text-left transition-colors"
          >
            <span className="text-[10px] text-grim-text-dim">{collapsed.has(hunk.file) ? "+" : "-"}</span>
            <span className="text-[11px] font-mono text-grim-accent">{hunk.file}</span>
          </button>

          {/* Lines */}
          {!collapsed.has(hunk.file) && (
            <div className="font-mono text-[10px] overflow-x-auto">
              {hunk.lines.map((line, j) => (
                <div
                  key={j}
                  className={`px-3 py-0 leading-[18px] whitespace-pre ${LINE_COLORS[line.type]}`}
                >
                  {line.type === "add" && <span className="text-green-400 mr-2">+</span>}
                  {line.type === "del" && <span className="text-red-400 mr-2">-</span>}
                  {line.type === "context" && <span className="mr-3"> </span>}
                  {line.text}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {hunks.length === 0 && !diff.diff_stat && (
        <div className="text-[11px] text-grim-text-dim text-center py-4">
          No changes detected
        </div>
      )}
    </div>
  );
}
