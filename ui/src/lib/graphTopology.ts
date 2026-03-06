/**
 * Graph topology types and layout utilities for Graph Studio.
 *
 * The actual node/edge data comes from GET /api/graph/topology.
 * This module defines the TypeScript types and the coordinate helper
 * that converts logical (col, row) positions into canvas pixels.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type NodeType =
  | "preprocessing"
  | "routing"
  | "companion"
  | "agent"
  | "postprocessing"
  | "infra";

export type EdgeType = "static" | "conditional";

export interface RoutingRule {
  condition: string;
  target: string;
}

export interface ToolDetail {
  name: string;
  description: string;
}

export interface TopologyNode {
  id: string;
  name: string;
  role: string;
  description: string;
  tools: string[];
  tools_detail?: ToolDetail[];
  color: string;
  tier: string;
  toggleable: boolean;
  node_type: NodeType;
  col: number;
  row: number;
  enabled: boolean;
  routing_rules?: RoutingRule[];
  protocol_priority?: string[];
  default_protocol?: string;
  temperature?: number;
  max_tool_steps?: number;
  model?: string;
  detail?: string;
  signals?: Record<string, string[]>;
}

export interface TopologyEdge {
  source: string;
  target: string;
  type: EdgeType;
  label?: string;
}

export interface GraphTopology {
  nodes: Record<string, TopologyNode>;
  edges: TopologyEdge[];
  node_count: number;
  edge_count: number;
}

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

export const COL_WIDTH = 130;
export const ROW_HEIGHT = 100;
export const CANVAS_OFFSET_X = 80;
export const CANVAS_OFFSET_Y = 250;

/** Convert logical (col, row) to canvas (x, y) pixel coordinates. */
export function toCanvasPos(col: number, row: number): { x: number; y: number } {
  return {
    x: CANVAS_OFFSET_X + col * COL_WIDTH,
    y: CANVAS_OFFSET_Y + row * ROW_HEIGHT,
  };
}
