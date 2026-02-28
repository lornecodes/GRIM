"use client";

import { IconVault } from "@/components/icons/NavIcons";

export function VaultExplorer() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconVault size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Vault Explorer</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Browse Kronos FDOs, search the knowledge graph, and explore connections. Coming soon.
      </p>
    </div>
  );
}
