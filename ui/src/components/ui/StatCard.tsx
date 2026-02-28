"use client";

interface StatCardProps {
  label: string;
  value: string | number;
  subtitle?: string;
  accent?: boolean;
}

export function StatCard({ label, value, subtitle, accent }: StatCardProps) {
  return (
    <div className="bg-grim-surface border border-grim-border rounded-xl p-4 flex flex-col gap-1">
      <div className="text-[11px] text-grim-text-dim uppercase tracking-wider">
        {label}
      </div>
      <div
        className={`text-2xl font-bold ${accent ? "text-grim-accent" : "text-grim-text"}`}
      >
        {value}
      </div>
      {subtitle && (
        <div className="text-[11px] text-grim-text-dim">{subtitle}</div>
      )}
    </div>
  );
}
