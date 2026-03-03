"use client";

import { useState } from "react";
import type { useEval } from "@/hooks/useEval";

interface Props {
  eval_: ReturnType<typeof useEval>;
}

interface CaseResultData {
  case_id: string;
  tier: number;
  category: string;
  passed: boolean;
  score: number;
  duration_ms: number;
  tags: string[];
  checks: Array<{ name: string; expected: unknown; actual: unknown; passed: boolean }>;
  dimensions: Array<{ name: string; score: number; rationale?: string }>;
  tool_trace: string[];
  response_text: string;
  error?: string;
}

interface SuiteData {
  tier: number;
  category: string;
  cases: CaseResultData[];
  passed: number;
  total: number;
  score: number;
}

interface RegressionItem {
  case_id: string;
  category: string;
  base_score: number;
  target_score: number;
  delta: number;
  severity: string;
}

interface ComparisonData {
  base_run_id: string;
  target_run_id: string;
  overall_delta: number;
  has_regressions: boolean;
  regressions: RegressionItem[];
  improvements: RegressionItem[];
  unchanged: number;
}

export function EvalResultsTab({ eval_ }: Props) {
  const { runs, activeResult, fetchResults, compareRuns, loading } = eval_;
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [expandedCase, setExpandedCase] = useState<string | null>(null);
  const [filterTier, setFilterTier] = useState<number | null>(null);
  const [filterStatus, setFilterStatus] = useState<"all" | "pass" | "fail">("all");

  // Compare state
  const [compareBaseId, setCompareBaseId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<ComparisonData | null>(null);
  const [comparing, setComparing] = useState(false);
  const [showCompare, setShowCompare] = useState(false);

  const handleSelectRun = (runId: string) => {
    setSelectedRunId(runId);
    fetchResults(runId);
  };

  const handleCompare = async () => {
    if (!selectedRunId || !compareBaseId) return;
    setComparing(true);
    const data = await compareRuns(compareBaseId, selectedRunId);
    setComparison(data);
    setComparing(false);
  };

  const result = activeResult as Record<string, unknown> | null;
  const suites = (result?.suites as SuiteData[] | undefined) || [];

  const allCases = suites.flatMap((s) =>
    s.cases.map((c) => ({ ...c, _suite: s.category, _tier: s.tier }))
  );

  const filtered = allCases.filter((c) => {
    if (filterTier && c._tier !== filterTier) return false;
    if (filterStatus === "pass" && !c.passed) return false;
    if (filterStatus === "fail" && c.passed) return false;
    return true;
  });

  return (
    <div className="space-y-4">
      {/* Run selector + filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={selectedRunId || ""}
          onChange={(e) => e.target.value && handleSelectRun(e.target.value)}
          className="bg-grim-surface border border-grim-border rounded-lg px-3 py-1.5 text-xs text-grim-text"
        >
          <option value="">Select a run...</option>
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id} — {new Date(r.timestamp).toLocaleString()} ({r.total_cases} cases,{" "}
              {(r.overall_score * 100).toFixed(0)}%)
            </option>
          ))}
        </select>

        {result && (
          <>
            <select
              value={filterTier ?? ""}
              onChange={(e) => setFilterTier(e.target.value ? Number(e.target.value) : null)}
              className="bg-grim-surface border border-grim-border rounded-lg px-3 py-1.5 text-xs text-grim-text"
            >
              <option value="">All tiers</option>
              <option value="1">Tier 1</option>
              <option value="2">Tier 2</option>
            </select>

            <div className="flex gap-1">
              {(["all", "pass", "fail"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setFilterStatus(s)}
                  className={`px-3 py-1 text-[10px] font-medium rounded-lg transition-colors ${
                    filterStatus === s
                      ? "bg-grim-accent/20 text-grim-accent"
                      : "text-grim-text-dim hover:text-grim-text"
                  }`}
                >
                  {s.toUpperCase()}
                </button>
              ))}
            </div>

            <button
              onClick={() => setShowCompare(!showCompare)}
              className={`px-3 py-1 text-[10px] font-medium rounded-lg transition-colors ${
                showCompare
                  ? "bg-purple-500/20 text-purple-400"
                  : "text-grim-text-dim hover:text-grim-text"
              }`}
            >
              Compare
            </button>

            <span className="text-[10px] text-grim-text-dim ml-auto">
              {filtered.length} of {allCases.length} cases
            </span>
          </>
        )}
      </div>

      {/* Compare panel */}
      {showCompare && selectedRunId && (
        <div className="bg-grim-surface border border-grim-border rounded-xl p-4 space-y-3">
          <div className="text-[11px] text-grim-text-dim uppercase tracking-wider">
            Compare Against Baseline
          </div>
          <div className="flex items-center gap-3">
            <select
              value={compareBaseId || ""}
              onChange={(e) => {
                setCompareBaseId(e.target.value || null);
                setComparison(null);
              }}
              className="bg-grim-bg border border-grim-border rounded-lg px-3 py-1.5 text-xs text-grim-text"
            >
              <option value="">Select baseline run...</option>
              {runs
                .filter((r) => r.run_id !== selectedRunId)
                .map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id} — {new Date(r.timestamp).toLocaleString()} ({(r.overall_score * 100).toFixed(0)}%)
                  </option>
                ))}
            </select>
            <button
              onClick={handleCompare}
              disabled={!compareBaseId || comparing}
              className="px-4 py-1.5 text-xs font-medium rounded-lg bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {comparing ? "Comparing..." : "Compare"}
            </button>
          </div>

          {/* Comparison results */}
          {comparison && (
            <div className="space-y-3">
              {/* Summary bar */}
              <div className="flex items-center gap-4 text-xs">
                <span className={`font-medium ${comparison.overall_delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  Overall: {comparison.overall_delta >= 0 ? "+" : ""}{(comparison.overall_delta * 100).toFixed(1)}%
                </span>
                {comparison.regressions.length > 0 && (
                  <span className="text-red-400">
                    {comparison.regressions.length} regression{comparison.regressions.length !== 1 ? "s" : ""}
                  </span>
                )}
                {comparison.improvements.length > 0 && (
                  <span className="text-emerald-400">
                    {comparison.improvements.length} improvement{comparison.improvements.length !== 1 ? "s" : ""}
                  </span>
                )}
                <span className="text-grim-text-dim">
                  {comparison.unchanged} unchanged
                </span>
              </div>

              {/* Regressions */}
              {comparison.regressions.length > 0 && (
                <div>
                  <div className="text-[10px] text-red-400 font-medium mb-1 uppercase tracking-wider">Regressions</div>
                  <div className="space-y-1">
                    {comparison.regressions.map((r) => (
                      <div key={r.case_id} className="flex items-center gap-3 text-xs bg-red-500/5 rounded-lg px-3 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                          r.severity === "critical" ? "bg-red-500/20 text-red-400" :
                          r.severity === "major" ? "bg-orange-500/20 text-orange-400" :
                          "bg-amber-500/20 text-amber-400"
                        }`}>
                          {r.severity}
                        </span>
                        <span className="font-mono text-grim-text">{r.case_id}</span>
                        <span className="text-grim-text-dim">{r.category}</span>
                        <span className="ml-auto text-red-400 font-mono">
                          {(r.base_score * 100).toFixed(0)}% → {(r.target_score * 100).toFixed(0)}%
                          ({(r.delta * 100).toFixed(0)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Improvements */}
              {comparison.improvements.length > 0 && (
                <div>
                  <div className="text-[10px] text-emerald-400 font-medium mb-1 uppercase tracking-wider">Improvements</div>
                  <div className="space-y-1">
                    {comparison.improvements.map((r) => (
                      <div key={r.case_id} className="flex items-center gap-3 text-xs bg-emerald-500/5 rounded-lg px-3 py-1.5">
                        <span className="font-mono text-grim-text">{r.case_id}</span>
                        <span className="text-grim-text-dim">{r.category}</span>
                        <span className="ml-auto text-emerald-400 font-mono">
                          {(r.base_score * 100).toFixed(0)}% → {(r.target_score * 100).toFixed(0)}%
                          (+{(r.delta * 100).toFixed(0)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {!comparison.has_regressions && comparison.improvements.length === 0 && (
                <div className="text-xs text-grim-text-dim text-center py-2">
                  No significant changes between these runs.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {loading && (
        <div className="text-xs text-grim-text-dim animate-pulse">Loading results...</div>
      )}

      {/* Results table */}
      {filtered.length > 0 && (
        <div className="bg-grim-surface border border-grim-border rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-grim-text-dim border-b border-grim-border bg-grim-bg/50">
                <th className="text-left px-4 py-2 font-medium">Case</th>
                <th className="text-left px-3 py-2 font-medium">Suite</th>
                <th className="text-center px-3 py-2 font-medium">Status</th>
                <th className="text-right px-3 py-2 font-medium">Score</th>
                <th className="text-right px-4 py-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <CaseRow
                  key={c.case_id}
                  case_={c}
                  expanded={expandedCase === c.case_id}
                  onToggle={() =>
                    setExpandedCase(expandedCase === c.case_id ? null : c.case_id)
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && result && filtered.length === 0 && (
        <div className="text-xs text-grim-text-dim text-center py-8">
          No cases match the current filters.
        </div>
      )}
    </div>
  );
}

function CaseRow({
  case_,
  expanded,
  onToggle,
}: {
  case_: CaseResultData & { _suite: string; _tier: number };
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-grim-border/50 hover:bg-grim-bg/30 cursor-pointer"
      >
        <td className="px-4 py-2 text-grim-text font-mono">{case_.case_id}</td>
        <td className="px-3 py-2 text-grim-text-dim">{case_._suite}</td>
        <td className="px-3 py-2 text-center">
          {case_.passed ? (
            <span className="text-emerald-400">PASS</span>
          ) : (
            <span className="text-red-400">FAIL</span>
          )}
        </td>
        <td className="px-3 py-2 text-right font-mono">
          {(case_.score * 100).toFixed(0)}%
        </td>
        <td className="px-4 py-2 text-right text-grim-text-dim">
          {case_.duration_ms}ms
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} className="px-4 py-3 bg-grim-bg/50 border-b border-grim-border">
            <CaseDetail case_={case_} />
          </td>
        </tr>
      )}
    </>
  );
}

function CaseDetail({ case_ }: { case_: CaseResultData }) {
  return (
    <div className="space-y-3 text-xs">
      {/* Tags */}
      {case_.tags?.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {case_.tags.map((t) => (
            <span
              key={t}
              className="px-2 py-0.5 bg-grim-accent/10 text-grim-accent rounded-full text-[10px]"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Error */}
      {case_.error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2 text-red-400">
          {case_.error}
        </div>
      )}

      {/* Checks */}
      {case_.checks?.length > 0 && (
        <div>
          <div className="text-grim-text-dim mb-1 font-medium">Checks</div>
          <div className="space-y-1">
            {case_.checks.map((ch, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className={ch.passed ? "text-emerald-400" : "text-red-400"}>
                  {ch.passed ? "+" : "x"}
                </span>
                <span className="text-grim-text">{ch.name}</span>
                {!ch.passed && (
                  <span className="text-grim-text-dim">
                    expected: {JSON.stringify(ch.expected)} | got: {JSON.stringify(ch.actual)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dimensions */}
      {case_.dimensions?.length > 0 && (
        <div>
          <div className="text-grim-text-dim mb-1 font-medium">Dimensions</div>
          <div className="space-y-1">
            {case_.dimensions.map((d, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className="text-grim-text w-28">{d.name}</span>
                <div className="flex-1 h-2 bg-grim-border rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      d.score >= 0.8
                        ? "bg-emerald-400"
                        : d.score >= 0.5
                          ? "bg-amber-400"
                          : "bg-red-400"
                    }`}
                    style={{ width: `${d.score * 100}%` }}
                  />
                </div>
                <span className="text-grim-text-dim w-12 text-right">
                  {(d.score * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tool trace */}
      {case_.tool_trace?.length > 0 && (
        <div>
          <div className="text-grim-text-dim mb-1 font-medium">Tool Trace</div>
          <div className="flex gap-1 flex-wrap">
            {case_.tool_trace.map((t, i) => (
              <span
                key={i}
                className="px-2 py-0.5 bg-grim-surface border border-grim-border rounded text-[10px] font-mono"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Response */}
      {case_.response_text && (
        <div>
          <div className="text-grim-text-dim mb-1 font-medium">Response</div>
          <pre className="bg-grim-bg border border-grim-border rounded-lg p-3 text-[11px] whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto">
            {case_.response_text}
          </pre>
        </div>
      )}
    </div>
  );
}
