import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useEval } from "../useEval";

// ---------------------------------------------------------------------------
// Mock fetch
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch;

// Mock WebSocket
class MockWebSocket {
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  send = vi.fn();
}

vi.stubGlobal("WebSocket", vi.fn(() => new MockWebSocket()));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  } as Response;
}

function errorJson(data: unknown, status = 500): Response {
  return {
    ok: false,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

const MOCK_RUNS = [
  {
    run_id: "run-001",
    timestamp: "2026-03-04T10:00:00Z",
    status: "completed",
    tier: 1,
    total_cases: 176,
    passed_cases: 170,
    overall_score: 0.97,
    git_sha: "abc123",
    duration_ms: 5000,
  },
  {
    run_id: "run-002",
    timestamp: "2026-03-04T11:00:00Z",
    status: "completed",
    tier: 3,
    total_cases: 30,
    passed_cases: 25,
    overall_score: 0.83,
    git_sha: "def456",
    duration_ms: 120000,
  },
];

const MOCK_DATASETS = [
  { tier: 1, category: "routing", description: "Route detection", case_count: 40, path: "datasets/tier1/routing_cases.yaml" },
  { tier: 2, category: "single_turn", description: "Single turn", case_count: 20, path: "datasets/tier2/single_turn_cases.yaml" },
  { tier: 3, category: "companion", description: "Companion", case_count: 10, path: "datasets/tier3/companion.yaml" },
];

const MOCK_TEST_CASES = {
  cases: [
    { id: "t1-route-001", tier: 1, category: "routing", description: "Basic routing", tags: ["routing"], turn_count: 1 },
    { id: "t1-route-002", tier: 1, category: "routing", description: "Skill routing", tags: ["routing", "skill"], turn_count: 1 },
  ],
};

const MOCK_RESULT = {
  run_id: "run-001",
  tier: 1,
  total_cases: 2,
  total_passed: 2,
  overall_score: 1.0,
  duration_ms: 500,
  suites: [
    {
      tier: 1,
      category: "routing",
      cases: [
        { case_id: "t1-route-001", passed: true, score: 1.0, duration_ms: 200 },
        { case_id: "t1-route-002", passed: true, score: 1.0, duration_ms: 300 },
      ],
      passed: 2,
      total: 2,
      score: 1.0,
    },
  ],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllTimers();
});

describe("useEval", () => {
  describe("fetchRuns", () => {
    it("fetches and stores run list", async () => {
      mockFetch.mockResolvedValueOnce(okJson(MOCK_RUNS));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchRuns();
      });

      expect(result.current.runs).toHaveLength(2);
      expect(result.current.runs[0].run_id).toBe("run-001");
      expect(result.current.runs[1].tier).toBe(3);
    });

    it("sets error on fetch failure", async () => {
      mockFetch.mockRejectedValueOnce(new Error("Network error"));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchRuns();
      });

      expect(result.current.error).toBe("Network error");
    });
  });

  describe("fetchDatasets", () => {
    it("fetches and stores dataset list", async () => {
      mockFetch.mockResolvedValueOnce(okJson(MOCK_DATASETS));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchDatasets();
      });

      expect(result.current.datasets).toHaveLength(3);
      expect(result.current.datasets[2].tier).toBe(3);
      expect(result.current.datasets[2].category).toBe("companion");
    });
  });

  describe("fetchTestCases", () => {
    it("fetches test cases for a tier", async () => {
      mockFetch.mockResolvedValueOnce(okJson(MOCK_TEST_CASES));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchTestCases(1);
      });

      expect(result.current.testCases).toHaveLength(2);
      expect(result.current.testCases[0].id).toBe("t1-route-001");
      expect(result.current.testCases[1].tags).toContain("skill");
    });

    it("passes category filter as query param", async () => {
      mockFetch.mockResolvedValueOnce(okJson({ cases: [] }));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchTestCases(2, "single_turn");
      });

      const fetchCall = mockFetch.mock.calls[0][0] as string;
      expect(fetchCall).toContain("/api/eval/cases/2");
      expect(fetchCall).toContain("category=single_turn");
    });

    it("handles empty response", async () => {
      mockFetch.mockResolvedValueOnce(okJson({}));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchTestCases(3);
      });

      expect(result.current.testCases).toEqual([]);
    });
  });

  describe("fetchResults", () => {
    it("fetches and stores result detail", async () => {
      mockFetch.mockResolvedValueOnce(okJson(MOCK_RESULT));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchResults("run-001");
      });

      expect(result.current.activeResult).toBeDefined();
      expect((result.current.activeResult as Record<string, unknown>)?.run_id).toBe("run-001");
      expect(result.current.loading).toBe(false);
    });

    it("sets error on failed fetch", async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 404, json: () => Promise.resolve({}) } as Response);
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchResults("nonexistent");
      });

      expect(result.current.error).toBe("Failed to fetch results");
    });
  });

  describe("fetchDatasetContent", () => {
    it("fetches dataset content for tier/category", async () => {
      const content = { cases: [{ id: "case-1", message: "hello" }] };
      mockFetch.mockResolvedValueOnce(okJson(content));
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.fetchDatasetContent(1, "routing");
      });

      const fetchCall = mockFetch.mock.calls[0][0] as string;
      expect(fetchCall).toContain("/api/eval/datasets/1/routing");
      expect(result.current.datasetContent).toEqual(content);
    });
  });

  describe("startRun", () => {
    it("sends POST to start a run and sets running status", async () => {
      mockFetch.mockResolvedValueOnce(okJson({ run_id: "run-new-001" }));
      // Polling will happen, mock that too
      mockFetch.mockResolvedValue(okJson({ status: "running" }));

      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.startRun(1);
      });

      expect(result.current.runStatus).toBe("running");
      expect(result.current.activeRunId).toBe("run-new-001");

      // Verify the POST payload
      const [url, opts] = mockFetch.mock.calls[0];
      expect(url).toContain("/api/eval/run");
      expect(opts.method).toBe("POST");
      const body = JSON.parse(opts.body);
      expect(body.tier).toBe(1);
    });

    it("sends categories when provided", async () => {
      mockFetch.mockResolvedValueOnce(okJson({ run_id: "run-new-002" }));
      mockFetch.mockResolvedValue(okJson({ status: "running" }));

      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.startRun(3, ["companion"]);
      });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.tier).toBe(3);
      expect(body.categories).toEqual(["companion"]);
    });

    it("handles start failure", async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500 } as Response);
      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.startRun(1);
      });

      expect(result.current.runStatus).toBe("failed");
      expect(result.current.error).toBeTruthy();
    });
  });

  describe("appendCase", () => {
    it("sends POST to append a case", async () => {
      // appendCase calls POST then fetchDatasets
      mockFetch
        .mockResolvedValueOnce(okJson({ ok: true }))   // append
        .mockResolvedValueOnce(okJson(MOCK_DATASETS));  // fetchDatasets refresh

      const { result } = renderHook(() => useEval());

      let ok: boolean | undefined;
      await act(async () => {
        ok = await result.current.appendCase(1, "routing", { id: "new-case", message: "test" });
      });

      expect(ok).toBe(true);
      const [url, opts] = mockFetch.mock.calls[0];
      expect(url).toContain("/api/eval/datasets/1/routing/cases");
      expect(opts.method).toBe("POST");
    });

    it("returns false on failure", async () => {
      mockFetch.mockResolvedValueOnce(errorJson({ error: "Invalid case" }));
      const { result } = renderHook(() => useEval());

      let ok: boolean | undefined;
      await act(async () => {
        ok = await result.current.appendCase(1, "routing", {});
      });

      expect(ok).toBe(false);
    });
  });

  describe("updateCase", () => {
    it("sends PUT to update a case", async () => {
      mockFetch.mockResolvedValueOnce(okJson({ ok: true }));
      const { result } = renderHook(() => useEval());

      let ok: boolean | undefined;
      await act(async () => {
        ok = await result.current.updateCase(1, "routing", "case-1", { id: "case-1", message: "updated" });
      });

      expect(ok).toBe(true);
      const [url, opts] = mockFetch.mock.calls[0];
      expect(url).toContain("/api/eval/datasets/1/routing/cases/case-1");
      expect(opts.method).toBe("PUT");
    });
  });

  describe("deleteCase", () => {
    it("sends DELETE and refreshes datasets", async () => {
      mockFetch
        .mockResolvedValueOnce(okJson({ ok: true }))   // delete
        .mockResolvedValueOnce(okJson(MOCK_DATASETS));  // fetchDatasets

      const { result } = renderHook(() => useEval());

      let ok: boolean | undefined;
      await act(async () => {
        ok = await result.current.deleteCase(1, "routing", "case-1");
      });

      expect(ok).toBe(true);
      const [url, opts] = mockFetch.mock.calls[0];
      expect(url).toContain("/api/eval/datasets/1/routing/cases/case-1");
      expect(opts.method).toBe("DELETE");
    });
  });

  describe("compareRuns", () => {
    it("fetches comparison data", async () => {
      const compData = {
        base_run_id: "run-001",
        target_run_id: "run-002",
        overall_delta: 0.05,
        has_regressions: false,
        regressions: [],
        improvements: [],
        unchanged: 150,
      };
      mockFetch.mockResolvedValueOnce(okJson(compData));
      const { result } = renderHook(() => useEval());

      let data: unknown;
      await act(async () => {
        data = await result.current.compareRuns("run-001", "run-002");
      });

      expect(data).toEqual(compData);
      const fetchCall = mockFetch.mock.calls[0][0] as string;
      expect(fetchCall).toContain("base=run-001");
      expect(fetchCall).toContain("target=run-002");
    });
  });

  describe("state management", () => {
    it("clears progress and caseRunStatus on new run", async () => {
      mockFetch.mockResolvedValueOnce(okJson({ run_id: "run-fresh" }));
      mockFetch.mockResolvedValue(okJson({ status: "running" }));

      const { result } = renderHook(() => useEval());

      await act(async () => {
        await result.current.startRun(1);
      });

      expect(result.current.progress).toEqual([]);
      expect(result.current.caseRunStatus).toEqual([]);
    });

    it("can set error and activeResult directly", async () => {
      const { result } = renderHook(() => useEval());

      act(() => {
        result.current.setError("custom error");
      });
      expect(result.current.error).toBe("custom error");

      act(() => {
        result.current.setActiveResult({ custom: true });
      });
      expect(result.current.activeResult).toEqual({ custom: true });
    });
  });
});
