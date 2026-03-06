"use client";

import { useState, useCallback, useEffect } from "react";

interface CommitInfo {
  hash: string;
  short_hash: string;
  message: string;
  author: string;
  date: string;
}

interface Props {
  workspaceId: string | null;
}

function formatRelativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.floor(diffHr / 24);
  return `${diffDays}d ago`;
}

export function CommitHistory({ workspaceId }: Props) {
  const [commits, setCommits] = useState<CommitInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchCommits = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}/api/pool/workspaces/${workspaceId}/commits`);
      if (resp.ok) {
        const data = await resp.json();
        setCommits(data.commits ?? []);
        setLoaded(true);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [apiBase, workspaceId]);

  useEffect(() => {
    if (workspaceId) {
      fetchCommits();
    }
  }, [workspaceId, fetchCommits]);

  if (!workspaceId) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        No workspace associated with this job
      </div>
    );
  }

  if (loading && !loaded) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        Loading commits...
      </div>
    );
  }

  if (loaded && commits.length === 0) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        No commits on this branch yet
      </div>
    );
  }

  return (
    <div className="border border-grim-border rounded-md overflow-hidden">
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider px-3 py-1.5 border-b border-grim-border bg-grim-surface">
        Branch Commits ({commits.length})
      </div>
      <div className="divide-y divide-grim-border/50 max-h-[400px] overflow-y-auto">
        {commits.map((c) => (
          <div key={c.hash} className="px-3 py-2 hover:bg-grim-surface-hover transition-colors">
            <div className="flex items-start gap-2">
              <span className="text-[10px] font-mono text-grim-accent shrink-0 mt-[1px]">
                {c.short_hash}
              </span>
              <span className="text-[11px] text-grim-text leading-tight flex-1 break-words">
                {c.message}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-1 ml-[calc(10ch)]">
              <span className="text-[9px] text-grim-text-dim">{c.author}</span>
              <span className="text-[9px] text-grim-text-dim">{formatRelativeTime(c.date)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
