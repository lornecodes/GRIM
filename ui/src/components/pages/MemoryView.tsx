"use client";

import { IconMemory } from "@/components/icons/NavIcons";

export function MemoryView() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconMemory size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Memory</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Session history, conversation search, and memory summaries. Coming soon.
      </p>
    </div>
  );
}
