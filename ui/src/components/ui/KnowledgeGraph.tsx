"use client";

import { useCallback, useMemo, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
import type { GraphData } from "@/hooks/useVaultExplorer";

// Dynamic import — react-force-graph-2d uses Canvas/window at import time
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
      Loading graph...
    </div>
  ),
});

// ---------------------------------------------------------------------------
// Domain color map (12 domains)
// ---------------------------------------------------------------------------

export const DOMAIN_COLORS: Record<string, string> = {
  physics: "#60a5fa",
  "ai-systems": "#c084fc",
  tools: "#34d399",
  personal: "#fbbf24",
  modelling: "#f97316",
  computing: "#f472b6",
  projects: "#7c6fef",
  people: "#a78bfa",
  interests: "#22d3ee",
  notes: "#8888a0",
  media: "#fb923c",
  journal: "#94a3b8",
};

// ---------------------------------------------------------------------------
// Graph node/link types for react-force-graph
// ---------------------------------------------------------------------------

interface GNode {
  id: string;
  name: string;
  domain: string;
  color: string;
  val: number;
  x?: number;
  y?: number;
}

interface GLink {
  source: string;
  target: string;
  type: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface KnowledgeGraphProps {
  data: GraphData;
  width: number;
  height: number;
  onNodeClick?: (id: string) => void;
  highlightNodeId?: string | null;
  mini?: boolean;
}

export function KnowledgeGraph({
  data,
  width,
  height,
  onNodeClick,
  highlightNodeId,
  mini,
}: KnowledgeGraphProps) {
  const fgRef = useRef<any>(null);

  // Convert GraphData to react-force-graph format
  const graphFormatted = useMemo(() => {
    // Count connections per node for sizing
    const connectionCount: Record<string, number> = {};
    for (const e of data.edges) {
      connectionCount[e.from] = (connectionCount[e.from] || 0) + 1;
      connectionCount[e.to] = (connectionCount[e.to] || 0) + 1;
    }

    const nodes: GNode[] = Object.values(data.nodes).map((n) => ({
      id: n.id,
      name: n.title,
      domain: n.domain,
      color: DOMAIN_COLORS[n.domain] || "#8888a0",
      val: Math.max(1, connectionCount[n.id] || 0),
    }));

    // Only include links whose source+target exist in nodes
    const nodeIds = new Set(nodes.map((n) => n.id));
    const links: GLink[] = data.edges
      .filter((e) => nodeIds.has(e.from) && nodeIds.has(e.to))
      .map((e) => ({
        source: e.from,
        target: e.to,
        type: e.type,
      }));

    return { nodes, links };
  }, [data]);

  // Custom node rendering
  const nodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const size = mini ? 2.5 : Math.max(3, Math.sqrt(node.val || 1) * 2.5);
      const isHighlighted = node.id === highlightNodeId;

      // Draw circle
      ctx.beginPath();
      ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
      ctx.fillStyle = isHighlighted ? "#ffffff" : node.color;
      ctx.globalAlpha = isHighlighted ? 1.0 : 0.85;
      ctx.fill();
      ctx.globalAlpha = 1.0;

      // Draw label (full mode only, when zoomed in enough)
      if (!mini && globalScale > 1.5) {
        const fontSize = Math.max(9, 11 / globalScale);
        ctx.font = `${fontSize}px JetBrains Mono, monospace`;
        ctx.fillStyle = isHighlighted ? "#ffffff" : "#c0c0d0";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(
          node.name.length > 30 ? node.name.slice(0, 28) + "…" : node.name,
          node.x,
          node.y + size + 2
        );
      }
    },
    [highlightNodeId, mini]
  );

  // Node pointer area for click detection
  const nodePointerAreaPaint = useCallback(
    (node: any, color: string, ctx: CanvasRenderingContext2D) => {
      const size = mini ? 4 : Math.max(5, Math.sqrt(node.val || 1) * 3);
      ctx.beginPath();
      ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    },
    [mini]
  );

  // Configure forces once when graph mounts/data changes.
  // Full mode: library's built-in auto-zoom (4/∛(nodeCount)) works well.
  // Mini mode: auto-zoom is too zoomed in for the small canvas, so we
  // override with zoomToFit after the library settles.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(mini ? -30 : -100);
    fg.d3Force("link")?.distance(mini ? 20 : 50);
    fg.d3Force("center")?.strength(0.05);

    if (mini && graphFormatted.nodes.length > 0) {
      // Override the library's auto-zoom for mini mode — fire after it settles
      const timer = setTimeout(() => {
        fg.zoomToFit(0, 10);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [mini, graphFormatted]);

  return (
    <ForceGraph2D
      ref={fgRef}
      graphData={graphFormatted}
      width={width}
      height={height}
      backgroundColor="#0a0a0f"
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={nodePointerAreaPaint}
      linkColor={() => "rgba(124, 111, 239, 0.12)"}
      linkWidth={0.4}
      linkDirectionalParticles={0}
      onNodeClick={
        onNodeClick
          ? (node: any) => onNodeClick(node.id as string)
          : undefined
      }
      nodeRelSize={1}
      cooldownTicks={mini ? 60 : 120}
      warmupTicks={mini ? 40 : 80}
      d3AlphaDecay={0.03}
      d3VelocityDecay={0.3}
      enableZoomInteraction={!mini}
      enablePanInteraction={!mini}
      enableNodeDrag={!mini}
    />
  );
}
