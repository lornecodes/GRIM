"use client";

import { useState, useEffect } from "react";
import { IconEval } from "@/components/icons/NavIcons";
import { useEval } from "@/hooks/useEval";
import { EvalRunTab } from "@/components/eval/EvalRunTab";
import { EvalResultsTab } from "@/components/eval/EvalResultsTab";
import { EvalHistoryTab } from "@/components/eval/EvalHistoryTab";
import { EvalDatasetsTab } from "@/components/eval/EvalDatasetsTab";

const TABS = [
  { id: "run", label: "Run" },
  { id: "results", label: "Results" },
  { id: "history", label: "History" },
  { id: "datasets", label: "Datasets" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function EvalDashboard() {
  const [activeTab, setActiveTab] = useState<TabId>("run");
  const eval_ = useEval();

  // Load initial data
  useEffect(() => {
    eval_.fetchRuns();
    eval_.fetchDatasets();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="max-w-6xl mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconEval size={28} className="text-grim-accent" />
        <h2 className="text-lg font-semibold">Evaluation</h2>
        {eval_.runStatus === "running" && (
          <span className="ml-2 px-2 py-0.5 text-[10px] font-medium rounded-full bg-amber-500/20 text-amber-400 animate-pulse">
            RUNNING
          </span>
        )}
        {eval_.runStatus === "completed" && (
          <span className="ml-2 px-2 py-0.5 text-[10px] font-medium rounded-full bg-emerald-500/20 text-emerald-400">
            COMPLETE
          </span>
        )}
        {eval_.runStatus === "failed" && (
          <span className="ml-2 px-2 py-0.5 text-[10px] font-medium rounded-full bg-red-500/20 text-red-400">
            FAILED
          </span>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-grim-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? "border-b-2 border-grim-accent text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Error banner */}
      {eval_.error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-xs text-red-400 flex items-center justify-between">
          <span>{eval_.error}</span>
          <button
            onClick={() => eval_.setError(null)}
            className="ml-2 text-red-400 hover:text-red-300"
          >
            dismiss
          </button>
        </div>
      )}

      {/* Tab content */}
      {activeTab === "run" && <EvalRunTab eval_={eval_} />}
      {activeTab === "results" && <EvalResultsTab eval_={eval_} />}
      {activeTab === "history" && <EvalHistoryTab eval_={eval_} />}
      {activeTab === "datasets" && <EvalDatasetsTab eval_={eval_} />}
    </div>
  );
}
