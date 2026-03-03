"use client";

import { useState } from "react";
import type { useEval } from "@/hooks/useEval";

interface Props {
  eval_: ReturnType<typeof useEval>;
}

export function EvalDatasetsTab({ eval_ }: Props) {
  const { datasets, datasetContent, fetchDatasetContent, appendCase, updateCase, deleteCase } = eval_;
  const [selectedDataset, setSelectedDataset] = useState<{
    tier: number;
    category: string;
  } | null>(null);
  const [expandedCase, setExpandedCase] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newCaseJson, setNewCaseJson] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  // Edit state
  const [editingCase, setEditingCase] = useState<string | null>(null);
  const [editJson, setEditJson] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  // Delete confirmation
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleSelectDataset = (tier: number, category: string) => {
    setSelectedDataset({ tier, category });
    fetchDatasetContent(tier, category);
    setExpandedCase(null);
    setShowAddForm(false);
    setEditingCase(null);
    setConfirmDelete(null);
  };

  const handleAddCase = async () => {
    if (!selectedDataset) return;
    setAddError(null);
    try {
      const parsed = JSON.parse(newCaseJson);
      const ok = await appendCase(selectedDataset.tier, selectedDataset.category, parsed);
      if (ok) {
        setNewCaseJson("");
        setShowAddForm(false);
        fetchDatasetContent(selectedDataset.tier, selectedDataset.category);
      }
    } catch (e) {
      setAddError((e as Error).message);
    }
  };

  const handleStartEdit = (c: Record<string, unknown>) => {
    const caseId = c.id as string;
    setEditingCase(caseId);
    setEditJson(JSON.stringify(c, null, 2));
    setEditError(null);
    setExpandedCase(caseId);
  };

  const handleSaveEdit = async () => {
    if (!selectedDataset || !editingCase) return;
    setEditError(null);
    try {
      const parsed = JSON.parse(editJson);
      const ok = await updateCase(selectedDataset.tier, selectedDataset.category, editingCase, parsed);
      if (ok) {
        setEditingCase(null);
        setEditJson("");
        fetchDatasetContent(selectedDataset.tier, selectedDataset.category);
      }
    } catch (e) {
      setEditError((e as Error).message);
    }
  };

  const handleDelete = async (caseId: string) => {
    if (!selectedDataset) return;
    const ok = await deleteCase(selectedDataset.tier, selectedDataset.category, caseId);
    if (ok) {
      setConfirmDelete(null);
      setExpandedCase(null);
      fetchDatasetContent(selectedDataset.tier, selectedDataset.category);
    }
  };

  const cases = (datasetContent?.cases as Array<Record<string, unknown>>) || [];

  return (
    <div className="flex gap-4 min-h-[400px]">
      {/* Left: dataset list */}
      <div className="w-64 shrink-0">
        <div className="bg-grim-surface border border-grim-border rounded-xl p-3 space-y-1">
          <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-2">
            Datasets
          </div>

          {/* Tier 1 */}
          <div className="text-[10px] text-grim-text-dim font-medium mt-2 mb-1">Tier 1 — Structural</div>
          {datasets
            .filter((d) => d.tier === 1)
            .map((d) => (
              <DatasetButton
                key={`${d.tier}-${d.category}`}
                label={d.category}
                count={d.case_count}
                active={
                  selectedDataset?.tier === d.tier &&
                  selectedDataset?.category === d.category
                }
                onClick={() => handleSelectDataset(d.tier, d.category)}
              />
            ))}

          {/* Tier 2 */}
          <div className="text-[10px] text-grim-text-dim font-medium mt-3 mb-1">Tier 2 — LLM-Graded</div>
          {datasets
            .filter((d) => d.tier === 2)
            .map((d) => (
              <DatasetButton
                key={`${d.tier}-${d.category}`}
                label={d.category}
                count={d.case_count}
                active={
                  selectedDataset?.tier === d.tier &&
                  selectedDataset?.category === d.category
                }
                onClick={() => handleSelectDataset(d.tier, d.category)}
              />
            ))}
        </div>
      </div>

      {/* Right: case list */}
      <div className="flex-1 min-w-0">
        {!selectedDataset ? (
          <div className="text-xs text-grim-text-dim text-center py-12">
            Select a dataset to view its cases.
          </div>
        ) : (
          <div className="space-y-3">
            {/* Header */}
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-grim-text">
                  {selectedDataset.category}
                </div>
                <div className="text-[10px] text-grim-text-dim">
                  Tier {selectedDataset.tier} — {cases.length} cases
                </div>
              </div>
              <button
                onClick={() => setShowAddForm(!showAddForm)}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-grim-accent/20 text-grim-accent hover:bg-grim-accent/30 transition-colors"
              >
                {showAddForm ? "Cancel" : "+ Add Case"}
              </button>
            </div>

            {/* Add case form */}
            {showAddForm && (
              <div className="bg-grim-surface border border-grim-border rounded-xl p-4 space-y-3">
                <div className="text-[11px] text-grim-text-dim uppercase tracking-wider">
                  Add New Case (JSON)
                </div>
                <textarea
                  value={newCaseJson}
                  onChange={(e) => setNewCaseJson(e.target.value)}
                  placeholder={`{\n  "id": "my-new-case",\n  "tags": ["test"],\n  "message": "...",\n  "expected": { ... }\n}`}
                  className="w-full h-40 bg-grim-bg border border-grim-border rounded-lg p-3 text-xs font-mono text-grim-text resize-none focus:outline-none focus:border-grim-accent"
                />
                {addError && (
                  <div className="text-xs text-red-400">{addError}</div>
                )}
                <button
                  onClick={handleAddCase}
                  className="px-4 py-2 text-xs font-medium rounded-lg bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors"
                >
                  Append Case
                </button>
              </div>
            )}

            {/* Case list */}
            <div className="space-y-1">
              {cases.map((c) => {
                const caseId = c.id as string;
                const isExpanded = expandedCase === caseId;
                const isEditing = editingCase === caseId;
                const isConfirmingDelete = confirmDelete === caseId;
                return (
                  <div
                    key={caseId}
                    className="bg-grim-surface border border-grim-border rounded-lg overflow-hidden"
                  >
                    <div className="flex items-center">
                      <button
                        onClick={() =>
                          setExpandedCase(isExpanded ? null : caseId)
                        }
                        className="flex-1 flex items-center gap-3 px-4 py-2 text-left hover:bg-grim-bg/30 transition-colors"
                      >
                        <span className="text-xs font-mono text-grim-accent">
                          {caseId}
                        </span>
                        {(c.tags as string[])?.slice(0, 3).map((t) => (
                          <span
                            key={t}
                            className="px-1.5 py-0.5 bg-grim-bg text-[9px] text-grim-text-dim rounded"
                          >
                            {t}
                          </span>
                        ))}
                        <span className="ml-auto text-[10px] text-grim-text-dim">
                          {isExpanded ? "−" : "+"}
                        </span>
                      </button>
                      {/* Action buttons */}
                      <div className="flex items-center gap-1 pr-2">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleStartEdit(c); }}
                          className="px-2 py-1 text-[10px] text-grim-text-dim hover:text-grim-accent transition-colors"
                          title="Edit case"
                        >
                          edit
                        </button>
                        {isConfirmingDelete ? (
                          <div className="flex items-center gap-1">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleDelete(caseId); }}
                              className="px-2 py-1 text-[10px] text-red-400 hover:text-red-300 font-medium transition-colors"
                            >
                              confirm
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); setConfirmDelete(null); }}
                              className="px-2 py-1 text-[10px] text-grim-text-dim hover:text-grim-text transition-colors"
                            >
                              cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={(e) => { e.stopPropagation(); setConfirmDelete(caseId); }}
                            className="px-2 py-1 text-[10px] text-grim-text-dim hover:text-red-400 transition-colors"
                            title="Delete case"
                          >
                            del
                          </button>
                        )}
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="px-4 py-3 border-t border-grim-border bg-grim-bg/30">
                        {isEditing ? (
                          <div className="space-y-2">
                            <textarea
                              value={editJson}
                              onChange={(e) => setEditJson(e.target.value)}
                              className="w-full h-60 bg-grim-bg border border-grim-border rounded-lg p-3 text-[11px] font-mono text-grim-text resize-none focus:outline-none focus:border-grim-accent"
                            />
                            {editError && (
                              <div className="text-xs text-red-400">{editError}</div>
                            )}
                            <div className="flex gap-2">
                              <button
                                onClick={handleSaveEdit}
                                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors"
                              >
                                Save
                              </button>
                              <button
                                onClick={() => { setEditingCase(null); setEditJson(""); }}
                                className="px-3 py-1.5 text-xs text-grim-text-dim hover:text-grim-text transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <pre className="text-[11px] font-mono text-grim-text whitespace-pre-wrap overflow-x-auto max-h-60 overflow-y-auto">
                            {JSON.stringify(c, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DatasetButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs transition-colors ${
        active
          ? "bg-grim-accent/20 text-grim-accent"
          : "text-grim-text-dim hover:text-grim-text hover:bg-grim-bg/50"
      }`}
    >
      <span className="truncate">{label}</span>
      <span className="text-[10px] ml-2 shrink-0">{count}</span>
    </button>
  );
}
