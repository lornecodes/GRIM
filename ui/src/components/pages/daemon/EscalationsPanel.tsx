"use client";

import { useState } from "react";
import type { PipelineItem } from "@/lib/daemonTypes";
import { ASSIGNEE_BADGE } from "@/lib/daemonTypes";

interface EscalationsPanelProps {
  escalations: PipelineItem[];
  onResolve: (id: string, answer: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
}

export function EscalationsPanel({ escalations, onResolve, onRetry }: EscalationsPanelProps) {
  const blocked = escalations.filter((e) => e.status === "blocked");
  const failed = escalations.filter((e) => e.status === "failed");

  if (blocked.length === 0 && failed.length === 0) {
    return (
      <div className="bg-grim-surface border border-grim-border rounded-lg p-6 text-center">
        <div className="text-green-400 text-lg mb-1">&#10003;</div>
        <div className="text-[11px] text-grim-text-dim">All clear — no escalations</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {blocked.length > 0 && (
        <div>
          <div className="text-[10px] text-orange-400 uppercase tracking-wider mb-2">
            Needs Answer ({blocked.length})
          </div>
          <div className="space-y-2">
            {blocked.map((item) => (
              <BlockedCard key={item.id} item={item} onResolve={onResolve} />
            ))}
          </div>
        </div>
      )}
      {failed.length > 0 && (
        <div>
          <div className="text-[10px] text-red-400 uppercase tracking-wider mb-2">
            Failed ({failed.length})
          </div>
          <div className="space-y-2">
            {failed.map((item) => (
              <FailedCard key={item.id} item={item} onRetry={onRetry} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function BlockedCard({
  item,
  onResolve,
}: {
  item: PipelineItem;
  onResolve: (id: string, answer: string) => Promise<void>;
}) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const badge = item.assignee ? ASSIGNEE_BADGE[item.assignee] : null;

  const handleResolve = async () => {
    if (!answer.trim()) return;
    setSubmitting(true);
    await onResolve(item.id, answer.trim());
    setAnswer("");
    setSubmitting(false);
  };

  return (
    <div className="bg-grim-surface border border-orange-400/20 rounded-md p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-1.5 h-1.5 rounded-full bg-orange-400 shrink-0" />
        <span className="text-[11px] text-grim-text font-mono truncate">{item.story_id}</span>
        {badge && (
          <span className={`text-[9px] px-1.5 py-0.5 rounded border ${badge.color} ml-auto`}>
            {badge.label}
          </span>
        )}
      </div>
      {item.error && (
        <div className="text-[11px] text-orange-300 mb-2 whitespace-pre-wrap">{item.error}</div>
      )}
      <textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Provide answer..."
        rows={2}
        className="w-full bg-grim-bg border border-grim-border rounded px-2 py-1.5 text-[11px] text-grim-text font-mono resize-none focus:outline-none focus:border-grim-accent"
      />
      <button
        onClick={handleResolve}
        disabled={submitting || !answer.trim()}
        className="mt-1.5 text-[10px] px-3 py-1 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors disabled:opacity-40"
      >
        {submitting ? "Resolving..." : "Resolve"}
      </button>
    </div>
  );
}

function FailedCard({
  item,
  onRetry,
}: {
  item: PipelineItem;
  onRetry: (id: string) => Promise<void>;
}) {
  const [retrying, setRetrying] = useState(false);
  const badge = item.assignee ? ASSIGNEE_BADGE[item.assignee] : null;

  const handleRetry = async () => {
    setRetrying(true);
    await onRetry(item.id);
    setRetrying(false);
  };

  return (
    <div className="bg-grim-surface border border-red-400/20 rounded-md p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-1.5 h-1.5 rounded-full bg-red-400 shrink-0" />
        <span className="text-[11px] text-grim-text font-mono truncate">{item.story_id}</span>
        {badge && (
          <span className={`text-[9px] px-1.5 py-0.5 rounded border ${badge.color} ml-auto`}>
            {badge.label}
          </span>
        )}
      </div>
      {item.error && (
        <div className="text-[11px] text-red-300 mb-2 font-mono whitespace-pre-wrap line-clamp-3">{item.error}</div>
      )}
      <div className="flex items-center gap-2">
        <span className="text-[9px] text-grim-text-dim">
          {item.attempts} attempt{item.attempts !== 1 ? "s" : ""}
        </span>
        <button
          onClick={handleRetry}
          disabled={retrying}
          className="text-[10px] px-3 py-1 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors disabled:opacity-40 ml-auto"
        >
          {retrying ? "Retrying..." : "Retry"}
        </button>
      </div>
    </div>
  );
}
