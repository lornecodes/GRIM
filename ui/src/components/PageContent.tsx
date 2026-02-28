"use client";

import { useGrimStore } from "@/store";
import { pages } from "./pages/PageRegistry";

export function PageContent() {
  const activePage = useGrimStore((s) => s.activePage);
  const current = pages.find((p) => p.id === activePage);
  const ActiveComponent = current?.component;

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <div className="flex-1 overflow-y-auto p-6">
        {ActiveComponent ? (
          <ActiveComponent />
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-grim-accent text-3xl mb-3 font-bold">G</div>
              <div className="text-sm text-grim-text-dim">GRIM Mission Control</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
