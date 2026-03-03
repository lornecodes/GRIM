"use client";

/**
 * TopologyGraph — Canvas renderer for the GRIM LangGraph topology.
 *
 * Uses react-force-graph-2d with all forces disabled and positions pinned
 * via fx/fy to create a fixed left-to-right DAG layout.  Live execution
 * state is overlaid via glow/pulse animations on active nodes and
 * highlighted traversed edges.
 */

import { useCallback, useMemo, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
import type { GraphTopology } from "@/lib/graphTopology";
import type { GraphOverlay } from "@/hooks/useGraphOverlay";
import { toCanvasPos } from "@/lib/graphTopology";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_RADIUS = 20;
const NODE_RADIUS_SMALL = 13;
const FONT = "JetBrains Mono, Consolas, monospace";

// Colors
const GLOW_ACCENT = "rgba(124, 111, 239, 0.6)";
const GLOW_ACTIVE = "rgba(96, 165, 250, 0.8)";
const EDGE_TRAVERSED = "rgba(124, 111, 239, 0.7)";
const EDGE_STATIC = "rgba(42, 42, 58, 0.9)";
const EDGE_CONDITIONAL = "rgba(245, 158, 11, 0.5)";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface TopologyGraphProps {
  topology: GraphTopology;
  overlay: GraphOverlay;
  width: number;
  height: number;
  selectedNodeId: string | null;
  onNodeClick: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TopologyGraph({
  topology,
  overlay,
  width,
  height,
  selectedNodeId,
  onNodeClick,
}: TopologyGraphProps) {
  const fgRef = useRef<any>(null);
  const frameRef = useRef(0);

  // Pulse animation for active nodes
  useEffect(() => {
    let rafId: number;
    const tick = () => {
      frameRef.current = (frameRef.current + 1) % 60;
      if (fgRef.current) fgRef.current.refresh();
      rafId = requestAnimationFrame(tick);
    };
    if (overlay.activeNodeId) {
      rafId = requestAnimationFrame(tick);
    }
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [overlay.activeNodeId]);

  // Disable all forces — positions are pinned
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force("charge", null);
    fg.d3Force("link", null);
    fg.d3Force("center", null);
    fg.d3Force("collision", null);
    // Zoom to fit after initial render
    setTimeout(() => fg.zoomToFit(300, 40), 100);
  }, [topology]);

  // Convert topology to react-force-graph format
  const graphData = useMemo(() => {
    const nodes = Object.values(topology.nodes).map((node) => {
      const pos = toCanvasPos(node.col, node.row);
      return {
        ...node,
        fx: pos.x,
        fy: pos.y,
        x: pos.x,
        y: pos.y,
      };
    });

    const links = topology.edges.map((e, i) => ({
      id: `${e.source}->${e.target}-${i}`,
      source: e.source,
      target: e.target,
      edgeType: e.type,
      label: e.label ?? "",
      isLoop: e.source === "re_dispatch" && e.target === "dispatch",
    }));

    return { nodes, links };
  }, [topology]);

  // Custom node rendering
  const nodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D) => {
      const nodeOvl = overlay.nodes[node.id];
      const isActive = nodeOvl?.active ?? false;
      const isCompleted = nodeOvl?.completed ?? false;
      const isOnPath = nodeOvl?.path ?? false;
      const isSelected = node.id === selectedNodeId;
      const isDisabled = node.enabled === false;
      const isSmall = ["preprocessing", "infra", "postprocessing"].includes(
        node.node_type
      );
      const radius = isSmall ? NODE_RADIUS_SMALL : NODE_RADIUS;

      // Pulse offset for active nodes
      const pulse =
        isActive
          ? Math.sin((frameRef.current / 60) * Math.PI * 2) * 3
          : 0;
      const drawRadius = radius + pulse;

      // Glow halo
      if (isActive || isSelected) {
        const color = isActive ? GLOW_ACTIVE : GLOW_ACCENT;
        const grad = ctx.createRadialGradient(
          node.x,
          node.y,
          radius * 0.4,
          node.x,
          node.y,
          drawRadius + 14
        );
        grad.addColorStop(0, color);
        grad.addColorStop(1, "rgba(0,0,0,0)");
        ctx.beginPath();
        ctx.arc(node.x, node.y, drawRadius + 14, 0, 2 * Math.PI);
        ctx.fillStyle = grad;
        ctx.fill();
      }

      // Completion ring
      if (isCompleted && !isActive) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 3, 0, 2 * Math.PI);
        ctx.strokeStyle = "rgba(52, 211, 153, 0.5)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // Main circle
      ctx.beginPath();
      ctx.arc(node.x, node.y, drawRadius, 0, 2 * Math.PI);
      const baseColor = isDisabled ? "#3a3a4a" : node.color;
      ctx.fillStyle = isActive
        ? "#ffffff"
        : isSelected
        ? node.color
        : baseColor;
      ctx.globalAlpha = isDisabled ? 0.35 : isOnPath ? 1.0 : 0.85;
      ctx.fill();
      ctx.globalAlpha = 1.0;

      // Selection ring
      if (isSelected && !isActive) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 2, 0, 2 * Math.PI);
        ctx.strokeStyle = "#7c6fef";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Label
      const fontSize = isSmall ? 7.5 : 9;
      ctx.font = `${fontSize}px ${FONT}`;
      ctx.fillStyle = isDisabled
        ? "#4a4a5a"
        : isActive
        ? "#0a0a0f"
        : "#e0e0e8";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const name: string = node.name;
      if (name.length > 10 && !isSmall) {
        // Split at space or midpoint
        const mid = name.indexOf(" ");
        if (mid > 0) {
          ctx.fillText(name.slice(0, mid), node.x, node.y - 4);
          ctx.fillText(name.slice(mid + 1), node.x, node.y + 5);
        } else {
          ctx.fillText(name, node.x, node.y);
        }
      } else {
        ctx.fillText(name, node.x, node.y);
      }

      // Duration badge
      if (isCompleted && (nodeOvl?.durationMs ?? 0) > 0) {
        ctx.font = `7px ${FONT}`;
        ctx.fillStyle = "rgba(52, 211, 153, 0.9)";
        ctx.fillText(`${nodeOvl.durationMs}ms`, node.x, node.y + radius + 10);
      }
    },
    [overlay, selectedNodeId]
  );

  // Custom link rendering
  const linkCanvasObject = useCallback(
    (link: any, ctx: CanvasRenderingContext2D) => {
      const src = link.source;
      const tgt = link.target;
      if (!src?.x || !tgt?.x) return;

      const edgeKey = `${src.id}->${tgt.id}`;
      const isTraversed = overlay.edges[edgeKey]?.traversed ?? false;
      const isConditional = link.edgeType === "conditional";
      const isLoop = link.isLoop;

      ctx.save();

      // Dashed for conditional
      ctx.setLineDash(isConditional ? [4, 4] : []);
      ctx.lineWidth = isTraversed ? 2.5 : 1;
      ctx.strokeStyle = isTraversed
        ? EDGE_TRAVERSED
        : isConditional
        ? EDGE_CONDITIONAL
        : EDGE_STATIC;

      const srcRadius = ["preprocessing", "infra", "postprocessing"].includes(
        src.node_type
      )
        ? NODE_RADIUS_SMALL
        : NODE_RADIUS;
      const tgtRadius = ["preprocessing", "infra", "postprocessing"].includes(
        tgt.node_type
      )
        ? NODE_RADIUS_SMALL
        : NODE_RADIUS;

      if (isLoop) {
        // Curved arc for re_dispatch -> dispatch loop
        const midX = (src.x + tgt.x) / 2 + 40;
        const midY = Math.max(src.y, tgt.y) + 50;
        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        ctx.quadraticCurveTo(midX, midY, tgt.x, tgt.y + tgtRadius);
        ctx.stroke();
      } else {
        // Straight line, clipped to node borders
        const dx = tgt.x - src.x;
        const dy = tgt.y - src.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 1) {
          ctx.restore();
          return;
        }
        const nx = dx / dist;
        const ny = dy / dist;

        const x1 = src.x + nx * srcRadius;
        const y1 = src.y + ny * srcRadius;
        const x2 = tgt.x - nx * tgtRadius;
        const y2 = tgt.y - ny * tgtRadius;

        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();

        // Arrow head
        const arrowLen = 7;
        const angle = Math.atan2(dy, dx);
        ctx.beginPath();
        ctx.moveTo(x2, y2);
        ctx.lineTo(
          x2 - arrowLen * Math.cos(angle - 0.35),
          y2 - arrowLen * Math.sin(angle - 0.35)
        );
        ctx.lineTo(
          x2 - arrowLen * Math.cos(angle + 0.35),
          y2 - arrowLen * Math.sin(angle + 0.35)
        );
        ctx.closePath();
        ctx.fillStyle = ctx.strokeStyle;
        ctx.fill();
      }

      // Edge label
      if (link.label && isConditional) {
        const labelX = isLoop
          ? (src.x + tgt.x) / 2 + 40
          : (src.x + tgt.x) / 2;
        const labelY = isLoop
          ? Math.max(src.y, tgt.y) + 50 + 10
          : (src.y + tgt.y) / 2 - 8;
        ctx.setLineDash([]);
        ctx.font = `7px ${FONT}`;
        ctx.fillStyle = isTraversed
          ? "#7c6fef"
          : "rgba(136, 136, 160, 0.7)";
        ctx.textAlign = "center";
        ctx.fillText(link.label, labelX, labelY);
      }

      ctx.restore();
    },
    [overlay.edges]
  );

  // Hit area for node clicks
  const nodePointerAreaPaint = useCallback(
    (node: any, color: string, ctx: CanvasRenderingContext2D) => {
      ctx.beginPath();
      ctx.arc(node.x, node.y, NODE_RADIUS + 6, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    },
    []
  );

  return (
    <ForceGraph2D
      ref={fgRef}
      graphData={graphData}
      width={width}
      height={height}
      backgroundColor="#0a0a0f"
      nodeCanvasObject={nodeCanvasObject}
      nodeCanvasObjectMode={() => "replace"}
      linkCanvasObject={linkCanvasObject}
      linkCanvasObjectMode={() => "replace"}
      nodePointerAreaPaint={nodePointerAreaPaint}
      onNodeClick={(node: any) => onNodeClick(node.id)}
      enableZoomInteraction
      enablePanInteraction
      enableNodeDrag={false}
      cooldownTicks={0}
    />
  );
}
