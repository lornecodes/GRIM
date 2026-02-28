"use client";

import { ResponsiveContainer } from "recharts";

interface ChartContainerProps {
  title?: string;
  height?: number;
  children: React.ReactNode;
}

export function ChartContainer({
  title,
  height = 240,
  children,
}: ChartContainerProps) {
  return (
    <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
      {title && (
        <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
          {title}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

// Shared Recharts theme constants
export const CHART_COLORS = {
  input: "#7c6fef",   // grim.accent
  output: "#34d399",  // trace.tool
  grid: "#2a2a3a",    // grim.border
  axis: "#8888a0",    // grim.text-dim
  tooltip: {
    bg: "#12121a",    // grim.surface
    border: "#2a2a3a",
  },
} as const;
