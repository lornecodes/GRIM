"use client";

import { IconModels } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useModels, type ModelData } from "@/hooks/useModels";

// ---------------------------------------------------------------------------
// Toggle switch (same pattern as SkillsExplorer)
// ---------------------------------------------------------------------------

function Toggle({
  enabled,
  loading,
  onToggle,
}: {
  enabled: boolean;
  loading: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      disabled={loading}
      className={`relative w-9 h-5 rounded-full transition-colors ${
        enabled ? "bg-grim-accent" : "bg-grim-border"
      } ${loading ? "opacity-50" : "cursor-pointer"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          enabled ? "translate-x-4" : ""
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Model row
// ---------------------------------------------------------------------------

function ModelRow({
  model,
  toggling,
  onToggle,
}: {
  model: ModelData;
  toggling: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={`flex items-center gap-3 py-3 px-4 rounded-lg border transition-all ${
        model.enabled
          ? "bg-grim-surface border-grim-border"
          : "bg-grim-surface/50 border-grim-border/50 opacity-60"
      }`}
    >
      {/* Status dot */}
      <div
        className={`w-2.5 h-2.5 rounded-full ${
          model.enabled ? "bg-grim-success" : "bg-grim-border"
        }`}
      />

      {/* Model info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-grim-text">
            {model.name}
          </span>
          {model.is_default && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-accent/15 text-grim-accent font-medium">
              default
            </span>
          )}
        </div>
        <span className="text-[10px] text-grim-text-dim font-mono">
          {model.id}
        </span>
      </div>

      {/* Stats */}
      <div className="hidden sm:flex items-center gap-4 text-[10px] text-grim-text-dim">
        <span>{(model.context_window / 1000).toFixed(0)}K ctx</span>
        <span>{(model.max_output / 1000).toFixed(0)}K out</span>
      </div>

      {/* Toggle */}
      <Toggle enabled={model.enabled} loading={toggling} onToggle={onToggle} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BoolPill (reused from Settings)
// ---------------------------------------------------------------------------

function BoolPill({
  value,
  trueLabel = "on",
  falseLabel = "off",
  onClick,
}: {
  value: boolean;
  trueLabel?: string;
  falseLabel?: string;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`text-[10px] px-1.5 py-0.5 rounded font-medium transition-colors ${
        value
          ? "bg-grim-success/15 text-grim-success"
          : "bg-grim-border/30 text-grim-text-dim"
      } ${onClick ? "hover:ring-1 hover:ring-grim-accent/30 cursor-pointer" : "cursor-default"}`}
    >
      {value ? trueLabel : falseLabel}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Models View page
// ---------------------------------------------------------------------------

export function ModelsView() {
  const {
    models,
    routing,
    provider,
    loading,
    error,
    toggling,
    toggleModel,
    updateRouting,
    refresh,
  } = useModels();

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconModels size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">Models</h2>
          <p className="text-xs text-grim-text-dim">
            {models.filter((m) => m.enabled).length} enabled /{" "}
            {models.length} total across {provider || "anthropic"}
          </p>
        </div>
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
      </div>

      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">
          Loading models...
        </div>
      )}

      {error && (
        <div className="text-xs text-red-400 py-8 text-center">{error}</div>
      )}

      {!loading && !error && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Model list */}
          <div className="space-y-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] text-grim-text-dim uppercase tracking-wider">
                Anthropic
              </span>
              <span className="text-[9px] text-grim-text-dim font-mono">
                api.anthropic.com
              </span>
            </div>
            {models.map((model) => (
              <ModelRow
                key={model.tier}
                model={model}
                toggling={toggling === model.tier}
                onToggle={() => toggleModel(model.tier)}
              />
            ))}
          </div>

          {/* Routing config */}
          {routing && (
            <DashboardTile title="Model Routing">
              <div className="space-y-2">
                <div className="flex items-center justify-between py-1.5 border-b border-grim-border/30">
                  <span className="text-xs text-grim-text-dim">Enabled</span>
                  <BoolPill
                    value={routing.enabled}
                    onClick={() =>
                      updateRouting({ enabled: !routing.enabled })
                    }
                  />
                </div>
                <div className="flex items-center justify-between py-1.5 border-b border-grim-border/30">
                  <span className="text-xs text-grim-text-dim">
                    Default Tier
                  </span>
                  <span className="text-xs font-mono text-grim-text">
                    {routing.default_tier}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1.5 border-b border-grim-border/30">
                  <span className="text-xs text-grim-text-dim">
                    LLM Classifier
                  </span>
                  <BoolPill
                    value={routing.classifier_enabled}
                    onClick={() =>
                      updateRouting({
                        classifier_enabled: !routing.classifier_enabled,
                      })
                    }
                  />
                </div>
                <div className="flex items-center justify-between py-1.5">
                  <span className="text-xs text-grim-text-dim">
                    Confidence Threshold
                  </span>
                  <span className="text-xs font-mono text-grim-text">
                    {routing.confidence_threshold}
                  </span>
                </div>
              </div>
            </DashboardTile>
          )}
        </div>
      )}
    </div>
  );
}
