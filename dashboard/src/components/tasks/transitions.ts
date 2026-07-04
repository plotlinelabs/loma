import type { Task } from "@/lib/api";

/**
 * The board's allowed-transition matrix. The backend PATCH handler enforces
 * the same rules authoritatively — keep the two in sync.
 *
 * Columns: any staging lane id, "working", "needs_input", "done".
 * Staging lanes hold two kinds of cards: *drafts* (never run) and *parked*
 * chats (started, shelved to recontinue later).
 *
 * | From \ To    | staged lane      | working            | needs_input | done          |
 * |--------------|------------------|--------------------|-------------|---------------|
 * | staged lane  | yes              | draft only (=start)| no          | parked only   |
 * | working      | no               | —                  | (derived)   | yes           |
 * | needs_input  | yes (=park)      | (by replying)      | —           | yes           |
 * | done         | no               | no                 | no          | — (reopen via menu) |
 */
export function canMove(task: Task, from: string, to: string, laneIds: string[]): boolean {
  if (from === to) return true; // reorder within any column
  const fromStaged = laneIds.includes(from);
  const toStaged = laneIds.includes(to);
  const isDraft = !task.status;

  if (fromStaged) {
    if (toStaged) return true;
    if (to === "working") return isDraft; // parked chats restart by replying in the chat
    if (to === "done") return !isDraft;
    return false;
  }
  if (from === "needs_input") return to === "done" || toStaged; // park to recontinue later
  if (from === "working") return to === "done";
  return false; // done: reopen via context menu only
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
