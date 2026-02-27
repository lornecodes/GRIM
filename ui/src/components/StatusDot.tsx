"use client";

import type { ConnectionStatus } from "@/lib/types";

interface StatusDotProps {
  status: ConnectionStatus;
  sessionId: string;
}

const statusConfig: Record<
  ConnectionStatus,
  { color: string; label: string }
> = {
  connected: { color: "bg-grim-success", label: "session" },
  connecting: { color: "bg-grim-warning animate-pulse", label: "connecting" },
  disconnected: { color: "bg-grim-error", label: "disconnected" },
};

export function StatusDot({ status, sessionId }: StatusDotProps) {
  const config = statusConfig[status];

  return (
    <div className="flex items-center gap-1.5 text-[11px] text-grim-text-dim">
      <div
        className={`w-[7px] h-[7px] rounded-full transition-colors ${config.color}`}
      />
      <span>
        {status === "connected" ? `${config.label} ${sessionId}` : config.label}
      </span>
    </div>
  );
}
