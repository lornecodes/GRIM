"use client";

import { useState, useEffect } from "react";
import type { useEval } from "@/hooks/useEval";
import type { TestCase, CaseRunStatus } from "@/hooks/useEval";

interface Props {
  eval_: ReturnType<typeof useEval>;
}

const TIER_TABS = [
  { id: 1, label: "Tier 1", sublabel: "Structural", color: "text-blue-400", bg: "bg-blue-500/20", bgHover: "hover:bg-blue-500/30" },
  { id: 2, label: "Tier 2", sublabel: "LLM-Graded", color: "text-purple-400", bg: "bg-purple-500/20", bgHover: "hover:bg-purple-500/30" },
  { id: 3, label: "Tier 3", sublabel: "Live", color: "text-orange-400", bg: "bg-orange-500/20", bgHover: "hover:bg-orange-500/30" },
] as const;

export function EvalRunTab({ eval_ }: Props) {
  const {
    runStatus, progress, activeResult, datasets, testCases, caseRunStatus,
    startRun, fetchTestCases,
  } = eval_;

  const isRunning = runStatus === "running";
  const [activeTier, setActiveTier] = useState<number>(1);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [expandedCase, setExpandedCase] = useState<string | null>(null);

  // Fetch test cases when tier tab changes
  useEffect(() => {
    fetchTestCases(activeTier);
    setCategoryFilter("all");
    setExpandedCase(null);
  }, [activeTier, fetchTestCases]);

  // Get categories for current tier
  const tierDatasets = datasets.filter((d) => d.tier === activeTier);
  const categories = tierDatasets.map((d) => d.category);

  // Filter test cases
  const filteredCases = categoryFilter === "all"
    ? testCases
    : testCases.filter((c) => c.category === categoryFilter);

  // Build status map from caseRunStatus + activeResult
  const statusMap = new Map<string, CaseRunStatus>();
  for (const s of caseRunStatus) {
    statusMap.set(s.case_id, s);
  }

  // Also populate from activeResult if we have one for this tier
  const result = activeResult as {
    tier?: number;
    total_cases?: number;
    total_passed?: number;
    overall_score?: number;
    duration_ms?: number;
    suites?: Array<{
      tier: number;
      category: string;
      passed: number;
      total: number;
      score: number;
      cases?: Array<{ case_id: string; passed: boolean; score: number; duration_ms: number }>;
    }>;
  } | null;

  const resultTier = result?.tier;
  const resultCases = new Map<string, { passed: boolean; score: number; duration_ms: number }>();
  if (result?.suites) {
    for (const suite of result.suites) {
      for (const c of (suite.cases || [])) {
        resultCases.set(c.case_id, { passed: c.passed, score: c.score, duration_ms: c.duration_ms });
      }
    }
  }

  // Stats
  const tierResult = resultTier === activeTier ? result : null;
  const totalCases = tierResult?.total_cases ?? 0;
  const passedCases = tierResult?.total_passed ?? 0;
  const overallScore = tierResult?.overall_score ?? 0;
  const durationMs = tierResult?.duration_ms ?? 0;

  const tierConfig = TIER_TABS.find((t) => t.id === activeTier)!;

  const handleRun = () => {
    const cats = categoryFilter !== "all" ? [categoryFilter] : undefined;
    startRun(activeTier, cats);
  };

  return (
    <div className="space-y-4">
      {/* Tier sub-tabs */}
      <div className="flex gap-1 bg-grim-surface border border-grim-border rounded-xl p-1">
        {TIER_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTier(tab.id)}
            className={`flex-1 px-4 py-2 rounded-lg text-xs font-medium transition-colors ${
              activeTier === tab.id
                ? `${tab.bg} ${tab.color}`
                : "text-grim-text-dim hover:text-grim-text hover:bg-grim-bg/50"
            }`}
          >
            {tab.label}
            <span className="ml-1.5 opacity-60">{tab.sublabel}</span>
          </button>
        ))}
      </div>

      {/* Header bar: run button + category filter */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={handleRun}
          disabled={isRunning}
          className={`px-5 py-2 text-xs font-medium rounded-lg ${tierConfig.bg} ${tierConfig.color} ${tierConfig.bgHover} disabled:opacity-40 disabled:cursor-not-allowed transition-colors`}
        >
          {isRunning ? "Running..." : `Run ${categoryFilter !== "all" ? categoryFilter : `All ${tierConfig.label}`}`}
        </button>

        {/* Category filter */}
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="bg-grim-surface border border-grim-border rounded-lg px-3 py-1.5 text-xs text-grim-text"
        >
          <option value="all">All categories ({testCases.length})</option>
          {categories.map((cat) => {
            const count = testCases.filter((c) => c.category === cat).length;
            return (
              <option key={cat} value={cat}>
                {cat} ({count})
              </option>
            );
          })}
        </select>

        {isRunning && (
          <span className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-amber-500/20 text-amber-400 animate-pulse">
            RUNNING
          </span>
        )}

        <span className="text-[10px] text-grim-text-dim ml-auto">
          {filteredCases.length} cases
        </span>
      </div>

      {/* Score cards */}
      {(totalCases > 0 || isRunning) && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <ScoreCard
            label="Overall Score"
            value={overallScore > 0 ? `${(overallScore * 100).toFixed(1)}%` : "—"}
            color={overallScore >= 0.9 ? "text-emerald-400" : overallScore >= 0.7 ? "text-amber-400" : overallScore > 0 ? "text-red-400" : "text-grim-text-dim"}
          />
          <ScoreCard
            label="Pass Rate"
            value={totalCases > 0 ? `${passedCases}/${totalCases}` : "—"}
            color={tierConfig.color}
          />
          <ScoreCard
            label="Total Cases"
            value={totalCases > 0 ? `${totalCases}` : `${filteredCases.length}`}
            color="text-grim-text"
          />
          <ScoreCard
            label="Duration"
            value={durationMs > 0 ? `${(durationMs / 1000).toFixed(1)}s` : "—"}
            color="text-grim-text-dim"
          />
        </div>
      )}

      {/* Live progress for Tier 3 */}
      {isRunning && activeTier === 3 && caseRunStatus.length > 0 && (
        <div className="bg-grim-surface border border-grim-border rounded-xl p-3">
          <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-2">
            Live Progress
          </div>
          <div className="flex gap-1 flex-wrap">
            {caseRunStatus.map((cs) => (
              <span
                key={cs.case_id}
                className={`px-2 py-0.5 text-[10px] font-mono rounded ${
                  cs.status === "running"
                    ? "bg-amber-500/20 text-amber-400 animate-pulse"
                    : cs.status === "passed"
                      ? "bg-emerald-500/20 text-emerald-400"
                      : cs.status === "failed"
                        ? "bg-red-500/20 text-red-400"
                        : "bg-grim-bg text-grim-text-dim"
                }`}
                title={cs.score !== undefined ? `${(cs.score * 100).toFixed(0)}% — ${cs.duration_ms}ms` : undefined}
              >
                {cs.case_id.replace(/^t3-/, "")}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Live progress for Tier 1/2 */}
      {isRunning && activeTier !== 3 && progress.length > 0 && (
        <div className="bg-grim-surface border border-grim-border rounded-xl p-3">
          <div className="text-[11px] text-grim-text-dim uppercase tracking-wider mb-2">
            Running...
          </div>
          <div className="space-y-1">
            {progress
              .filter((evt) => evt.type === "suite_start" || evt.type === "suite_end")
              .map((evt, i) => (
                <div key={i} className="flex items-center gap-3 text-xs">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      evt.type === "suite_start"
                        ? "bg-amber-400 animate-pulse"
                        : (evt.score ?? 0) >= 1.0
                          ? "bg-emerald-400"
                          : (evt.score ?? 0) >= 0.7
                            ? "bg-amber-400"
                            : "bg-red-400"
                    }`}
                  />
                  <span className="text-grim-text">{evt.category}</span>
                  {evt.type === "suite_end" && (
                    <span className="text-grim-text-dim">
                      {evt.passed}/{evt.total}
                      {evt.score !== undefined && ` (${(evt.score * 100).toFixed(0)}%)`}
                    </span>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Test case table */}
      {filteredCases.length > 0 && (
        <div className="bg-grim-surface border border-grim-border rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-grim-text-dim border-b border-grim-border bg-grim-bg/50">
                <th className="text-left px-4 py-2 font-medium">Case ID</th>
                <th className="text-left px-3 py-2 font-medium">Category</th>
                <th className="text-left px-3 py-2 font-medium hidden lg:table-cell">Description</th>
                <th className="text-center px-3 py-2 font-medium w-16">Turns</th>
                <th className="text-center px-3 py-2 font-medium w-20">Status</th>
                <th className="text-right px-3 py-2 font-medium w-16">Score</th>
                <th className="text-right px-4 py-2 font-medium w-16">Time</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((tc) => {
                const runSt = statusMap.get(tc.id);
                const resSt = resultCases.get(tc.id);
                const isExpanded = expandedCase === tc.id;

                return (
                  <TestCaseRow
                    key={tc.id}
                    testCase={tc}
                    runStatus={runSt}
                    resultStatus={resSt}
                    expanded={isExpanded}
                    onToggle={() => setExpandedCase(isExpanded ? null : tc.id)}
                    tierColor={tierConfig.color}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {filteredCases.length === 0 && !isRunning && (
        <div className="text-xs text-grim-text-dim text-center py-8">
          No test cases loaded. Check that the GRIM server is running.
        </div>
      )}
    </div>
  );
}

function TestCaseRow({
  testCase,
  runStatus,
  resultStatus,
  expanded,
  onToggle,
  tierColor,
}: {
  testCase: TestCase;
  runStatus?: CaseRunStatus;
  resultStatus?: { passed: boolean; score: number; duration_ms: number };
  expanded: boolean;
  onToggle: () => void;
  tierColor: string;
}) {
  // Determine display status
  let statusDisplay: React.ReactNode = <span className="text-grim-text-dim">—</span>;
  let scoreDisplay = "—";
  let timeDisplay = "—";

  if (runStatus) {
    if (runStatus.status === "running") {
      statusDisplay = <span className="text-amber-400 animate-pulse">RUN</span>;
    } else if (runStatus.status === "passed") {
      statusDisplay = <span className="text-emerald-400">PASS</span>;
      if (runStatus.score !== undefined) scoreDisplay = `${(runStatus.score * 100).toFixed(0)}%`;
      if (runStatus.duration_ms) timeDisplay = `${runStatus.duration_ms}ms`;
    } else if (runStatus.status === "failed") {
      statusDisplay = <span className="text-red-400">FAIL</span>;
      if (runStatus.score !== undefined) scoreDisplay = `${(runStatus.score * 100).toFixed(0)}%`;
      if (runStatus.duration_ms) timeDisplay = `${runStatus.duration_ms}ms`;
    }
  } else if (resultStatus) {
    statusDisplay = resultStatus.passed
      ? <span className="text-emerald-400">PASS</span>
      : <span className="text-red-400">FAIL</span>;
    scoreDisplay = `${(resultStatus.score * 100).toFixed(0)}%`;
    timeDisplay = resultStatus.duration_ms > 0 ? `${resultStatus.duration_ms}ms` : "—";
  }

  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-grim-border/50 hover:bg-grim-bg/30 cursor-pointer"
      >
        <td className="px-4 py-2 text-grim-text font-mono text-[11px]">{testCase.id}</td>
        <td className="px-3 py-2">
          <span className={`${tierColor} opacity-80`}>{testCase.category}</span>
        </td>
        <td className="px-3 py-2 text-grim-text-dim truncate max-w-[200px] hidden lg:table-cell">
          {testCase.description}
        </td>
        <td className="px-3 py-2 text-center text-grim-text-dim">
          {testCase.turn_count > 1 ? testCase.turn_count : ""}
        </td>
        <td className="px-3 py-2 text-center text-[10px] font-medium">
          {statusDisplay}
        </td>
        <td className="px-3 py-2 text-right font-mono text-grim-text-dim">
          {scoreDisplay}
        </td>
        <td className="px-4 py-2 text-right text-grim-text-dim">
          {timeDisplay}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="px-4 py-3 bg-grim-bg/50 border-b border-grim-border">
            <TestCaseDetail testCase={testCase} />
          </td>
        </tr>
      )}
    </>
  );
}

function TestCaseDetail({ testCase }: { testCase: TestCase }) {
  return (
    <div className="space-y-2 text-xs">
      {testCase.description && (
        <div className="text-grim-text">{testCase.description}</div>
      )}
      {testCase.tags.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {testCase.tags.map((t) => (
            <span
              key={t}
              className="px-2 py-0.5 bg-grim-accent/10 text-grim-accent rounded-full text-[10px]"
            >
              {t}
            </span>
          ))}
        </div>
      )}
      {testCase.turn_count > 1 && (
        <div className="text-grim-text-dim">
          Multi-turn: {testCase.turn_count} turns
        </div>
      )}
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
