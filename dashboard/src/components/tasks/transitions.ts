import type { Task } from "@/lib/api";

/**
 * The board's allowed-transition matrix. The backend PATCH handler enforces
 * the same rules authoritatively — keep the two in sync.
 *
 * Columns: any staging lane id, "working", "needs_input", "done".
 *
 * | From \ To    | staged lane | working      | needs_input | done |
 * |--------------|-------------|--------------|-------------|------|
 * | staged lane  | yes         | yes (=start) | no          | no   |
 * | working      | no          | —            | (derived)   | yes  |
 * | needs_input  | no          | (by replying)| —           | yes  |
 * | done         | no          | no           | no          | —    |
 */
export function canMove(task: Task, from: string, to: string, laneIds: string[]): boolean {
  if (from === to) return laneIds.includes(from); // same-lane reorder only
  const fromStaged = laneIds.includes(from);
  const toStaged = laneIds.includes(to);

  if (fromStaged) return toStaged || to === "working";
  if (from === "working" || from === "needs_input") return to === "done";
  return false; // done: reopen via context menu only
}

/** Midpoint rank for dropping between two neighbors in a staged lane. */
export function rankBetween(before: number | null, after: number | null): number {
  if (before === null && after === null) return -Date.now();
  if (before === null) return (after as number) - 1;
  if (after === null) return before + 1;
  return (before + after) / 2;
}
