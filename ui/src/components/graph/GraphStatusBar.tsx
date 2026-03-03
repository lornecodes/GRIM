"use client";

interface GraphStatusBarProps {
  activeSessions: number;
  nodeCount: number;
  edgeCount: number;
  isStreaming: boolean;
}

export function GraphStatusBar({
  activeSessions,
  nodeCount,
  edgeCount,
  isStreaming,
}: GraphStatusBarProps) {
  return (
    <div className="bg-grim-surface border border-grim-border rounded-lg px-4 py-2 flex items-center gap-4 text-xs text-grim-text-dim">
      {/* Session count */}
      <div className="flex items-center gap-1.5">
        <div
          className={`w-1.5 h-1.5 rounded-full ${
            activeSessions > 0 ? "bg-green-400" : "bg-grim-text-dim/30"
          }`}
        />
        <span>
          {activeSessions} active{" "}
          {activeSessions === 1 ? "session" : "sessions"}
        </span>
      </div>

      <span className="text-grim-border">|</span>

      {/* Topology stats */}
      <span>{nodeCount} nodes</span>
      <span>{edgeCount} edges</span>

      {/* Live indicator */}
      {isStreaming && (
        <>
          <span className="text-grim-border">|</span>
          <div className="flex items-center gap-1.5 ml-auto">
            <div className="w-1.5 h-1.5 rounded-full bg-trace-node animate-pulse" />
            <span className="text-trace-node">Executing</span>
          </div>
        </>
      )}
    </div>
  );
}
