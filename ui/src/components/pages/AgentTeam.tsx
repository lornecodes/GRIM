"use client";

import { IconAgents } from "@/components/icons/NavIcons";

export function AgentTeam() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconAgents size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Agent Team</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Companion, Coder, Researcher, Operator — agent status, tasks, and performance. Coming soon.
      </p>
    </div>
  );
}
