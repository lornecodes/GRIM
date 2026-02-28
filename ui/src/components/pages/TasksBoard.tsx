"use client";

import { IconTasks } from "@/components/icons/NavIcons";

export function TasksBoard() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4">
      <IconTasks size={48} className="text-grim-accent opacity-40" />
      <h2 className="text-sm font-semibold text-grim-text">Tasks</h2>
      <p className="text-xs text-grim-text-dim max-w-xs text-center">
        Task board with kanban columns, priorities, and assignment tracking. Coming soon.
      </p>
    </div>
  );
}
