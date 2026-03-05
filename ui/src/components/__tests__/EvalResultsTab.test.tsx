import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EvalResultsTab } from "../eval/EvalResultsTab";
import type { useEval } from "@/hooks/useEval";

// ---------------------------------------------------------------------------
// Mock eval hook
// ---------------------------------------------------------------------------

function mockEval(overrides: Partial<ReturnType<typeof useEval>> = {}): ReturnType<typeof useEval> {
  return {
    runs: [],
    activeResult: null,
    datasets: [],
    datasetContent: null,
    runStatus: "idle",
    activeRunId: null,
    progress: [],
    loading: false,
    error: null,
    testCases: [],
    caseRunStatus: [],
    fetchRuns: vi.fn(),
    fetchDatasets: vi.fn(),
    fetchResults: vi.fn(),
    fetchDatasetContent: vi.fn(),
    fetchTestCases: vi.fn(),
    startRun: vi.fn(),
    appendCase: vi.fn(),
    updateCase: vi.fn(),
    deleteCase: vi.fn(),
    compareRuns: vi.fn(),
    setActiveResult: vi.fn(),
    setError: vi.fn(),
    ...overrides,
  };
}

const TIER1_RESULT = {
  run_id: "run-t1",
  tier: 1,
  total_cases: 3,
  total_passed: 2,
  overall_score: 0.83,
  duration_ms: 1500,
  suites: [
    {
      tier: 1,
      category: "routing",
      cases: [
        {
          case_id: "t1-route-001",
          tier: 1,
          category: "routing",
          passed: true,
          score: 1.0,
          duration_ms: 200,
          tags: ["routing"],
          checks: [{ name: "route_match", expected: "companion", actual: "companion", passed: true }],
          dimensions: [],
          tool_trace: [],
          response_text: "",
        },
        {
          case_id: "t1-route-002",
          tier: 1,
          category: "routing",
          passed: false,
          score: 0.0,
          duration_ms: 150,
          tags: ["routing"],
          checks: [{ name: "route_match", expected: "planning", actual: "companion", passed: false }],
          dimensions: [],
          tool_trace: [],
          response_text: "",
        },
      ],
      passed: 1,
      total: 2,
      score: 0.5,
    },
    {
      tier: 1,
      category: "keyword_routing",
      cases: [
        {
          case_id: "t1-kw-001",
          tier: 1,
          category: "keyword_routing",
          passed: true,
          score: 1.0,
          duration_ms: 100,
          tags: ["keyword"],
          checks: [],
          dimensions: [],
          tool_trace: [],
          response_text: "",
        },
      ],
      passed: 1,
      total: 1,
      score: 1.0,
    },
  ],
};

const TIER3_RESULT = {
  run_id: "run-t3",
  tier: 3,
  total_cases: 1,
  total_passed: 1,
  overall_score: 0.85,
  duration_ms: 15000,
  suites: [
    {
      tier: 3,
      category: "companion",
      cases: [
        {
          case_id: "t3-comp-001",
          tier: 3,
          category: "companion",
          passed: true,
          score: 0.85,
          duration_ms: 12000,
          tags: ["companion", "live"],
          checks: [],
          dimensions: [],
          tool_trace: ["kronos_search", "kronos_get"],
          response_text: "I found the information...",
          judgments: [
            { judge: "routing", score: 1.0, passed: true, rationale: "Correct route to companion" },
            { judge: "quality", score: 0.9, passed: true, rationale: "High quality response" },
            { judge: "efficiency", score: 0.65, passed: false, rationale: "Tokens exceeded budget" },
          ],
          routing_path: ["identity", "memory", "companion_router", "conversation"],
          metrics: {
            total_tokens: 37412,
            input_tokens: 35000,
            output_tokens: 2412,
            wall_time_ms: 12000,
            turns: 1,
            llm_call_count: 3,
            tool_call_count: 5,
            cost_estimate_usd: 0.14,
          },
          turn_results: [
            {
              turn_index: 0,
              response_text: "I found the information...",
              routing_path: ["companion_router", "conversation"],
              subgraph: "conversation",
              tools_called: ["kronos_search", "kronos_get"],
            },
          ],
        },
      ],
      passed: 1,
      total: 1,
      score: 0.85,
    },
  ],
};

