"use client";

import { useState, useMemo } from "react";
import { IconCalendar } from "@/components/icons/NavIcons";
import { useCalendar, type CalendarEntry } from "@/hooks/useCalendar";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pad(n: number) {
  return n.toString().padStart(2, "0");
}

function toDateStr(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function getMonthRange(year: number, month: number) {
  const start = new Date(year, month, 1);
  const end = new Date(year, month + 1, 0);
  return { start: toDateStr(start), end: toDateStr(end) };
}

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay();
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const DAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const STATUS_COLORS: Record<string, string> = {
  active: "bg-blue-500/20 border-l-blue-400",
  in_progress: "bg-yellow-500/20 border-l-yellow-400",
  resolved: "bg-green-500/20 border-l-green-400",
  new: "bg-gray-500/20 border-l-gray-400",
  personal: "bg-purple-500/20 border-l-purple-400",
};

// ---------------------------------------------------------------------------
// Event chip
// ---------------------------------------------------------------------------

function EventChip({ entry }: { entry: CalendarEntry }) {
  const isPersonal = !!entry.id && !entry.story_id;
  const statusKey = isPersonal ? "personal" : (entry.status ?? "new");
  const colorClass = STATUS_COLORS[statusKey] ?? STATUS_COLORS.new;

  return (
    <div
      className={`text-[8px] px-1.5 py-0.5 rounded border-l-2 truncate ${colorClass}`}
      title={`${entry.title}${entry.time ? ` at ${entry.time}` : ""}${entry.estimate_days ? ` (${entry.estimate_days}d)` : ""}`}
    >
      <span className="text-grim-text">{entry.title}</span>
      {entry.time && (
        <span className="text-grim-text-dim ml-1">{entry.time}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add event form
// ---------------------------------------------------------------------------

function AddEventForm({
  onAdd,
  onClose,
  defaultDate,
}: {
  onAdd: (args: { title: string; date: string; time?: string; notes?: string }) => void;
  onClose: () => void;
  defaultDate: string;
}) {
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(defaultDate);
  const [time, setTime] = useState("");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-sm w-full mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-grim-text mb-4">Add Event</h3>
        <div className="space-y-3">
          <input
            type="text"
            placeholder="Event title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text placeholder-grim-text-dim focus:outline-none focus:border-grim-accent"
          />
          <div className="flex gap-2">
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="flex-1 text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            />
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              className="w-28 text-[11px] px-3 py-2 rounded-lg bg-grim-bg border border-grim-border text-grim-text focus:outline-none focus:border-grim-accent"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={onClose}
              className="text-[10px] px-3 py-1.5 rounded bg-grim-border/30 text-grim-text-dim hover:text-grim-text transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                if (title.trim() && date) {
                  onAdd({ title: title.trim(), date, time: time || undefined });
                  onClose();
                }
              }}
              disabled={!title.trim()}
              className="text-[10px] px-3 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim transition-colors disabled:opacity-30"
            >
              Add
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CalendarView() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());
  const [showAdd, setShowAdd] = useState(false);
  const [selectedDate, setSelectedDate] = useState("");

  const range = useMemo(() => getMonthRange(year, month), [year, month]);
  const { calendar, loading, error, addEvent, syncSchedule, refresh } = useCalendar(
    range.start,
    range.end
  );

  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfWeek(year, month);
  const todayStr = toDateStr(now);

  // Index entries by date for fast lookup
  const entriesByDate = useMemo(() => {
    const map: Record<string, CalendarEntry[]> = {};
    if (!calendar?.entries) return map;
    for (const entry of calendar.entries) {
      // Work entries have start_date/end_date range, personal have date
      if (entry.date) {
        (map[entry.date] ??= []).push(entry);
      }
      if (entry.start_date) {
        // Expand date range
        const start = new Date(entry.start_date);
        const end = entry.end_date ? new Date(entry.end_date) : start;
        for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
          const ds = toDateStr(d);
          (map[ds] ??= []).push(entry);
        }
      }
    }
    return map;
  }, [calendar]);

  const prevMonth = () => {
    if (month === 0) { setMonth(11); setYear(year - 1); }
    else setMonth(month - 1);
  };

  const nextMonth = () => {
    if (month === 11) { setMonth(0); setYear(year + 1); }
    else setMonth(month + 1);
  };

  const totalEntries = calendar?.entries?.length ?? 0;

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconCalendar size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">Calendar</h2>
          <p className="text-xs text-grim-text-dim">
            {totalEntries} events in {MONTH_NAMES[month]} {year}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={syncSchedule}
            className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
            title="Sync work schedule from board"
          >
            Sync
          </button>
          <button
            onClick={() => { setSelectedDate(todayStr); setShowAdd(true); }}
            className="text-[10px] px-2 py-1 rounded bg-grim-accent/20 border border-grim-accent/30 text-grim-accent hover:bg-grim-accent/30 transition-colors"
          >
            + Event
          </button>
          <button
            onClick={refresh}
            className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Month navigation */}
      <div className="flex items-center justify-center gap-4">
        <button
          onClick={prevMonth}
          className="text-grim-text-dim hover:text-grim-text text-sm px-2 transition-colors"
        >
          &lt;
        </button>
        <h3 className="text-sm font-medium text-grim-text min-w-[160px] text-center">
          {MONTH_NAMES[month]} {year}
        </h3>
        <button
          onClick={nextMonth}
          className="text-grim-text-dim hover:text-grim-text text-sm px-2 transition-colors"
        >
          &gt;
        </button>
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">
          Loading calendar...
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="text-xs text-red-400 py-4 text-center">
          {error}
          <button onClick={refresh} className="ml-2 underline hover:text-red-300">retry</button>
        </div>
      )}

      {/* Calendar grid */}
      {!loading && (
        <div className="border border-grim-border rounded-lg overflow-hidden">
          {/* Day headers */}
          <div className="grid grid-cols-7 bg-grim-surface">
            {DAY_HEADERS.map((d) => (
              <div
                key={d}
                className="text-[9px] font-medium text-grim-text-dim text-center py-2 border-b border-grim-border"
              >
                {d}
              </div>
            ))}
          </div>

          {/* Day cells */}
          <div className="grid grid-cols-7">
            {/* Empty cells before first day */}
            {Array.from({ length: firstDay }).map((_, i) => (
              <div key={`empty-${i}`} className="h-24 border-b border-r border-grim-border/30 bg-grim-bg/30" />
            ))}

            {/* Day cells */}
            {Array.from({ length: daysInMonth }).map((_, i) => {
              const day = i + 1;
              const dateStr = `${year}-${pad(month + 1)}-${pad(day)}`;
              const isToday = dateStr === todayStr;
              const dayEntries = entriesByDate[dateStr] ?? [];
              const isWeekend = (firstDay + i) % 7 === 0 || (firstDay + i) % 7 === 6;

              return (
                <div
                  key={day}
                  className={`h-24 border-b border-r border-grim-border/30 p-1 overflow-hidden cursor-pointer transition-colors ${
                    isToday
                      ? "bg-grim-accent/5 border-grim-accent/20"
                      : isWeekend
                      ? "bg-grim-bg/50"
                      : "bg-grim-bg hover:bg-grim-surface-hover"
                  }`}
                  onClick={() => { setSelectedDate(dateStr); setShowAdd(true); }}
                >
                  <div className={`text-[10px] mb-1 ${
                    isToday
                      ? "text-grim-accent font-bold"
                      : "text-grim-text-dim"
                  }`}>
                    {day}
                  </div>
                  <div className="space-y-0.5">
                    {dayEntries.slice(0, 3).map((entry, idx) => (
                      <EventChip key={`${entry.id ?? entry.story_id ?? idx}`} entry={entry} />
                    ))}
                    {dayEntries.length > 3 && (
                      <div className="text-[7px] text-grim-text-dim text-center">
                        +{dayEntries.length - 3} more
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex gap-4 justify-center text-[8px] text-grim-text-dim">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-blue-500/40" /> Active
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-yellow-500/40" /> In Progress
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-green-500/40" /> Resolved
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-purple-500/40" /> Personal
        </span>
      </div>

      {/* Add event modal */}
      {showAdd && (
        <AddEventForm
          defaultDate={selectedDate}
          onAdd={addEvent}
          onClose={() => setShowAdd(false)}
        />
      )}
    </div>
  );
}
