"use client";

import { useState, useEffect, useCallback } from "react";
import { IconSettings } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useGrimConfig, type GrimConfigData } from "@/hooks/useGrimConfig";

// ---------------------------------------------------------------------------
// Reusable components
// ---------------------------------------------------------------------------

function ConfigRow({
  label,
  value,
  editValue,
  editing,
  onEdit,
  onChange,
  onSave,
  onCancel,
  type = "text",
}: {
  label: string;
  value: React.ReactNode;
  editValue?: string;
  editing?: boolean;
  onEdit?: () => void;
  onChange?: (v: string) => void;
  onSave?: () => void;
  onCancel?: () => void;
  type?: "text" | "number";
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-grim-border/30 last:border-b-0 group">
      <span className="text-xs text-grim-text-dim">{label}</span>
      <div className="flex items-center gap-1.5">
        {editing ? (
          <>
            <input
              type={type}
              value={editValue ?? ""}
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSave?.();
                if (e.key === "Escape") onCancel?.();
              }}
              className="text-xs font-mono text-grim-text bg-grim-bg border border-grim-accent/30 rounded px-1.5 py-0.5 outline-none w-32 text-right"
              autoFocus
            />
            <button onClick={onSave} className="text-[9px] text-grim-accent hover:underline">
              save
            </button>
            <button onClick={onCancel} className="text-[9px] text-grim-text-dim hover:text-grim-text">
              esc
            </button>
          </>
        ) : (
          <>
            <span className="text-xs font-mono text-grim-text">{value}</span>
            {onEdit && (
              <button
                onClick={onEdit}
                className="text-[9px] text-grim-text-dim opacity-0 group-hover:opacity-100 hover:text-grim-accent transition-opacity"
              >
                edit
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

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

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="py-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-grim-text-dim">{label}</span>
        <span className="text-xs font-mono text-grim-text tabular-nums">
          {value.toFixed(2)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 bg-grim-border rounded-full appearance-none cursor-pointer accent-grim-accent"
      />
      <div className="flex justify-between text-[9px] text-grim-text-dim mt-0.5">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

const TABS = [
  { id: "general", label: "General" },
  { id: "identity", label: "Identity" },
  { id: "routing", label: "Routing" },
  { id: "context", label: "Context" },
  { id: "persistence", label: "Persistence" },
  { id: "evolution", label: "Evolution" },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ---------------------------------------------------------------------------
// Identity types
// ---------------------------------------------------------------------------

interface FieldState {
  coherence: number;
  valence: number;
  uncertainty: number;
}

interface IdentityData {
  field_state: FieldState;
  system_prompt: string;
}

// ---------------------------------------------------------------------------
// Settings View
// ---------------------------------------------------------------------------

export function SettingsView() {
  const { config, loading, error, saving, saveConfig, refresh } = useGrimConfig();
  const [activeTab, setActiveTab] = useState<TabId>("general");
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  // Identity state
  const [identity, setIdentity] = useState<IdentityData | null>(null);
  const [identityLoading, setIdentityLoading] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [promptDraft, setPromptDraft] = useState("");

  const fetchIdentity = useCallback(async () => {
    setIdentityLoading(true);
    try {
      const res = await fetch("/api/identity");
      if (res.ok) {
        const data = await res.json();
        setIdentity(data);
      }
    } catch { /* ignore */ }
    setIdentityLoading(false);
  }, []);

  useEffect(() => {
    if (activeTab === "identity" && !identity) {
      fetchIdentity();
    }
  }, [activeTab, identity, fetchIdentity]);

  const startEdit = useCallback((field: string, currentValue: string | number) => {
    setEditingField(field);
    setEditValue(String(currentValue));
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingField(null);
    setEditValue("");
  }, []);

  const handleSave = useCallback(async (field: string, value: string) => {
    if (!config) return;

    let updates: Partial<GrimConfigData> = {};

    switch (field) {
      case "env":
        updates = { env: value };
        break;
      case "model":
        updates = { model: value };
        break;
      case "temperature":
        updates = { temperature: parseFloat(value) };
        break;
      case "max_tokens":
        updates = { max_tokens: parseInt(value) };
        break;
      case "routing.default_tier":
        updates = { routing: { ...config.routing, default_tier: value } };
        break;
      case "routing.confidence_threshold":
        updates = { routing: { ...config.routing, confidence_threshold: parseFloat(value) } };
        break;
      case "context.max_tokens":
        updates = { context: { ...config.context, max_tokens: parseInt(value) } };
        break;
      case "context.keep_recent":
        updates = { context: { ...config.context, keep_recent: parseInt(value) } };
        break;
      case "objectives_max_active":
        updates = { objectives_max_active: parseInt(value) };
        break;
      default:
        cancelEdit();
        return;
    }

    await saveConfig(updates);
    setEditingField(null);
    setEditValue("");
    setSaveStatus("Saved");
    setTimeout(() => setSaveStatus(null), 2000);
  }, [config, saveConfig, cancelEdit]);

  const toggleBool = useCallback(async (path: string) => {
    if (!config) return;

    let updates: Partial<GrimConfigData> = {};

    switch (path) {
      case "routing.enabled":
        updates = { routing: { ...config.routing, enabled: !config.routing.enabled } };
        break;
      case "routing.classifier_enabled":
        updates = { routing: { ...config.routing, classifier_enabled: !config.routing.classifier_enabled } };
        break;
      case "skills.auto_load":
        updates = { skills: { ...config.skills, auto_load: !config.skills.auto_load } };
        break;
      case "skills.match_per_turn":
        updates = { skills: { ...config.skills, match_per_turn: !config.skills.match_per_turn } };
        break;
      default:
        return;
    }

    await saveConfig(updates);
    setSaveStatus("Saved");
    setTimeout(() => setSaveStatus(null), 2000);
  }, [config, saveConfig]);

  const saveFieldState = useCallback(async (fs: FieldState) => {
    try {
      const res = await fetch("/api/identity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field_state: fs }),
      });
      if (res.ok) {
        const data = await res.json();
        setIdentity(data);
        setSaveStatus("Saved");
        setTimeout(() => setSaveStatus(null), 2000);
      }
    } catch { /* ignore */ }
  }, []);

  const saveSystemPrompt = useCallback(async () => {
    try {
      const res = await fetch("/api/identity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: promptDraft }),
      });
      if (res.ok) {
        const data = await res.json();
        setIdentity(data);
        setEditingPrompt(false);
        setSaveStatus("Saved");
        setTimeout(() => setSaveStatus(null), 2000);
      }
    } catch { /* ignore */ }
  }, [promptDraft]);

  const isEditing = (field: string) => editingField === field;

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconSettings size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">Settings</h2>
          <p className="text-xs text-grim-text-dim">
            Runtime configuration
            {saving && <span className="text-grim-accent ml-2 animate-pulse">saving...</span>}
            {saveStatus && <span className="text-grim-success ml-2">{saveStatus}</span>}
          </p>
        </div>
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-grim-border/30 pb-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-xs rounded-t transition-colors ${
              activeTab === tab.id
                ? "bg-grim-surface text-grim-accent border border-grim-border border-b-0 -mb-px"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">
          Loading configuration...
        </div>
      )}

      {error && (
        <div className="text-xs text-red-400 py-8 text-center">{error}</div>
      )}

      {config && (
        <>
          {/* General Tab */}
          {activeTab === "general" && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <DashboardTile title="General">
                <ConfigRow
                  label="Environment"
                  value={
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      config.env === "production"
                        ? "bg-grim-accent/15 text-grim-accent"
                        : "bg-grim-warning/15 text-grim-warning"
                    }`}>
                      {config.env}
                    </span>
                  }
                  editing={isEditing("env")}
                  editValue={editValue}
                  onEdit={() => startEdit("env", config.env)}
                  onChange={setEditValue}
                  onSave={() => handleSave("env", editValue)}
                  onCancel={cancelEdit}
                />
                <ConfigRow label="Vault Path" value={config.vault_path} />
                <ConfigRow
                  label="Default Model"
                  value={config.model}
                  editing={isEditing("model")}
                  editValue={editValue}
                  onEdit={() => startEdit("model", config.model)}
                  onChange={setEditValue}
                  onSave={() => handleSave("model", editValue)}
                  onCancel={cancelEdit}
                />
                <ConfigRow
                  label="Temperature"
                  value={config.temperature}
                  editing={isEditing("temperature")}
                  editValue={editValue}
                  onEdit={() => startEdit("temperature", config.temperature)}
                  onChange={setEditValue}
                  onSave={() => handleSave("temperature", editValue)}
                  onCancel={cancelEdit}
                  type="number"
                />
                <ConfigRow
                  label="Max Tokens"
                  value={config.max_tokens.toLocaleString()}
                  editing={isEditing("max_tokens")}
                  editValue={editValue}
                  onEdit={() => startEdit("max_tokens", config.max_tokens)}
                  onChange={setEditValue}
                  onSave={() => handleSave("max_tokens", editValue)}
                  onCancel={cancelEdit}
                  type="number"
                />
              </DashboardTile>

              <DashboardTile title="Skills">
                <ConfigRow
                  label="Auto Load"
                  value={
                    <BoolPill
                      value={config.skills.auto_load}
                      onClick={() => toggleBool("skills.auto_load")}
                    />
                  }
                />
                <ConfigRow
                  label="Match Per Turn"
                  value={
                    <BoolPill
                      value={config.skills.match_per_turn}
                      onClick={() => toggleBool("skills.match_per_turn")}
                    />
                  }
                />
              </DashboardTile>

              <DashboardTile title="Limits">
                <ConfigRow
                  label="Max Active Objectives"
                  value={config.objectives_max_active}
                  editing={isEditing("objectives_max_active")}
                  editValue={editValue}
                  onEdit={() => startEdit("objectives_max_active", config.objectives_max_active)}
                  onChange={setEditValue}
                  onSave={() => handleSave("objectives_max_active", editValue)}
                  onCancel={cancelEdit}
                  type="number"
                />
              </DashboardTile>
            </div>
          )}

          {/* Identity Tab */}
          {activeTab === "identity" && (
            <div className="space-y-4">
              {identityLoading && (
                <div className="text-xs text-grim-text-dim py-4 text-center">
                  Loading identity...
                </div>
              )}

              {identity && (
                <>
                  <DashboardTile title="Personality Field State">
                    <Slider
                      label="Coherence"
                      value={identity.field_state.coherence}
                      min={0}
                      max={1}
                      step={0.05}
                      onChange={(v) => {
                        const fs = { ...identity.field_state, coherence: v };
                        setIdentity({ ...identity, field_state: fs });
                        saveFieldState(fs);
                      }}
                    />
                    <Slider
                      label="Valence"
                      value={identity.field_state.valence}
                      min={-1}
                      max={1}
                      step={0.05}
                      onChange={(v) => {
                        const fs = { ...identity.field_state, valence: v };
                        setIdentity({ ...identity, field_state: fs });
                        saveFieldState(fs);
                      }}
                    />
                    <Slider
                      label="Uncertainty"
                      value={identity.field_state.uncertainty}
                      min={0}
                      max={1}
                      step={0.05}
                      onChange={(v) => {
                        const fs = { ...identity.field_state, uncertainty: v };
                        setIdentity({ ...identity, field_state: fs });
                        saveFieldState(fs);
                      }}
                    />

                    <div className="mt-3 pt-3 border-t border-grim-border/30">
                      <p className="text-[10px] text-grim-text-dim leading-relaxed">
                        <strong>Coherence:</strong> How focused/structured responses are (0 = scattered, 1 = laser)<br />
                        <strong>Valence:</strong> Emotional tone (-1 = critical, 0 = neutral, 1 = enthusiastic)<br />
                        <strong>Uncertainty:</strong> Epistemic caution (0 = confident, 1 = very uncertain)
                      </p>
                    </div>
                  </DashboardTile>

                  <DashboardTile
                    title="System Prompt"
                    headerRight={
                      editingPrompt ? (
                        <div className="flex gap-1.5">
                          <button
                            onClick={saveSystemPrompt}
                            className="text-[10px] px-2 py-0.5 rounded bg-grim-accent/15 text-grim-accent hover:bg-grim-accent/25"
                          >
                            save
                          </button>
                          <button
                            onClick={() => setEditingPrompt(false)}
                            className="text-[10px] px-2 py-0.5 rounded text-grim-text-dim hover:text-grim-text"
                          >
                            cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setPromptDraft(identity.system_prompt);
                            setEditingPrompt(true);
                          }}
                          className="text-[10px] px-2 py-0.5 rounded text-grim-text-dim hover:text-grim-accent"
                        >
                          edit
                        </button>
                      )
                    }
                  >
                    {editingPrompt ? (
                      <textarea
                        value={promptDraft}
                        onChange={(e) => setPromptDraft(e.target.value)}
                        className="w-full h-80 bg-grim-bg border border-grim-border rounded p-3 text-xs font-mono text-grim-text outline-none resize-y"
                      />
                    ) : (
                      <pre className="text-[11px] font-mono text-grim-text whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
                        {identity.system_prompt || "No system prompt loaded"}
                      </pre>
                    )}
                  </DashboardTile>

                  <DashboardTile title="Identity Paths">
                    <ConfigRow label="System Prompt" value={config.identity.system_prompt_path.split("/").pop()} />
                    <ConfigRow label="Personality" value={config.identity.personality_path.split("/").pop()} />
                    <ConfigRow label="Personality Cache" value={config.identity.personality_cache_path.split("/").pop()} />
                    <ConfigRow label="Skills Directory" value={config.identity.skills_path.split("/").pop()} />
                  </DashboardTile>
                </>
              )}
            </div>
          )}

          {/* Routing Tab */}
          {activeTab === "routing" && (
            <DashboardTile title="Model Routing">
              <ConfigRow
                label="Enabled"
                value={
                  <BoolPill
                    value={config.routing.enabled}
                    onClick={() => toggleBool("routing.enabled")}
                  />
                }
              />
              <ConfigRow
                label="Default Tier"
                value={config.routing.default_tier}
                editing={isEditing("routing.default_tier")}
                editValue={editValue}
                onEdit={() => startEdit("routing.default_tier", config.routing.default_tier)}
                onChange={setEditValue}
                onSave={() => handleSave("routing.default_tier", editValue)}
                onCancel={cancelEdit}
              />
              <ConfigRow
                label="LLM Classifier"
                value={
                  <BoolPill
                    value={config.routing.classifier_enabled}
                    onClick={() => toggleBool("routing.classifier_enabled")}
                  />
                }
              />
              <ConfigRow
                label="Confidence Threshold"
                value={config.routing.confidence_threshold}
                editing={isEditing("routing.confidence_threshold")}
                editValue={editValue}
                onEdit={() => startEdit("routing.confidence_threshold", config.routing.confidence_threshold)}
                onChange={setEditValue}
                onSave={() => handleSave("routing.confidence_threshold", editValue)}
                onCancel={cancelEdit}
                type="number"
              />
            </DashboardTile>
          )}

          {/* Context Tab */}
          {activeTab === "context" && (
            <DashboardTile title="Context Window">
              <ConfigRow
                label="Max Tokens"
                value={config.context.max_tokens.toLocaleString()}
                editing={isEditing("context.max_tokens")}
                editValue={editValue}
                onEdit={() => startEdit("context.max_tokens", config.context.max_tokens)}
                onChange={setEditValue}
                onSave={() => handleSave("context.max_tokens", editValue)}
                onCancel={cancelEdit}
                type="number"
              />
              <ConfigRow
                label="Keep Recent Messages"
                value={config.context.keep_recent}
                editing={isEditing("context.keep_recent")}
                editValue={editValue}
                onEdit={() => startEdit("context.keep_recent", config.context.keep_recent)}
                onChange={setEditValue}
                onSave={() => handleSave("context.keep_recent", editValue)}
                onCancel={cancelEdit}
                type="number"
              />
            </DashboardTile>
          )}

          {/* Persistence Tab */}
          {activeTab === "persistence" && (
            <DashboardTile title="Persistence">
              <ConfigRow label="Backend" value={config.persistence.checkpoint_backend} />
              <ConfigRow label="Checkpoint Path" value={config.persistence.checkpoint_path.split("/").pop()} />
              <ConfigRow label="Redis" value={<BoolPill value={config.redis_url} trueLabel="connected" falseLabel="none" />} />
            </DashboardTile>
          )}

          {/* Evolution Tab */}
          {activeTab === "evolution" && (
            <DashboardTile title="Evolution">
              <ConfigRow label="Snapshot Frequency" value={config.evolution.frequency} />
              <ConfigRow label="Directory" value={config.evolution.directory.split("/").pop()} />
            </DashboardTile>
          )}
        </>
      )}
    </div>
  );
}
