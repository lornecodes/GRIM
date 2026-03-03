"use client";

import { useState } from "react";
import type { TopologyNode } from "@/lib/graphTopology";
import type { GraphOverlay } from "@/hooks/useGraphOverlay";

interface NodeInspectorProps {
  node: TopologyNode;
  overlay: GraphOverlay;
  toggling: boolean;
  onToggle: () => void;
  onClose: () => void;
}

/** Collapsible section wrapper. */
function Section({
  title,
  defaultOpen = false,
  badge,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  badge?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[9px] text-grim-text-dim hover:text-grim-text transition-colors w-full"
      >
        <span>{open ? "\u25be" : "\u25b8"}</span>
        <span className="uppercase tracking-wider">{title}</span>
        {badge && (
          <span className="ml-auto text-[8px] px-1 py-0.5 rounded bg-grim-border/30 text-grim-text-dim font-mono">
            {badge}
          </span>
        )}
      </button>
      {open && <div className="mt-1.5">{children}</div>}
    </div>
  );
}

export function NodeInspector({
  node,
  overlay,
  toggling,
  onToggle,
  onClose,
}: NodeInspectorProps) {
  const nodeOverlay = overlay.nodes[node.id];
  const isActive = nodeOverlay?.active ?? false;
  const isCompleted = nodeOverlay?.completed ?? false;

  const hasProtocol = (node.default_protocol ?? "").trim().length > 0;
  const hasSkills = (node.protocol_priority ?? []).length > 0;
  const hasSignals = node.signals && Object.keys(node.signals).length > 0;
  const hasTools = node.tools.length > 0;
  const hasConfig =
    node.temperature != null ||
    node.max_tool_steps != null ||
    node.model != null;

  // Label for the protocol section
  const protocolLabel =
    node.node_type === "companion"
      ? "Mode Preamble"
      : node.id === "audit"
        ? "System Preamble"
        : "Default Protocol";

  return (
    <div className="h-full bg-grim-surface border border-grim-border rounded-xl p-4 flex flex-col gap-3 overflow-y-auto animate-fade-in">
      {/* Header */}
      <div className="flex items-start gap-2">
        <div
          className="w-3 h-3 rounded-full mt-1 flex-shrink-0"
          style={{ backgroundColor: node.color }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-grim-text">
              {node.name}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim">
              {node.node_type}
            </span>
            {node.tier === "ironclaw" && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-orange-400/15 text-orange-400 font-mono">
                claw
              </span>
            )}
          </div>
          <p className="text-[10px] text-grim-text-dim mt-0.5">{node.role}</p>
        </div>
        <button
          onClick={onClose}
          className="text-grim-text-dim hover:text-grim-text text-xs flex-shrink-0 px-1"
        >
          x
        </button>
      </div>

      {/* Execution state */}
      <div className="bg-grim-bg border border-grim-border/50 rounded-lg px-3 py-2">
        <div className="text-[9px] text-grim-text-dim uppercase tracking-wider mb-1">
          Execution State
        </div>
        {isActive ? (
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-trace-node animate-pulse" />
            <span className="text-xs text-trace-node">Active</span>
          </div>
        ) : isCompleted ? (
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-grim-success" />
              <span className="text-xs text-grim-success">Completed</span>
            </div>
            {(nodeOverlay?.durationMs ?? 0) > 0 && (
              <span className="text-[10px] text-grim-text-dim font-mono tabular-nums ml-auto">
                {nodeOverlay.durationMs}ms
              </span>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-grim-text-dim/30" />
            <span className="text-xs text-grim-text-dim">Idle</span>
          </div>
        )}
      </div>

      {/* Overview — rich detail or fallback to description */}
      <Section title="Overview" defaultOpen>
        <p className="text-[11px] text-grim-text leading-relaxed whitespace-pre-line">
          {node.detail || node.description}
        </p>
      </Section>

      {/* Protocol / Preamble */}
      {hasProtocol && (
        <Section title={protocolLabel}>
          <div className="max-h-48 overflow-y-auto bg-grim-bg border border-grim-border/50 rounded-lg p-2">
            <pre className="text-[10px] text-grim-text-dim font-mono whitespace-pre-wrap leading-relaxed">
              {node.default_protocol}
            </pre>
          </div>
        </Section>
      )}

      {/* Skill Protocols */}
      {hasSkills && (
        <Section
          title="Skill Protocols"
          badge={`${node.protocol_priority!.length}`}
        >
          <div className="flex flex-col gap-1">
            {node.protocol_priority!.map((skill, i) => (
              <div
                key={skill}
                className="flex items-center gap-2 text-[10px]"
              >
                <span className="text-grim-text-dim font-mono w-4 text-right">
                  {i + 1}.
                </span>
                <span className="px-1.5 py-0.5 rounded bg-grim-accent/15 text-grim-accent font-mono">
                  {skill}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Routing Signals */}
      {hasSignals && (
        <Section title="Routing Signals">
          <div className="space-y-2">
            {Object.entries(node.signals!).map(([category, keywords]) => (
              <div key={category}>
                <div className="text-[9px] text-grim-accent font-mono mb-1">
                  {category}
                </div>
                <div className="flex flex-wrap gap-1">
                  {keywords.map((kw) => (
                    <span
                      key={kw}
                      className="text-[8px] px-1 py-0.5 rounded bg-grim-border/30 text-grim-text-dim"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Routing rules */}
      {node.routing_rules && node.routing_rules.length > 0 && (
        <Section title="Routing Rules" defaultOpen>
          <div className="space-y-1">
            {node.routing_rules.map((rule, i) => (
              <div
                key={i}
                className="bg-grim-bg border border-grim-border/50 rounded px-2 py-1.5"
              >
                <div className="text-[9px] text-grim-text-dim">
                  {rule.condition}
                </div>
                <div className="text-[10px] text-grim-accent font-mono">
                  &rarr; {rule.target}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Tools with descriptions */}
      {hasTools && (
        <Section title="Tools" badge={`${node.tools.length}`}>
          <div className="space-y-1">
            {(node.tools_detail ?? node.tools.map((t) => ({ name: t, description: "" }))).map(
              (tool) => (
                <div
                  key={tool.name}
                  className="bg-grim-bg border border-grim-border/50 rounded px-2 py-1.5"
                >
                  <div className="text-[10px] text-grim-text font-mono">
                    {tool.name}
                  </div>
                  {tool.description && (
                    <div className="text-[9px] text-grim-text-dim mt-0.5 leading-relaxed">
                      {tool.description}
                    </div>
                  )}
                </div>
              )
            )}
          </div>
        </Section>
      )}

      {/* Config */}
      {hasConfig && (
        <Section title="Config">
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
            {node.model != null && (
              <>
                <span className="text-[9px] text-grim-text-dim">model</span>
                <span className="text-[10px] text-grim-text font-mono truncate">
                  {node.model}
                </span>
              </>
            )}
            {node.temperature != null && (
              <>
                <span className="text-[9px] text-grim-text-dim">temperature</span>
                <span className="text-[10px] text-grim-text font-mono">
                  {node.temperature}
                </span>
              </>
            )}
            {node.max_tool_steps != null && (
              <>
                <span className="text-[9px] text-grim-text-dim">max tool steps</span>
                <span className="text-[10px] text-grim-text font-mono">
                  {node.max_tool_steps}
                </span>
              </>
            )}
          </div>
        </Section>
      )}

      {/* Agent toggle */}
      {node.toggleable && (
        <div className="border-t border-grim-border pt-3 mt-auto">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[10px] text-grim-text">Enable Agent</div>
              <div className="text-[9px] text-grim-text-dim">
                {node.enabled
                  ? "Active in routing"
                  : "Disabled \u2014 requests skip this agent"}
              </div>
            </div>
            <button
              onClick={onToggle}
              disabled={toggling}
              className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
                node.enabled ? "bg-grim-accent" : "bg-grim-border"
              } ${toggling ? "opacity-50" : "cursor-pointer"}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  node.enabled ? "translate-x-4" : ""
                }`}
              />
            </button>
          </div>
        </div>
      )}

      {/* Always-on badge */}
      {!node.toggleable && (
        <div className="border-t border-grim-border pt-2 mt-auto">
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-success/15 text-grim-success">
            always on
          </span>
        </div>
      )}
    </div>
  );
}
