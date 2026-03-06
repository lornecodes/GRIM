"use client";

import { useEffect, useRef } from "react";
import type { TranscriptEntry } from "@/lib/poolTypes";
import { TranscriptLine } from "./TranscriptLine";

interface Props {
  jobId: string;
  transcript: TranscriptEntry[];
  isLive: boolean;
}

export function LiveTranscript({ jobId, transcript, isLive }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new entries arrive (only if near bottom)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    if (nearBottom || isLive) {
      el.scrollTop = el.scrollHeight;
    }
  }, [transcript.length, isLive]);

  return (
    <div className="bg-grim-bg border border-grim-border rounded-md overflow-hidden font-mono">
      {/* Terminal title bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-grim-surface border-b border-grim-border">
        {/* Traffic lights */}
        <div className="flex gap-1">
          <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
        </div>
        <span className="text-[10px] text-grim-text-dim flex-1 text-center">
          {jobId}@pool
        </span>
        {isLive && (
          <span className="text-[9px] text-green-400 animate-pulse font-medium">LIVE</span>
        )}
        {!isLive && transcript.length > 0 && (
          <span className="text-[9px] text-grim-text-dim">COMPLETE</span>
        )}
      </div>

      {/* Scrollable transcript body */}
      <div
        ref={containerRef}
        className="max-h-[500px] overflow-y-auto p-3 space-y-0"
      >
        {transcript.length === 0 && (
          <div className="text-[11px] text-grim-text-dim text-center py-8">
            {isLive ? "Waiting for agent output..." : "No transcript available"}
          </div>
        )}

        {transcript.map((entry, i) => (
          <TranscriptLine key={i} entry={entry} />
        ))}

        {isLive && (
          <div className="flex items-center gap-1 pt-1">
            <span className="text-grim-accent animate-pulse">_</span>
          </div>
        )}
      </div>
    </div>
  );
}
