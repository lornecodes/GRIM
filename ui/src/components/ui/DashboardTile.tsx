"use client";

import type { ReactNode } from "react";

interface DashboardTileProps {
  title: string;
  icon?: ReactNode;
  headerRight?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function DashboardTile({
  title,
  icon,
  headerRight,
  children,
  className,
}: DashboardTileProps) {
  return (
    <div
      className={`bg-grim-surface border border-grim-border rounded-xl p-4 flex flex-col ${className ?? ""}`}
    >
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <span className="text-[11px] text-grim-text-dim uppercase tracking-wider">
          {title}
        </span>
        {headerRight && <div className="ml-auto">{headerRight}</div>}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}
