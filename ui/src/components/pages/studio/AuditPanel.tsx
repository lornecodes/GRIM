"use client";

import type { TranscriptEntry } from "@/lib/poolTypes";

interface Props {
  transcript: TranscriptEntry[];
  jobType: string;
}

export function AuditPanel({ transcript, jobType }: Props) {
  if (jobType === "audit") {
    // Audit jobs: show full audit findings from text entries
    const auditEntries = transcript.filter(
      (e) => e.type === "text" && e.text,
    );

    if (auditEntries.length === 0) {
      return (
        <div className="text-[11px] text-grim-text-dim text-center py-8">
          No audit results yet
        </div>
      );
    }

    return (
      <div className="space-y-3">
        <div className="text-[10px] text-grim-text-dim uppercase tracking-wider">Audit Findings</div>
        {auditEntries.map((entry, i) => (
          <div
            key={i}
            className="bg-grim-surface border border-grim-border rounded-md p-3"
          >
            <div className="text-[11px] text-grim-text whitespace-pre-wrap font-mono">
              {entry.text}
            </div>
          </div>
        ))}
      </div>
    );
  }

  // Non-audit jobs: show tool usage audit trail
  const toolEntries = transcript.filter(
    (e) => e.type === "tool_use" || e.type === "tool_result",
  );

  // Check for denied tools (audit gate denials appear in tool_result output)
  const deniedEntries = transcript.filter(
    (e) => e.type === "tool_result" && e.outputPreview?.includes("Audit gate denied"),
  );

  if (toolEntries.length === 0) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        No tool activity recorded yet
      </div>
    );
  }

  // Summarize tool usage
  const toolCounts: Record<string, number> = {};
  for (const e of toolEntries) {
    if (e.type === "tool_use" && e.toolName) {
      toolCounts[e.toolName] = (toolCounts[e.toolName] || 0) + 1;
    }
  }
  const sortedTools = Object.entries(toolCounts).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-4">
      {/* Denied calls */}
      {deniedEntries.length > 0 && (
        <div>
          <div className="text-[10px] text-red-400 uppercase tracking-wider mb-2">
            Denied ({deniedEntries.length})
          </div>
          {deniedEntries.map((entry, i) => (
            <div
              key={i}
              className="bg-red-950/30 border border-red-800/50 rounded-md p-2 mb-1"
            >
              <div className="text-[11px] text-red-300 font-mono">
                {entry.outputPreview}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tool usage summary */}
      <div>
        <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-2">
          Tool Usage ({toolEntries.length} calls)
        </div>
        <div className="bg-grim-surface border border-grim-border rounded-md p-3">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-grim-text-dim text-left">
                <th className="pb-1">Tool</th>
                <th className="pb-1 text-right">Calls</th>
              </tr>
            </thead>
            <tbody>
              {sortedTools.map(([name, count]) => (
                <tr key={name} className="text-grim-text border-t border-grim-border/50">
                  <td className="py-0.5 font-mono">{name}</td>
                  <td className="py-0.5 text-right">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
