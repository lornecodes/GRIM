import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EvalDatasetsTab } from "../eval/EvalDatasetsTab";
import type { useEval } from "@/hooks/useEval";

// ---------------------------------------------------------------------------
// Mock eval hook
// ---------------------------------------------------------------------------

function mockEval(overrides: Partial<ReturnType<typeof useEval>> = {}): ReturnType<typeof useEval> {
  return {
    runs: [],
    activeResult: null,
    datasets: [
      { tier: 1, category: "routing", description: "Route detection", case_count: 40, path: "" },
      { tier: 1, category: "keyword_routing", description: "Keywords", case_count: 30, path: "" },
      { tier: 2, category: "single_turn", description: "Single turn", case_count: 20, path: "" },
      { tier: 2, category: "multi_turn", description: "Multi turn", case_count: 15, path: "" },
      { tier: 3, category: "companion", description: "Companion tests", case_count: 10, path: "" },
      { tier: 3, category: "planning", description: "Planning tests", case_count: 8, path: "" },
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
    appendCase: vi.fn().mockResolvedValue(true),
    updateCase: vi.fn().mockResolvedValue(true),
    deleteCase: vi.fn().mockResolvedValue(true),
    compareRuns: vi.fn(),
    setActiveResult: vi.fn(),
    setError: vi.fn(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EvalDatasetsTab", () => {
  describe("sidebar — tier sections", () => {
    it("renders Tier 1 section with datasets", () => {
      render(<EvalDatasetsTab eval_={mockEval()} />);
      expect(screen.getByText("Tier 1 — Structural")).toBeInTheDocument();
      expect(screen.getByText("routing")).toBeInTheDocument();
      expect(screen.getByText("keyword_routing")).toBeInTheDocument();
    });

    it("renders Tier 2 section", () => {
      render(<EvalDatasetsTab eval_={mockEval()} />);
      expect(screen.getByText("Tier 2 — LLM-Graded")).toBeInTheDocument();
      expect(screen.getByText("single_turn")).toBeInTheDocument();
      expect(screen.getByText("multi_turn")).toBeInTheDocument();
    });

    it("renders Tier 3 section", () => {
      render(<EvalDatasetsTab eval_={mockEval()} />);
      expect(screen.getByText("Tier 3 — Live Integration")).toBeInTheDocument();
      expect(screen.getByText("companion")).toBeInTheDocument();
      expect(screen.getByText("planning")).toBeInTheDocument();
    });

    it("hides Tier 3 section when no tier 3 datasets", () => {
      const eval_ = mockEval({
        datasets: [
          { tier: 1, category: "routing", description: "", case_count: 40, path: "" },
        ],
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      expect(screen.queryByText("Tier 3 — Live Integration")).not.toBeInTheDocument();
    });

    it("shows case counts in dataset buttons", () => {
      render(<EvalDatasetsTab eval_={mockEval()} />);
      expect(screen.getByText("40")).toBeInTheDocument(); // routing
      expect(screen.getByText("30")).toBeInTheDocument(); // keyword_routing
      expect(screen.getByText("10")).toBeInTheDocument(); // companion
    });
  });

  describe("dataset selection", () => {
    it("shows empty state before selection", () => {
      render(<EvalDatasetsTab eval_={mockEval()} />);
      expect(screen.getByText("Select a dataset to view its cases.")).toBeInTheDocument();
    });

    it("calls fetchDatasetContent when clicking a dataset", () => {
      const eval_ = mockEval();
      render(<EvalDatasetsTab eval_={eval_} />);

      fireEvent.click(screen.getByText("routing"));
      expect(eval_.fetchDatasetContent).toHaveBeenCalledWith(1, "routing");
    });

    it("shows dataset header with case count after selection", () => {
      const eval_ = mockEval({
        datasetContent: { cases: [] },
      });
      render(<EvalDatasetsTab eval_={eval_} />);

      fireEvent.click(screen.getByText("routing"));
      // The case count header shows "Tier N — X cases"
      expect(screen.getByText(/0 cases/)).toBeInTheDocument();
      // The "+ Add Case" button appears (tier 1/2)
      expect(screen.getByText("+ Add Case")).toBeInTheDocument();
    });
  });

  describe("case list", () => {
    it("renders cases from dataset content", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [
            { id: "case-001", tags: ["routing", "core"], message: "hello" },
            { id: "case-002", tags: ["routing"], message: "test" },
          ],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      expect(screen.getByText("case-001")).toBeInTheDocument();
      expect(screen.getByText("case-002")).toBeInTheDocument();
    });

    it("shows tags on case rows", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [
            { id: "case-001", tags: ["routing", "core"], message: "hello" },
          ],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      expect(screen.getByText("core")).toBeInTheDocument();
    });

    it("expands case to show JSON detail", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [
            { id: "case-001", tags: ["routing"], message: "hello world" },
          ],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      // Click to expand
      fireEvent.click(screen.getByText("case-001"));
      expect(screen.getByText(/hello world/)).toBeInTheDocument();
    });
  });

  describe("tier 1/2 — CRUD buttons", () => {
    it("shows Add Case button for tier 1", () => {
      const eval_ = mockEval({
        datasetContent: { cases: [] },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      expect(screen.getByText("+ Add Case")).toBeInTheDocument();
    });

    it("shows edit and del buttons on tier 1 cases", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [{ id: "case-001", tags: [], message: "test" }],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      expect(screen.getByText("edit")).toBeInTheDocument();
      expect(screen.getByText("del")).toBeInTheDocument();
    });

    it("shows add form when clicking Add Case", () => {
      const eval_ = mockEval({
        datasetContent: { cases: [] },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));
      fireEvent.click(screen.getByText("+ Add Case"));

      expect(screen.getByText("Add New Case (JSON)")).toBeInTheDocument();
      expect(screen.getByText("Append Case")).toBeInTheDocument();
    });

    it("shows delete confirmation flow", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [{ id: "case-001", tags: [], message: "test" }],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("routing"));

      // Click del
      fireEvent.click(screen.getByText("del"));
      // Should show confirm/cancel
      expect(screen.getByText("confirm")).toBeInTheDocument();
      expect(screen.getByText("cancel")).toBeInTheDocument();
    });
  });

  describe("tier 3 — read only", () => {
    it("shows read-only label instead of Add Case for tier 3", () => {
      const eval_ = mockEval({
        datasetContent: { cases: [{ id: "t3-001", tags: [], turns: [] }] },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("companion"));

      expect(screen.getByText("read-only")).toBeInTheDocument();
      expect(screen.queryByText("+ Add Case")).not.toBeInTheDocument();
    });

    it("hides edit/del buttons for tier 3 cases", () => {
      const eval_ = mockEval({
        datasetContent: {
          cases: [{ id: "t3-comp-001", tags: ["companion"], turns: [] }],
        },
      });
      render(<EvalDatasetsTab eval_={eval_} />);
      fireEvent.click(screen.getByText("companion"));

      expect(screen.queryByText("edit")).not.toBeInTheDocument();
      expect(screen.queryByText("del")).not.toBeInTheDocument();
    });
  });
});
