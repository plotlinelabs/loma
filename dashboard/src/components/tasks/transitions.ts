import type { Task } from "@/lib/api";

/**
 * The board's allowed-transition matrix. The backend PATCH handler enforces
 * the same rules authoritatively — keep the two in sync.
 *
 * Columns: any staging lane id, "working", "needs_input" (Needs you: blocked
 * on someone), "review" (Ready to review: finished), "done".
 * Staging lanes hold two kinds of cards: *drafts* (never run) and *parked*
 * chats (started, shelved to recontinue later).
 *
 * | From \ To         | staged lane  | working            | needs_input / review | done        |
 * |-------------------|--------------|--------------------|----------------------|-------------|
 * | staged lane       | yes          | draft only (=start)| no                   | parked only |
 * | working           | no           | —                  | (derived)            | yes         |
 * | needs_input/review| yes (=park)  | (by replying)      | (derived)            | yes         |
 * | done              | yes (=park)  | no                 | yes (=reopen)        | —           |
 */
export function canMove(task: Task, from: string, to: string, laneIds: string[]): boolean {
  // A starred card is your own bookmark: it moves freely between your lanes
  // and Done. It sits in Running / Needs you / Ready to review while the real
  // task does (see starLiveColumn); moving it to a lane from there parks it
  // until the task moves on. Those columns themselves are never drop targets.
  if (task.star) return from === to || to === "done" || laneIds.includes(to);
  if (task.human_task) return false;
  if (from === to) return true; // reorder within any column
  const fromStaged = laneIds.includes(from);
  const toStaged = laneIds.includes(to);
  const isDraft = !task.status;

  if (fromStaged) {
    if (toStaged) return true;
    // Starting sends the draft's details — a title-only draft has nothing to
    // send. Parked chats restart by replying in the chat.
    if (to === "working") return isDraft && !!task.prompt.trim();
    if (to === "done") return !isDraft;
    return false;
  }
  if (from === "needs_input" || from === "review") return to === "done" || toStaged; // park to recontinue later
  if (from === "working") return to === "done";
  if (from === "done") return isStoppedColumn(to) || toStaged; // reopen or park back
  return false;
}

/** The real-task column a starred card follows on your board, if any.
 * None while you have it parked in one of your lanes. */
export function starLiveColumn(task: Task): "working" | "needs_input" | "review" | null {
  if (task.star?.parked) return null;
  const source = task.star?.source_column;
  return source === "working" || source === "needs_input" || source === "review" ? source : null;
}

/** Best guess at a reopened task's column until the next poll: the server
 * only knows why a task waits while it is active. */
export function reopenedColumn(task: Task): "working" | "needs_input" | "review" {
  if (task.status === "running" || task.status === "queued") return "working";
  return task.status === "error" || task.status === "interrupted" ? "needs_input" : "review";
}

/** Columns a stopped (not running, not done) task lives in. */
export function isStoppedColumn(column: string): column is "needs_input" | "review" {
  return column === "needs_input" || column === "review";
}

/** Midpoint rank for dropping between two neighbors in a column. */
export function rankBetween(before: number | null, after: number | null): number {
  // Empty column: negated epoch *seconds* — same scale as the backend's
  // recency fallback so defaults and manual ranks stay comparable.
  if (before === null && after === null) return -Date.now() / 1000;
  if (before === null) return (after as number) - 1;
  if (after === null) return before + 1;
  return (before + after) / 2;
}
