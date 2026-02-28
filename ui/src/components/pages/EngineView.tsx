"use client";

import { useState, useEffect } from "react";
import { IconEngine } from "@/components/icons/NavIcons";
import { useGrimStore } from "@/store";

interface ToolInfo {
  name: string;
  description: string;
  risk_level: string;
}

interface EngineStatus {
  available: boolean;
  version?: string;
  uptime_secs?: number;
  tools?: ToolInfo[];
  metrics?: {
    requests_total: number;
    requests_failed: number;
    active_sessions: number;
    uptime_seconds: number;
  };
  message?: string;
}

const RISK_COLORS: Record<string, string> = {
  Low: "text-green-400",
  Medium: "text-yellow-400",
  High: "text-orange-400",
  Critical: "text-red-400",
};

export function EngineView() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const setIronclawStatus = useGrimStore((s) => s.setIronclawStatus);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  useEffect(() => {
    async function fetchStatus() {
      try {
        const resp = await fetch(`${apiBase}/api/ironclaw/status`);
        const data = await resp.json();
        setStatus(data);
        setIronclawStatus(data.available ? "connected" : "disconnected");
      } catch {
        setStatus({ available: false, message: "Failed to reach GRIM server" });
        setIronclawStatus("disconnected");
      } finally {
        setLoading(false);
      }
    }
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [apiBase, setIronclawStatus]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-grim-text-dim">Loading engine status...</div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconEngine size={32} className="text-grim-accent" />
        <div>
          <h2 className="text-lg font-semibold text-grim-text">IronClaw Engine</h2>
          <p className="text-xs text-grim-text-dim">
            Sandboxed execution engine — zero-trust security pipeline
          </p>
        </div>
      </div>

      {/* Status card */}
      <div className="bg-grim-surface border border-grim-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <div
            className={`w-2 h-2 rounded-full ${
              status?.available ? "bg-green-400" : "bg-red-400"
            }`}
          />
          <span className="text-sm font-medium text-grim-text">
            {status?.available ? "Connected" : "Disconnected"}
          </span>
          {status?.version && (
            <span className="text-xs text-grim-text-dim ml-auto">
              v{status.version}
            </span>
          )}
        </div>

        {status?.available && status.uptime_secs != null && (
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <span className="text-grim-text-dim">Uptime</span>
              <div className="text-grim-text font-mono">
                {formatUptime(status.uptime_secs)}
              </div>
            </div>
            {status.metrics && (
              <>
                <div>
                  <span className="text-grim-text-dim">Requests</span>
                  <div className="text-grim-text font-mono">
                    {status.metrics.requests_total}
                    {status.metrics.requests_failed > 0 && (
                      <span className="text-red-400 ml-1">
                        ({status.metrics.requests_failed} failed)
                      </span>
                    )}
                  </div>
                </div>
                <div>
                  <span className="text-grim-text-dim">Active Sessions</span>
                  <div className="text-grim-text font-mono">
                    {status.metrics.active_sessions}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {!status?.available && status?.message && (
          <p className="text-xs text-grim-text-dim mt-2">{status.message}</p>
        )}
      </div>

      {/* Tools */}
      {status?.tools && status.tools.length > 0 && (
        <div className="bg-grim-surface border border-grim-border rounded-lg p-4">
          <h3 className="text-sm font-medium text-grim-text mb-3">
            Available Tools ({status.tools.length})
          </h3>
          <div className="space-y-2">
            {status.tools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center gap-3 text-xs py-1.5 px-2 rounded bg-grim-bg/50"
              >
                <span className="font-mono text-grim-accent min-w-[120px]">
                  {tool.name}
                </span>
                <span className="text-grim-text-dim flex-1">
                  {tool.description}
                </span>
                <span
                  className={`text-[10px] font-bold uppercase ${
                    RISK_COLORS[tool.risk_level] || "text-grim-text-dim"
                  }`}
                >
                  {tool.risk_level}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Info */}
      <div className="text-[10px] text-grim-text-dim space-y-1">
        <p>IronClaw provides sandboxed tool execution with a 13-layer zero-trust security pipeline.</p>
        <p>All file, shell, and network operations pass through RBAC, command guardian, DLP, SSRF protection, and audit logging.</p>
      </div>
    </div>
  );
}

function formatUptime(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  return `${h}h ${m}m`;
}
