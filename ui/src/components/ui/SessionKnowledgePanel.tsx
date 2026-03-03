"use client";

import { useState, useRef, useEffect } from "react";
import { useSessionKnowledge } from "@/hooks/useSessionKnowledge";
import { KnowledgeGraph, DOMAIN_COLORS } from "./KnowledgeGraph";
import { KnowledgeTurnSlider } from "./KnowledgeTurnSlider";

interface SessionKnowledgePanelProps {
  sessionId?: string;
  onBack: () => void;
}

export function SessionKnowledgePanel({ sessionId, onBack }: SessionKnowledgePanelProps) {
  const sk = useSessionKnowledge(sessionId);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 380, height: 300 });
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  // Measure container for graph sizing
  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver(([entry]) => {
      setDimensions({
        width: entry.contentRect.width,
        height: Math.max(200, entry.contentRect.height - 80), // leave room for slider + header
      });
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  const selectedEntry = selectedNode
    ? sk.entries.find((e) => e.fdo_id === selectedNode)
    : null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-grim-border/30">
        <button
          onClick={onBack}
          className="text-[11px] text-grim-text-dim hover:text-grim-text transition-colors"
        >
          ← Chat
        </button>
        <span className="text-[11px] font-medium text-grim-text">
          Session Knowledge
        </span>
        <span className="ml-auto text-[10px] text-grim-text-dim">
          {sk.totalCount} concepts
        </span>
        <button
          onClick={sk.refresh}
          className="text-[10px] px-1.5 py-0.5 rounded bg-grim-grim-bg/40 border border-grim-border/30 hover:border-grim-border/60 text-grim-text-dim transition-colors"
          disabled={sk.isLoading}
        >
          {sk.isLoading ? "..." : "↻"}
        </button>
      </div>

      {/* Graph area */}
      <div ref={containerRef} className="flex-1 min-h-0 relative">
        {sk.graphData && sk.graphData.count > 0 ? (
          <KnowledgeGraph
            data={sk.graphData}
            width={dimensions.width}
            height={dimensions.height}
            onNodeClick={setSelectedNode}
            highlightNodeId={selectedNode}
            mini
          />
        ) : (
          <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
            {sk.isLoading ? "Loading..." : "No knowledge accumulated yet"}
          </div>
        )}
      </div>

      {/* Turn slider */}
      {sk.maxTurn > 0 && (
        <KnowledgeTurnSlider
          maxTurn={sk.maxTurn}
          currentTurn={sk.currentTurn}
          onTurnChange={sk.filterToTurn}
          totalNodes={sk.graphData?.count || 0}
        />
      )}

      {/* Selected node detail */}
      {selectedEntry && (
        <div className="px-3 py-2 border-t border-grim-border/30 bg-grim-grim-bg/30 max-h-[120px] overflow-y-auto">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: DOMAIN_COLORS[selectedEntry.fdo_domain] || "#8888a0" }}
            />
            <span className="text-[11px] font-medium text-grim-text">
              {selectedEntry.fdo_title}
            </span>
          </div>
          <div className="text-[10px] text-grim-text-dim space-y-0.5">
            <div>Domain: {selectedEntry.fdo_domain} · Confidence: {selectedEntry.fdo_confidence?.toFixed(1)}</div>
            <div>Fetched by: {selectedEntry.fetched_by} · Turn: {selectedEntry.fetched_turn}</div>
            <div>Referenced: {selectedEntry.hit_count}× · Query: "{selectedEntry.query}"</div>
          </div>
        </div>
      )}
    </div>
  );
}
