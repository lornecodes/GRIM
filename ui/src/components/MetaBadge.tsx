"use client";

import type { ResponseMeta } from "@/lib/types";

interface MetaBadgeProps {
  meta: ResponseMeta;
}

export function MetaBadge({ meta }: MetaBadgeProps) {
  const parts: string[] = [];
  if (meta.mode) parts.push(meta.mode);
  if (meta.knowledge_count > 0) parts.push(`${meta.knowledge_count} FDOs`);
  if (meta.skills?.length) parts.push(meta.skills.join(", "));
  if (meta.total_ms) parts.push(`${meta.total_ms}ms`);

  if (parts.length === 0) return null;

  return (
    <div className="inline-flex items-center gap-1 mt-2.5 px-2 py-1 rounded bg-grim-accent/10 border border-grim-accent/20 text-[10px] text-grim-accent">
      {parts.join(" \u00B7 ")}
    </div>
  );
}
