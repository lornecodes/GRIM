"use client";

/**
 * GraphStudio — Container component for the GRIM topology visualizer.
 *
 * Fetches topology data from /api/graph/topology, tracks active sessions,
 * and renders a split-panel layout with the force-graph canvas on the left
 * and a node inspector on the right.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import type { GraphTopology, TopologyNode } from "@/lib/graphTopology";
import { NodeInspector } from "./NodeInspector";
import { GraphStatusBar } from "./GraphStatusBar";
import { useGraphOverlay } from "@/hooks/useGraphOverlay";

// Dynamic import — ForceGraph2D uses Canvas/window
const TopologyGraph = dynamic(
  () =>
    import("./TopologyGraph").then((m) => ({
      default: m.TopologyGraph,
    })),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
        Loading graph...
      </div>
    ),
  }
);

const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GraphStudio() {
  const [topology, setTopology] = useState<GraphTopology | null>(null);
  const [activeSessions, setActiveSessions] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [togglingAgent, setTogglingAgent] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 500 });

  const overlay = useGraphOverlay();

  // Fetch topology
  const fetchTopology = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/api/graph/topology`);
      if (resp.ok) {
        const data = await resp.json();
        setTopology(data);
      }
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  // Fetch session count
  const fetchSessions = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/api/graph/sessions`);
      if (resp.ok) {
        const data = await resp.json();
        setActiveSessions(data.active ?? 0);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchTopology();
    fetchSessions();
    const interval = setInterval(fetchSessions, 5000);
    return () => clearInterval(interval);
  }, [fetchTopology, fetchSessions]);

  // ResizeObserver for responsive canvas
  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDims({ width: Math.floor(width), height: Math.floor(height) });
      }
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  // Toggle agent enabled state
  const handleToggle = useCallback(
    async (id: string) => {
      if (!topology) return;
      setTogglingAgent(id);
      try {
        const resp = await fetch(`${apiBase}/api/agents/${id}/toggle`, {
          method: "POST",
        });
        if (resp.ok) {
          const data = await resp.json();
          setTopology((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              nodes: {
                ...prev.nodes,
                [id]: { ...prev.nodes[id], enabled: data.enabled },
              },
            };
          });
        }
      } catch {
        /* ignore */
      }
      setTogglingAgent(null);
    },
    [topology]
  );

  const selectedNode: TopologyNode | null =
    selectedNodeId && topology ? topology.nodes[selectedNodeId] ?? null : null;

  return (
    <div className="flex flex-col gap-3">
      {/* Status bar */}
      <GraphStatusBar
        activeSessions={activeSessions}
        nodeCount={topology ? Object.keys(topology.nodes).length : 0}
        edgeCount={topology?.edges.length ?? 0}
        isStreaming={overlay.isStreaming}
      />

      {/* Split panel: graph + inspector */}
      <div className="flex gap-3" style={{ height: 520 }}>
        {/* Graph canvas */}
        <div
          ref={containerRef}
          className="flex-1 bg-grim-bg border border-grim-border rounded-xl overflow-hidden"
        >
          {loading ? (
            <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
              Loading topology...
            </div>
          ) : !topology ? (
            <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
              Could not load graph topology. Is the server running?
            </div>
          ) : (
            <TopologyGraph
              topology={topology}
              overlay={overlay}
              width={dims.width}
              height={dims.height}
              selectedNodeId={selectedNodeId}
              onNodeClick={(id) =>
                setSelectedNodeId((prev) => (prev === id ? null : id))
              }
            />
          )}
        </div>

        {/* Node inspector (conditional) */}
        {selectedNode && (
          <div className="w-96 flex-shrink-0">
            <NodeInspector
              node={selectedNode}
              overlay={overlay}
              toggling={togglingAgent === selectedNode.id}
              onToggle={() => handleToggle(selectedNode.id)}
              onClose={() => setSelectedNodeId(null)}
            />
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-[9px] text-grim-text-dim px-1">
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-[#8888a0]" />
          <span>preprocessing</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-[#f59e0b]" />
          <span>routing</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-[#7c6fef]" />
          <span>companion</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-[#3b82f6]" />
          <span>agent</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-[#34d399]" />
          <span>postprocessing</span>
        </div>
        <div className="flex items-center gap-1.5 ml-4">
          <div className="w-4 border-t border-grim-border" />
          <span>static</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-4 border-t border-dashed border-[#f59e0b]" />
          <span>conditional</span>
        </div>
      </div>
    </div>
  );
}
