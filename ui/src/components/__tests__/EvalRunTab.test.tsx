import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EvalRunTab } from "../eval/EvalRunTab";
import type { useEval } from "@/hooks/useEval";

// ---------------------------------------------------------------------------
// Mock eval hook return
// ---------------------------------------------------------------------------

function mockEval(overrides: Partial<ReturnType<typeof useEval>> = {}): ReturnType<typeof useEval> {
  return {
    runs: [],
    activeResult: null,
    datasets: [
      { tier: 1, category: "routing", description: "Route detection", case_count: 40, path: "" },
      { tier: 1, category: "keyword_routing", description: "Keywords", case_count: 30, path: "" },
      { tier: 2, category: "single_turn", description: "Single turn", case_count: 20, path: "" },
      { tier: 3, category: "companion", description: "Companion", case_count: 10, path: "" },
      { tier: 3, category: "planning", description: "Planning", case_count: 8, path: "" },
    ],
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EvalRunTab", () => {
  describe("tier sub-tabs", () => {
    it("renders three tier tabs", () => {
      render(<EvalRunTab eval_={mockEval()} />);
      expect(screen.getByText("Tier 1")).toBeInTheDocument();
      expect(screen.getByText("Tier 2")).toBeInTheDocument();
      expect(screen.getByText("Tier 3")).toBeInTheDocument();
    });

    it("shows tier sublabels", () => {
      render(<EvalRunTab eval_={mockEval()} />);
      expect(screen.getByText("Structural")).toBeInTheDocument();
      expect(screen.getByText("LLM-Graded")).toBeInTheDocument();
      expect(screen.getByText("Live")).toBeInTheDocument();
    });

    it("calls fetchTestCases on mount with tier 1", () => {
      const eval_ = mockEval();
      render(<EvalRunTab eval_={eval_} />);
      expect(eval_.fetchTestCases).toHaveBeenCalledWith(1);
    });

    it("calls fetchTestCases when switching tiers", () => {
      const eval_ = mockEval();
      render(<EvalRunTab eval_={eval_} />);

      fireEvent.click(screen.getByText("Tier 2"));
      expect(eval_.fetchTestCases).toHaveBeenCalledWith(2);

      fireEvent.click(screen.getByText("Tier 3"));
      expect(eval_.fetchTestCases).toHaveBeenCalledWith(3);
    });
  });

  describe("run controls", () => {
    it("shows run button for active tier", () => {
      render(<EvalRunTab eval_={mockEval()} />);
      expect(screen.getByText(/Run.*All.*Tier 1/)).toBeInTheDocument();
    });

    it("calls startRun when clicking run button", () => {
      const eval_ = mockEval();
      render(<EvalRunTab eval_={eval_} />);

      fireEvent.click(screen.getByText(/Run.*All.*Tier 1/));
      expect(eval_.startRun).toHaveBeenCalledWith(1, undefined);
    });

    it("disables run button when running", () => {
      render(<EvalRunTab eval_={mockEval({ runStatus: "running" })} />);
      const btn = screen.getByText("Running...");
      expect(btn).toBeDisabled();
    });

    it("shows RUNNING badge when active", () => {
      render(<EvalRunTab eval_={mockEval({ runStatus: "running" })} />);
      expect(screen.getByText("RUNNING")).toBeInTheDocument();
    });
  });

  describe("category filter", () => {
    it("shows All categories option", () => {
      const eval_ = mockEval({
        testCases: [
          { id: "t1-r-001", tier: 1, category: "routing", description: "Test", tags: [], turn_count: 1 },
          { id: "t1-r-002", tier: 1, category: "routing", description: "Test 2", tags: [], turn_count: 1 },
        ],
      });
      render(<EvalRunTab eval_={eval_} />);
      expect(screen.getByText("All categories (2)")).toBeInTheDocument();
    });

    it("shows category options from datasets", () => {
      render(<EvalRunTab eval_={mockEval()} />);
      // Tier 1 is active by default, should show routing and keyword_routing in the select
      const select = screen.getAllByRole("combobox")[0];
      const options = select.querySelectorAll("option");
      const optionTexts = Array.from(options).map((o) => o.textContent);
      expect(optionTexts.some((t) => t?.includes("routing"))).toBe(true);
    });
  });

  describe("test case table", () => {
    const testCases = [
      { id: "t1-route-001", tier: 1, category: "routing", description: "Basic routing test", tags: ["routing"], turn_count: 1 },
      { id: "t1-route-002", tier: 1, category: "routing", description: "Skill match", tags: ["skill"], turn_count: 1 },
      { id: "t1-kw-001", tier: 1, category: "keyword_routing", description: "Keyword test", tags: ["keyword"], turn_count: 1 },
    ];

    it("renders case IDs in the table", () => {
      render(<EvalRunTab eval_={mockEval({ testCases })} />);
      expect(screen.getByText("t1-route-001")).toBeInTheDocument();
      expect(screen.getByText("t1-route-002")).toBeInTheDocument();
      expect(screen.getByText("t1-kw-001")).toBeInTheDocument();
    });

    it("shows case count", () => {
      render(<EvalRunTab eval_={mockEval({ testCases })} />);
      expect(screen.getByText("3 cases")).toBeInTheDocument();
    });

    it("shows table headers", () => {
      render(<EvalRunTab eval_={mockEval({ testCases })} />);
      expect(screen.getByText("Case ID")).toBeInTheDocument();
      expect(screen.getByText("Category")).toBeInTheDocument();
      expect(screen.getByText("Status")).toBeInTheDocument();
      expect(screen.getByText("Score")).toBeInTheDocument();
    });

    it("shows no test cases message when empty", () => {
      render(<EvalRunTab eval_={mockEval()} />);
      expect(screen.getByText(/No test cases loaded/)).toBeInTheDocument();
    });

    it("expands case detail on click", () => {
      const testCasesWithDesc = [
        { id: "t1-route-001", tier: 1, category: "routing", description: "Detailed description here", tags: ["special-tag", "core"], turn_count: 1 },
      ];
      render(<EvalRunTab eval_={mockEval({ testCases: testCasesWithDesc })} />);

      // Click the row to expand
      fireEvent.click(screen.getByText("t1-route-001"));
      // Tags should be visible in expanded detail
      expect(screen.getByText("special-tag")).toBeInTheDocument();
      expect(screen.getByText("core")).toBeInTheDocument();
    });
  });

  describe("score cards", () => {
    it("shows score cards when results are available", () => {
      const eval_ = mockEval({
        activeResult: {
          tier: 1,
          total_cases: 50,
          total_passed: 48,
          overall_score: 0.96,
          duration_ms: 3500,
        },
        testCases: [
          { id: "t1-r-001", tier: 1, category: "routing", description: "", tags: [], turn_count: 1 },
        ],
      });
      render(<EvalRunTab eval_={eval_} />);

      expect(screen.getByText("Overall Score")).toBeInTheDocument();
      expect(screen.getByText("96.0%")).toBeInTheDocument();
      expect(screen.getByText("Pass Rate")).toBeInTheDocument();
      expect(screen.getByText("48/50")).toBeInTheDocument();
      expect(screen.getByText("Duration")).toBeInTheDocument();
      expect(screen.getByText("3.5s")).toBeInTheDocument();
    });
  });

  describe("live status during run", () => {
    it("shows per-case status from caseRunStatus", () => {
      const eval_ = mockEval({
        runStatus: "running",
        testCases: [
          { id: "t3-comp-001", tier: 3, category: "companion", description: "", tags: [], turn_count: 1 },
          { id: "t3-comp-002", tier: 3, category: "companion", description: "", tags: [], turn_count: 2 },
        ],
        caseRunStatus: [
          { case_id: "t3-comp-001", status: "passed", score: 0.9, duration_ms: 5000 },
          { case_id: "t3-comp-002", status: "running" },
        ],
      });
      render(<EvalRunTab eval_={eval_} />);

      // Switch to Tier 3 first
      fireEvent.click(screen.getByText("Tier 3"));

      // The status should appear in the table based on statusMap
      // PASS and RUN indicators
      expect(screen.getByText("PASS")).toBeInTheDocument();
      expect(screen.getByText("RUN")).toBeInTheDocument();
    });

    it("shows suite progress for tier 1/2 during run", () => {
      const eval_ = mockEval({
        runStatus: "running",
        testCases: [
          { id: "t1-r-001", tier: 1, category: "routing", description: "", tags: [], turn_count: 1 },
        ],
        progress: [
          { type: "suite_start", tier: 1, category: "routing" },
          { type: "suite_end", tier: 1, category: "routing", passed: 38, total: 40, score: 0.95 },
        ],
      });
      render(<EvalRunTab eval_={eval_} />);

      // Progress text "38/40 (95%)" is in one span — use a function matcher
      expect(screen.getByText((content) => content.includes("38/40"))).toBeInTheDocument();
    });
  });

  describe("tier 3 live progress pills", () => {
    it("renders tier 3 case status pills during run", () => {
      const eval_ = mockEval({
        runStatus: "running",
        testCases: [
          { id: "t3-comp-001", tier: 3, category: "companion", description: "", tags: [], turn_count: 1 },
        ],
        caseRunStatus: [
          { case_id: "t3-comp-001", status: "passed", score: 0.85, duration_ms: 8000 },
          { case_id: "t3-comp-002", status: "running" },
          { case_id: "t3-comp-003", status: "failed", score: 0.3, duration_ms: 12000 },
        ],
      });
      render(<EvalRunTab eval_={eval_} />);

      // Switch to Tier 3
      fireEvent.click(screen.getByText("Tier 3"));

      // The pills strip out the "t3-" prefix
      expect(screen.getByText("comp-001")).toBeInTheDocument();
      expect(screen.getByText("comp-002")).toBeInTheDocument();
      expect(screen.getByText("comp-003")).toBeInTheDocument();
    });
  });
});