const RUNS = [
  { run_id: "run-t1", timestamp: "2026-03-04T10:00:00Z", status: "completed", tier: 1, total_cases: 3, passed_cases: 2, overall_score: 0.83, git_sha: "abc", duration_ms: 1500 },
  { run_id: "run-t3", timestamp: "2026-03-04T11:00:00Z", status: "completed", tier: 3, total_cases: 1, passed_cases: 1, overall_score: 0.85, git_sha: "def", duration_ms: 15000 },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EvalResultsTab", () => {
  describe("run selector", () => {
    it("renders run selector with options", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS })} />);
      expect(screen.getByText("Select a run...")).toBeInTheDocument();
    });

    it("calls fetchResults when selecting a run", () => {
      const eval_ = mockEval({ runs: RUNS });
      render(<EvalResultsTab eval_={eval_} />);

      const select = screen.getAllByRole("combobox")[0];
      fireEvent.change(select, { target: { value: "run-t1" } });
      expect(eval_.fetchResults).toHaveBeenCalledWith("run-t1");
    });
  });

  describe("tier 1/2 results", () => {
    it("renders case rows from result suites", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      expect(screen.getByText("t1-route-001")).toBeInTheDocument();
      expect(screen.getByText("t1-route-002")).toBeInTheDocument();
      expect(screen.getByText("t1-kw-001")).toBeInTheDocument();
    });

    it("shows PASS and FAIL status", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      // Filter buttons also say "PASS" and "FAIL", so check within the table
      const table = screen.getByRole("table");
      const passes = table.querySelectorAll(".text-emerald-400");
      const fails = table.querySelectorAll(".text-red-400");
      expect(passes.length).toBe(2); // t1-route-001 and t1-kw-001
      expect(fails.length).toBe(1); // t1-route-002
    });

    it("shows score percentages", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      // Score column uses font-mono class
      const table = screen.getByRole("table");
      const scoreCells = table.querySelectorAll("td.font-mono");
      const scores = Array.from(scoreCells).map((c) => c.textContent);
      expect(scores).toContain("100%");
      expect(scores).toContain("0%");
    });

    it("expands case detail with checks on click", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);

      // Click the failing case row
      fireEvent.click(screen.getByText("t1-route-002"));

      // Should show the checks section
      expect(screen.getByText("Checks")).toBeInTheDocument();
      expect(screen.getByText("route_match")).toBeInTheDocument();
    });
  });

  describe("tier filter", () => {
    it("includes Tier 3 option in filter dropdown", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      expect(screen.getByText("Tier 3")).toBeInTheDocument();
    });

    it("filters cases by pass/fail status", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);

      // Click FAIL filter
      fireEvent.click(screen.getByText("FAIL", { selector: "button" }));

      // Should only show the failing case
      expect(screen.getByText("t1-route-002")).toBeInTheDocument();
      expect(screen.queryByText("t1-route-001")).not.toBeInTheDocument();
    });

    it("shows case count in filter summary", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      expect(screen.getByText("3 of 3 cases")).toBeInTheDocument();
    });
  });

  describe("tier 3 results — judges", () => {
    it("renders judge bars for tier 3 cases", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);

      // Expand the tier 3 case
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Judges")).toBeInTheDocument();
      expect(screen.getByText("routing")).toBeInTheDocument();
      expect(screen.getByText("quality")).toBeInTheDocument();
      expect(screen.getByText("efficiency")).toBeInTheDocument();
    });

    it("shows pass/fail badges on judges", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      // 2 passing judges (routing, quality) + 1 case PASS + run selector PASS → multiple PASS
      // 1 failing judge (efficiency) → at least one FAIL in judge section
      const judges = screen.getByText("Judges").parentElement!;
      expect(judges.querySelectorAll('[class*="emerald"]').length).toBeGreaterThan(0);
      expect(judges.querySelectorAll('[class*="red"]').length).toBeGreaterThan(0);
    });

    it("shows judge rationale text", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Correct route to companion")).toBeInTheDocument();
      expect(screen.getByText("Tokens exceeded budget")).toBeInTheDocument();
    });
  });

  describe("tier 3 results — routing path", () => {
    it("renders routing path pills", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Routing Path")).toBeInTheDocument();
      expect(screen.getByText("identity")).toBeInTheDocument();
      expect(screen.getByText("memory")).toBeInTheDocument();
      expect(screen.getByText("companion_router")).toBeInTheDocument();
      expect(screen.getByText("conversation")).toBeInTheDocument();
    });

    it("shows arrows between routing nodes", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      const arrows = screen.getAllByText("→");
      expect(arrows.length).toBe(3); // 4 nodes = 3 arrows
    });
  });

  describe("tier 3 results — efficiency metrics", () => {
    it("renders metrics grid", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Efficiency")).toBeInTheDocument();
      expect(screen.getByText("Tokens")).toBeInTheDocument();
      expect(screen.getByText("Cost")).toBeInTheDocument();
      expect(screen.getByText("Wall Time")).toBeInTheDocument();
      expect(screen.getByText("Loops")).toBeInTheDocument();
      expect(screen.getByText("LLM Calls")).toBeInTheDocument();
      expect(screen.getByText("Tool Calls")).toBeInTheDocument();
    });

    it("shows formatted metric values", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("37,412")).toBeInTheDocument();
      expect(screen.getByText("$0.1400")).toBeInTheDocument();
      expect(screen.getByText("12.0s")).toBeInTheDocument();
    });

    it("shows token breakdown (in/out)", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText(/in: 35\.0K/)).toBeInTheDocument();
      expect(screen.getByText(/out: 2\.4K/)).toBeInTheDocument();
    });
  });

  describe("tier 3 results — per-turn detail", () => {
    const multiTurnResult = {
      ...TIER3_RESULT,
      suites: [
        {
          ...TIER3_RESULT.suites[0],
          cases: [
            {
              ...TIER3_RESULT.suites[0].cases[0],
              turn_results: [
                {
                  turn_index: 0,
                  response_text: "First turn response",
                  routing_path: ["companion_router"],
                  subgraph: "conversation",
                  tools_called: ["kronos_search"],
                },
                {
                  turn_index: 1,
                  response_text: "Second turn response",
                  routing_path: ["planning_companion"],
                  subgraph: "planning",
                  tools_called: ["kronos_get", "kronos_graph"],
                },
              ],
            },
          ],
        },
      ],
    };

    it("shows turn section for multi-turn cases", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: multiTurnResult })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Turns (2)")).toBeInTheDocument();
      expect(screen.getByText("T1")).toBeInTheDocument();
      expect(screen.getByText("T2")).toBeInTheDocument();
    });

    it("shows subgraph labels on turn headers", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: multiTurnResult })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      // Look for turn-specific subgraph badges (they appear inside buttons with grim-accent class)
      const turnSection = screen.getByText("Turns (2)").parentElement!;
      const badges = turnSection.querySelectorAll(".text-grim-accent");
      const badgeTexts = Array.from(badges).map((b) => b.textContent);
      expect(badgeTexts).toContain("conversation");
      expect(badgeTexts).toContain("planning");
    });
  });

  describe("compare panel", () => {
    it("shows compare button when results are loaded", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER1_RESULT })} />);
      expect(screen.getByText("Compare")).toBeInTheDocument();
    });

    it("toggles compare panel", () => {
      // The compare panel requires activeResult to show the Compare button,
      // and it needs a selectedRunId (set by handleSelectRun) to render the panel.
      // Since handleSelectRun is triggered by the select, we simulate that first.
      const eval_ = mockEval({ runs: RUNS, activeResult: TIER1_RESULT });
      render(<EvalResultsTab eval_={eval_} />);

      // Select a run first so selectedRunId is set
      const select = screen.getAllByRole("combobox")[0];
      fireEvent.change(select, { target: { value: "run-t1" } });

      // Now click Compare
      fireEvent.click(screen.getByText("Compare"));
      expect(screen.getByText("Compare Against Baseline")).toBeInTheDocument();
    });
  });

  describe("loading state", () => {
    it("shows loading indicator", () => {
      render(<EvalResultsTab eval_={mockEval({ loading: true })} />);
      expect(screen.getByText("Loading results...")).toBeInTheDocument();
    });
  });

  describe("empty state", () => {
    it("shows no cases message when filtered to empty", () => {
      const eval_ = mockEval({ activeResult: { ...TIER1_RESULT, suites: [] } });
      render(<EvalResultsTab eval_={eval_} />);
      expect(screen.getByText(/No cases match/)).toBeInTheDocument();
    });
  });

  describe("tool trace", () => {
    it("shows tool trace in tier 3 expanded case", () => {
      render(<EvalResultsTab eval_={mockEval({ runs: RUNS, activeResult: TIER3_RESULT })} />);
      fireEvent.click(screen.getByText("t3-comp-001"));

      expect(screen.getByText("Tool Trace")).toBeInTheDocument();
      expect(screen.getByText("kronos_search")).toBeInTheDocument();
      expect(screen.getByText("kronos_get")).toBeInTheDocument();
    });
  });
});
