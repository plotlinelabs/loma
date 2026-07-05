import type { Task } from "@/lib/api";

// Shared card display derivations for the desktop TaskCard and MobileTaskCard.

export const columnDotStyles: Record<string, string> = {
  working: "bg-blue-500 animate-pulse",
  needs_input: "bg-amber-500",
  done: "bg-emerald-500",
};

export const isStaged = (task: Task) => task.task_status === "todo";
export const isDraft = (task: Task) => isStaged(task) && !task.status;
export const isParked = (task: Task) => isStaged(task) && !!task.status;

export function taskDot(task: Task): string | undefined {
  const isError =
    task.column === "needs_input" &&
    (task.status === "error" || task.status === "interrupted");
  if (isError) return "bg-red-500";
  if (isParked(task)) return "bg-muted-foreground/40"; // paused, not demanding attention
  return columnDotStyles[task.column];
}

export function taskTimestamp(task: Task): string | null {
  return task.column === "working" ? task.started_at
    : task.column === "needs_input" ? task.finished_at
    : task.column === "done" ? task.task_done_at
    : task.task_staged_at;
}
