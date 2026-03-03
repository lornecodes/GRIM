"use client";

import { useRef, useEffect, useState } from "react";
import { useMemoryGraph } from "@/hooks/useMemoryGraph";
import { KnowledgeGraph } from "./KnowledgeGraph";

export function MemoryKnowledgeGraph() {
  const mg = useMemoryGraph();
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });

  // Measure container
  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver(([entry]) => {
      setDimensions({
        width: entry.contentRect.width,
        height: Math.max(300, entry.contentRect.height),
      });
    });
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  const selectedNodeData = mg.selectedNode && mg.graphData
    ? mg.graphData.nodes[mg.selectedNode]
    : null;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={mg.refresh}
          className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
          disabled={mg.isLoading}
        >
          {mg.isLoading ? "Loading..." : "Refresh"}
        </button>

        {/* Section filter */}
        {mg.sections.length > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-grim-text-dim">Section:</span>
            <select
              value={mg.filters.section || ""}
              onChange={(e) => mg.filters.setSection(e.target.value || null)}
              className="text-[10px] px-2 py-1 rounded bg-grim-bg border border-grim-border text-grim-text outline-none"
            >
              <option value="">All sections</option>
              {mg.sections.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        )}

        <span className="ml-auto text-[10px] text-grim-text-dim tabular-nums">
          {mg.graphData?.count || 0} FDOs referenced
        </span>
      </div>

      {/* Graph + Inspector split */}
      <div className="flex gap-4">
        {/* Graph area */}
        <div
          ref={containerRef}
          className="flex-1 bg-grim-bg border border-grim-border rounded-lg overflow-hidden"
          style={{ minHeight: 400 }}
        >
          {mg.graphData && mg.graphData.count > 0 ? (
            <KnowledgeGraph
              data={mg.graphData}
              width={dimensions.width}
              height={dimensions.height}
              onNodeClick={mg.setSelectedNode}
              highlightNodeId={mg.selectedNode}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
              {mg.isLoading
                ? "Loading memory graph..."
                : "No FDO references found in memory. Use [[fdo-id]] wikilinks in memory.md to build the graph."}
            </div>
          )}
        </div>

        {/* Inspector panel */}
        {selectedNodeData && (
          <div className="w-64 shrink-0 bg-grim-surface border border-grim-border rounded-lg p-3 max-h-[500px] overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-grim-text">
                {selectedNodeData.title}
              </span>
              <button
                onClick={() => mg.setSelectedNode(null)}
                className="text-grim-text-dim hover:text-grim-text text-xs"
              >
                close
              </button>
            </div>

            <div className="space-y-2">
              <div className="text-[10px] text-grim-text-dim">
                <span className="uppercase tracking-wider">FDO ID</span>
                <div className="text-[11px] text-grim-text font-mono mt-0.5">
                  {selectedNodeData.id}
                </div>
              </div>

              {selectedNodeData.tags && selectedNodeData.tags.length > 0 && (
                <div className="text-[10px] text-grim-text-dim">
                  <span className="uppercase tracking-wider">Referenced in</span>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {selectedNodeData.tags.map((section) => (
                      <span
                        key={section}
                        className="text-[9px] px-1.5 py-0.5 rounded bg-grim-accent/10 text-grim-accent cursor-pointer hover:bg-grim-accent/20 transition-colors"
                        onClick={() => mg.filters.setSection(section)}
                      >
                        {section}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="text-[10px] text-grim-text-dim">
                <span className="uppercase tracking-wider">Confidence</span>
                <div className="text-[11px] text-grim-text mt-0.5">
                  {selectedNodeData.confidence.toFixed(1)}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
