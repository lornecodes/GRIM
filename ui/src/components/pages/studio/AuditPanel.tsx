"use client";

import type { TranscriptEntry } from "@/lib/poolTypes";

interface Props {
  transcript: TranscriptEntry[];
  jobType: string;
}

export function AuditPanel({ transcript, jobType }: Props) {
  if (jobType !== "audit") {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        Audit results are only available for audit-type jobs
      </div>
    );
  }

  // Extract audit-related text entries from transcript
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
