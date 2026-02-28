"use client";

import { IconDashboard } from "@/components/icons/NavIcons";

export function DashboardHome() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconDashboard size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Mission Control</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Status overview, activity feed, and key metrics. Coming soon.
      </p>
    </div>
  );
}
