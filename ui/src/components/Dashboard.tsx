"use client";

import { useGrimStore } from "@/store";
import { widgets } from "./dashboard/WidgetRegistry";

export function Dashboard() {
  const activeWidget = useGrimStore((s) => s.activeDashboardWidget);
  const setWidget = useGrimStore((s) => s.setActiveDashboardWidget);

  const current = widgets.find((w) => w.id === activeWidget);
  const ActiveComponent = current?.component;

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      {/* Widget nav bar */}
      {widgets.length > 1 && (
        <div className="flex items-center gap-1 px-5 py-2 border-b border-grim-border shrink-0">
          {widgets.map((w) => (
            <button
              key={w.id}
              onClick={() => setWidget(w.id)}
              className={`px-3 py-1 text-[11px] rounded-md transition-all ${
                w.id === activeWidget
                  ? "bg-grim-accent/10 text-grim-accent border border-grim-accent/30"
                  : "text-grim-text-dim hover:text-grim-text border border-transparent"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      )}

      {/* Active widget */}
      <div className="flex-1 overflow-y-auto p-6">
        {ActiveComponent ? (
          <ActiveComponent />
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-grim-accent text-3xl mb-3 font-bold">G</div>
              <div className="text-sm text-grim-text-dim">GRIM Mission Control</div>
              <div className="text-xs text-grim-text-dim mt-1">
                Select a dashboard widget to get started.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
