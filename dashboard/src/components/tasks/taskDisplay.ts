import type { Task, TaskPriority } from "@/lib/api";

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

/** Priority levels in menu order (most urgent first). Text + symbol only —
 * no color coding by design. */
export const TASK_PRIORITIES: Array<{ id: TaskPriority; symbol: string; label: string }> = [
  { id: "urgent", symbol: "\u203c", label: "Urgent" },
  { id: "high", symbol: "\u2191", label: "High" },
  { id: "medium", symbol: "\u2192", label: "Medium" },
  { id: "low", symbol: "\u2193", label: "Low" },
];

export function priorityDisplay(priority: TaskPriority | null | undefined) {
  return TASK_PRIORITIES.find((p) => p.id === priority) ?? null;
}

export function taskTimestamp(task: Task): string | null {
  return task.column === "working" ? task.started_at
    : task.column === "needs_input" ? task.finished_at
    : task.column === "done" ? task.task_done_at
    : task.task_staged_at;
}

// ── Deadlines ────────────────────────────────────────────────────────────────

/** Parse a "YYYY-MM-DD" deadline into a local-midnight Date. */
export function parseDeadline(deadline: string | null | undefined): Date | null {
  if (!deadline) return null;
  const [y, m, d] = deadline.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

/** Format a Date as a "YYYY-MM-DD" deadline string (local calendar date). */
export function toDeadlineString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Card display shape for a task deadline. `urgent` flags deadlines less than
 * 2 days away (due tomorrow, today, or overdue) — rendered in red. */
export function deadlineDisplay(deadline: string | null | undefined) {
  const date = parseDeadline(deadline);
  if (!date) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const daysLeft = Math.round((date.getTime() - today.getTime()) / 86_400_000);
  const remainingLabel =
    daysLeft < 0 ? `${-daysLeft}d overdue`
    : daysLeft === 0 ? "due today"
    : `${daysLeft}d left`;
  return {
    date,
    daysLeft,
    dateLabel: date.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    fullDate: date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
    remainingLabel,
    urgent: daysLeft < 2,
  };
}
