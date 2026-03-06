"use client";

import { useGrimStore } from "@/store";
import type { SlotInfo } from "@/lib/poolTypes";

const EMPTY_SLOTS: SlotInfo[] = [];

function SlotBadge({ slot }: { slot: SlotInfo }) {
  const navigateToJob = useGrimStore((s) => s.navigateToJob);

  return (
    <button
      onClick={() => slot.current_job_id && navigateToJob(slot.current_job_id)}
      disabled={!slot.current_job_id}
      className={`
        flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors
        ${slot.busy
          ? "border-green-400/40 bg-green-400/5 hover:bg-green-400/10 cursor-pointer"
          : "border-grim-border bg-grim-surface cursor-default"
        }
      `}
    >
      <div className={`w-2 h-2 rounded-full shrink-0 ${slot.busy ? "bg-green-400 animate-pulse" : "bg-grim-border"}`} />
      <span className="text-[11px] font-mono text-grim-text-dim">{slot.slot_id}</span>
      {slot.current_job_id && (
        <span className="text-[10px] font-mono text-grim-accent truncate max-w-[100px]">
          {slot.current_job_id}
        </span>
      )}
    </button>
  );
}

export function SlotGrid() {
  const slots = useGrimStore((s) => s.poolStatus?.slots ?? EMPTY_SLOTS);

  if (slots.length === 0) {
    return null;
  }

  return (
    <div>
      <div className="text-[10px] text-grim-text-dim uppercase tracking-wider mb-1.5">Agent Slots</div>
      <div className="flex gap-2 flex-wrap">
        {slots.map((slot) => (
          <SlotBadge key={slot.slot_id} slot={slot} />
        ))}
      </div>
    </div>
  );
}
