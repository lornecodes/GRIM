"use client";

import { IconEvolution } from "@/components/icons/NavIcons";

export function EvolutionView() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconEvolution size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Field State</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Coherence, valence, and uncertainty over time. Evolution snapshots and drift tracking. Coming soon.
      </p>
    </div>
  );
}
