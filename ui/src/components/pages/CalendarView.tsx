"use client";

import { IconCalendar } from "@/components/icons/NavIcons";

export function CalendarView() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconCalendar size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Calendar</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Timeline view — scheduled tasks, content publishing, and automation events. Coming soon.
      </p>
    </div>
  );
}
