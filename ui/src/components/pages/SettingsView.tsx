"use client";

import { useState, useCallback } from "react";
import { IconSettings } from "@/components/icons/NavIcons";
import { DashboardTile } from "@/components/ui/DashboardTile";
import { useGrimConfig, type GrimConfigData } from "@/hooks/useGrimConfig";

// ---------------------------------------------------------------------------
// Editable config row — click to edit, enter to save
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

// ---------------------------------------------------------------------------
// Settings View
// ---------------------------------------------------------------------------

export function SettingsView() {
  const { config, loading, error, saving, saveConfig, refresh } = useGrimConfig();
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

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

      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">
          Loading configuration...
        </div>
      )}

      {error && (
        <div className="text-xs text-red-400 py-8 text-center">
          {error}
        </div>
      )}

      {config && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* General */}
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

          {/* Model Routing */}
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

          {/* Context Window */}
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

          {/* Identity */}
          <DashboardTile title="Identity">
            <ConfigRow label="System Prompt" value={config.identity.system_prompt_path.split("/").pop()} />
            <ConfigRow label="Personality" value={config.identity.personality_path.split("/").pop()} />
            <ConfigRow label="Personality Cache" value={config.identity.personality_cache_path.split("/").pop()} />
            <ConfigRow label="Skills Directory" value={config.identity.skills_path.split("/").pop()} />
          </DashboardTile>

          {/* Skills */}
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

          {/* Persistence */}
          <DashboardTile title="Persistence">
            <ConfigRow label="Backend" value={config.persistence.checkpoint_backend} />
            <ConfigRow label="Checkpoint Path" value={config.persistence.checkpoint_path.split("/").pop()} />
            <ConfigRow label="Redis" value={<BoolPill value={config.redis_url} trueLabel="connected" falseLabel="none" />} />
          </DashboardTile>

          {/* Evolution */}
          <DashboardTile title="Evolution">
            <ConfigRow label="Snapshot Frequency" value={config.evolution.frequency} />
            <ConfigRow label="Directory" value={config.evolution.directory.split("/").pop()} />
          </DashboardTile>

          {/* Limits */}
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
    </div>
  );
}
