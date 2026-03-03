"use client";

import { useState } from "react";
import type { useEval } from "@/hooks/useEval";

interface Props {
  eval_: ReturnType<typeof useEval>;
}

export function EvalRunTab({ eval_ }: Props) {
  const { runStatus, progress, activeResult, datasets, startRun } = eval_;
  const isRunning = runStatus === "running";

  // Category filter state
  const [selectedCategories, setSelectedCategories] = useState<Set<string>>(new Set());
  const [showFilters, setShowFilters] = useState(false);

  const toggleCategory = (cat: string) => {
    setSelectedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const handleRun = (tier: number | string) => {
    const cats = selectedCategories.size > 0 ? Array.from(selectedCategories) : undefined;
    startRun(tier, cats);
  };

  // Extract stats from active result
  const stats = activeResult as { total_cases?: number; total_passed?: number; overall_score?: number; duration_ms?: number; suites?: Array<{ tier: number; category: string; passed: number; total: number; score: number }> } | null;
  const totalCases: number = stats?.total_cases ?? 0;
  const passedCases: number = stats?.total_passed ?? 0;
  const overallScore: number = stats?.overall_score ?? 0;
  const durationMs: number = stats?.duration_ms ?? 0;

  const tier1Datasets = datasets.filter((d) => d.tier === 1);
  const tier2Datasets = datasets.filter((d) => d.tier === 2);

  return (
    <div className="space-y-6">
      {/* Run controls */}
      <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
        <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
          Run Evaluation
        </div>
        <div className="flex gap-3 flex-wrap items-center">
          <button
            onClick={() => handleRun(1)}
            disabled={isRunning}
            className="px-4 py-2 text-xs font-medium rounded-lg bg-grim-accent/20 text-grim-accent hover:bg-grim-accent/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Tier 1 — Structural
          </button>
          <button
            onClick={() => handleRun(2)}
            disabled={isRunning}
            className="px-4 py-2 text-xs font-medium rounded-lg bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Tier 2 — LLM-Graded
          </button>
          <button
            onClick={() => handleRun("all")}
            disabled={isRunning}
            className="px-4 py-2 text-xs font-medium rounded-lg bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            All Tiers
          </button>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`px-3 py-2 text-xs rounded-lg transition-colors ${
              showFilters || selectedCategories.size > 0
                ? "bg-amber-500/20 text-amber-400"
                : "text-grim-text-dim hover:text-grim-text hover:bg-grim-bg/50"
            }`}
          >
            Filter{selectedCategories.size > 0 ? ` (${selectedCategories.size})` : ""}
          </button>
        </div>

        {/* Category filters */}
        {showFilters && (
          <div className="mt-3 pt-3 border-t border-grim-border">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] text-grim-text-dim uppercase tracking-wider">
                Categories
              </span>
              {selectedCategories.size > 0 && (
                <button
                  onClick={() => setSelectedCategories(new Set())}
                  className="text-[10px] text-grim-text-dim hover:text-grim-text transition-colors"
                >
                  clear all
                </button>
              )}
            </div>
            {tier1Datasets.length > 0 && (
              <div className="mb-2">
                <div className="text-[9px] text-grim-text-dim font-medium mb-1">Tier 1</div>
                <div className="flex gap-2 flex-wrap">
                  {tier1Datasets.map((d) => (
                    <label key={d.category} className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedCategories.has(d.category)}
                        onChange={() => toggleCategory(d.category)}
                        className="rounded border-grim-border bg-grim-bg text-grim-accent focus:ring-grim-accent/30 w-3 h-3"
                      />
                      <span className={`text-[11px] ${selectedCategories.has(d.category) ? "text-grim-text" : "text-grim-text-dim"}`}>
                        {d.category} ({d.case_count})
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {tier2Datasets.length > 0 && (
              <div>
                <div className="text-[9px] text-grim-text-dim font-medium mb-1">Tier 2</div>
                <div className="flex gap-2 flex-wrap">
                  {tier2Datasets.map((d) => (
                    <label key={d.category} className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedCategories.has(d.category)}
                        onChange={() => toggleCategory(d.category)}
                        className="rounded border-grim-border bg-grim-bg text-grim-accent focus:ring-grim-accent/30 w-3 h-3"
                      />
                      <span className={`text-[11px] ${selectedCategories.has(d.category) ? "text-grim-text" : "text-grim-text-dim"}`}>
                        {d.category} ({d.case_count})
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Score cards */}
      {(totalCases > 0 || isRunning) ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <ScoreCard
            label="Overall Score"
            value={overallScore > 0 ? `${(overallScore * 100).toFixed(1)}%` : "—"}
            color={overallScore >= 0.9 ? "text-emerald-400" : overallScore >= 0.7 ? "text-amber-400" : "text-red-400"}
          />
          <ScoreCard
            label="Pass Rate"
            value={totalCases > 0 ? `${passedCases}/${totalCases}` : "—"}
            color="text-grim-accent"
          />
          <ScoreCard
            label="Total Cases"
            value={totalCases > 0 ? `${totalCases}` : "—"}
            color="text-grim-text"
          />
          <ScoreCard
            label="Duration"
            value={durationMs > 0 ? `${(durationMs / 1000).toFixed(1)}s` : "—"}
            color="text-grim-text-dim"
          />
        </div>
      ) : null}

      {/* Live progress */}
      {(isRunning || progress.length > 0) && (
        <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
          <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
            {isRunning ? "Running..." : "Last Run Progress"}
          </div>
          <div className="space-y-2">
            {progress.map((evt, i) => (
              <div
                key={i}
                className="flex items-center gap-3 text-xs"
              >
                {evt.type === "suite_start" && (
                  <>
                    <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                    <span className="text-grim-text-dim">
                      Tier {evt.tier}
                    </span>
                    <span className="text-grim-text">{evt.category}</span>
                    <span className="text-grim-text-dim">
                      ({evt.total} cases)
                    </span>
                  </>
                )}
                {evt.type === "suite_end" && (
                  <>
                    <span
                      className={`w-2 h-2 rounded-full ${
                        evt.score !== undefined && evt.score >= 1.0
                          ? "bg-emerald-400"
                          : evt.score !== undefined && evt.score >= 0.7
                            ? "bg-amber-400"
                            : "bg-red-400"
                      }`}
                    />
                    <span className="text-grim-text-dim">
                      Tier {evt.tier}
                    </span>
                    <span className="text-grim-text">{evt.category}</span>
                    <span className="font-medium">
                      {evt.passed}/{evt.total}
                    </span>
                    {evt.score !== undefined && (
                      <span
                        className={
                          evt.score >= 1.0
                            ? "text-emerald-400"
                            : evt.score >= 0.7
                              ? "text-amber-400"
                              : "text-red-400"
                        }
                      >
                        ({(evt.score * 100).toFixed(1)}%)
                      </span>
                    )}
                  </>
                )}
              </div>
            ))}
            {isRunning && progress.length === 0 && (
              <div className="text-xs text-grim-text-dim animate-pulse">
                Starting evaluation...
              </div>
            )}
          </div>
        </div>
      )}

      {/* Suite breakdown from results */}
      {!isRunning && stats?.suites && stats.suites.length > 0 ? (
        <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
          <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-3">
            Suite Breakdown
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-grim-text-dim border-b border-grim-border">
                <th className="text-left py-2 font-medium">Suite</th>
                <th className="text-left py-2 font-medium">Tier</th>
                <th className="text-right py-2 font-medium">Passed</th>
                <th className="text-right py-2 font-medium">Total</th>
                <th className="text-right py-2 font-medium">Score</th>
              </tr>
            </thead>
            <tbody>
              {stats.suites.map((suite, i) => (
                <tr key={i} className="border-b border-grim-border/50">
                  <td className="py-2 text-grim-text">{suite.category}</td>
                  <td className="py-2 text-grim-text-dim">{suite.tier}</td>
                  <td className="py-2 text-right">{suite.passed}</td>
                  <td className="py-2 text-right text-grim-text-dim">{suite.total}</td>
                  <td className="py-2 text-right">
                    <span
                      className={
                        suite.score >= 1.0
                          ? "text-emerald-400"
                          : suite.score >= 0.7
                            ? "text-amber-400"
                            : "text-red-400"
                      }
                    >
                      {(suite.score * 100).toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ScoreCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="bg-grim-surface border border-grim-border rounded-xl p-4">
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
    </div>
  );
}
