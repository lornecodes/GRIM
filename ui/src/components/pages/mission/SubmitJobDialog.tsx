"use client";

import { useState, useCallback } from "react";
import type { JobType, JobPriority } from "@/lib/poolTypes";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
}

const JOB_TYPES: { value: JobType; label: string }[] = [
  { value: "code", label: "Code" },
  { value: "research", label: "Research" },
  { value: "audit", label: "Audit" },
  { value: "plan", label: "Plan" },
  { value: "index", label: "Index" },
];

const PRIORITIES: { value: JobPriority; label: string }[] = [
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

export function SubmitJobDialog({ open, onClose, onSubmitted }: Props) {
  const [jobType, setJobType] = useState<JobType>("code");
  const [priority, setPriority] = useState<JobPriority>("normal");
  const [instructions, setInstructions] = useState("");
  const [fdoIds, setFdoIds] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const handleSubmit = useCallback(async () => {
    if (!instructions.trim()) return;
    setSubmitting(true);
    setError(null);

    try {
      const body: Record<string, unknown> = {
        job_type: jobType,
        priority,
        instructions: instructions.trim(),
      };
      if (fdoIds.trim()) {
        body.kronos_fdo_ids = fdoIds.split(",").map((s) => s.trim()).filter(Boolean);
      }

      const resp = await fetch(`${apiBase}/api/pool/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${resp.status}`);
      }

      setInstructions("");
      setFdoIds("");
      onClose();
      onSubmitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to submit");
    } finally {
      setSubmitting(false);
    }
  }, [apiBase, jobType, priority, instructions, fdoIds, onClose, onSubmitted]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-grim-surface border border-grim-border rounded-xl p-5 w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-medium text-grim-text mb-4">Submit Job</div>

        {/* Job Type */}
        <div className="mb-3">
          <label className="text-[10px] text-grim-text-dim uppercase tracking-wider block mb-1">Type</label>
          <div className="flex gap-1.5">
            {JOB_TYPES.map((t) => (
              <button
                key={t.value}
                onClick={() => setJobType(t.value)}
                className={`text-[11px] px-2.5 py-1 rounded border transition-colors ${
                  jobType === t.value
                    ? "border-grim-accent bg-grim-accent/10 text-grim-accent"
                    : "border-grim-border text-grim-text-dim hover:text-grim-text"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Priority */}
        <div className="mb-3">
          <label className="text-[10px] text-grim-text-dim uppercase tracking-wider block mb-1">Priority</label>
          <div className="flex gap-1.5">
            {PRIORITIES.map((p) => (
              <button
                key={p.value}
                onClick={() => setPriority(p.value)}
                className={`text-[11px] px-2.5 py-1 rounded border transition-colors ${
                  priority === p.value
                    ? "border-grim-accent bg-grim-accent/10 text-grim-accent"
                    : "border-grim-border text-grim-text-dim hover:text-grim-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Instructions */}
        <div className="mb-3">
          <label className="text-[10px] text-grim-text-dim uppercase tracking-wider block mb-1">Instructions</label>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="What should the agent do?"
            rows={4}
            className="w-full bg-grim-bg border border-grim-border rounded-md px-3 py-2 text-[12px] text-grim-text font-mono resize-y placeholder:text-grim-text-dim/50 focus:outline-none focus:border-grim-accent"
          />
        </div>

        {/* Kronos FDO IDs (optional) */}
        <div className="mb-4">
          <label className="text-[10px] text-grim-text-dim uppercase tracking-wider block mb-1">
            Kronos FDO IDs <span className="text-grim-text-dim">(optional, comma-separated)</span>
          </label>
          <input
            type="text"
            value={fdoIds}
            onChange={(e) => setFdoIds(e.target.value)}
            placeholder="e.g. pac-comprehensive, grim-architecture"
            className="w-full bg-grim-bg border border-grim-border rounded-md px-3 py-1.5 text-[12px] text-grim-text font-mono placeholder:text-grim-text-dim/50 focus:outline-none focus:border-grim-accent"
          />
        </div>

        {/* Error */}
        {error && (
          <div className="text-[11px] text-red-400 mb-3">{error}</div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="text-[11px] px-3 py-1.5 rounded border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !instructions.trim()}
            className="text-[11px] px-3 py-1.5 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? "Submitting..." : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}
