"use client";

import { IconSettings } from "@/components/icons/NavIcons";

export function SettingsView() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconSettings size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Settings</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Configuration, integrations, model routing, and agent parameters. Coming soon.
      </p>
    </div>
  );
}
