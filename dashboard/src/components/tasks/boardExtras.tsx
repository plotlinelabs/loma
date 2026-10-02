"use client";

import { createContext, useContext } from "react";
import { cn } from "@/lib/utils";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CardFilter, CardFilterMatch, Task, TaskBoardRole, TaskBoardSummary } from "@/lib/api";

/** Board-wide state the task components read without prop drilling. */
export interface BoardExtras {
  myEmail: string | null;
  /** Caller's role on the current board. */
  role: TaskBoardRole | null;
  /** Shared boards only: owners/editors a task can be assigned to. */
  assignable: string[];
  /** "Assigned to me" filter is on. */
  assignedToMe: boolean;
  /** Card boards: field filters (from the Filter menu or a saved view). */
  cardFilters: CardFilter[];
  filterMatch: CardFilterMatch;
  /** Opens the "Move to board" picker for one of your tasks. */
  onMoveToBoard?: (task: Task) => void;
}

export const BoardExtrasContext = createContext<BoardExtras>({
  myEmail: null, role: null, assignable: [], assignedToMe: false, cardFilters: [], filterMatch: "all",
});

export const useBoardExtras = () => useContext(BoardExtrasContext);

/** People who can be assigned (and so run tasks): the creator plus owners and editors. */
export function assignablePeople(board: TaskBoardSummary | undefined): string[] {
  if (!board?.shared) return [];
  const people = [board.owner, ...board.members.filter((m) => m.role !== "viewer").map((m) => m.email)];
  return Array.from(new Set(people.filter(Boolean)));
}

export function personInitials(email: string): string {
  const name = email.split("@")[0] ?? "";
  const parts = name.split(/[._-]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}

/** Small round initials badge for a task's assignee. */
export function AssigneeBadge({ email, me, className }: { email: string; me?: string | null; className?: string }) {
  const isMe = !!me && me === email;
  return (
    <span
      title={`Assigned to ${isMe ? "you" : email}`}
      aria-label={`Assigned to ${isMe ? "you" : email}`}
      className={cn(
        "inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full px-1 text-[9px] font-semibold",
        isMe ? "bg-brand-600 text-white" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      {personInitials(email)}
    </span>
  );
}

const UNASSIGNED = "__unassigned__";

/** Assignee dropdown for a task on a shared board. */
export function AssigneeSelect({ value, people, me, disabled, onChange, className }: {
  value: string | null | undefined;
  people: string[];
  me: string | null;
  disabled?: boolean;
  onChange: (email: string | null) => void;
  className?: string;
}) {
  // Keep a stale assignee visible until the server clears it.
  const options = value && !people.includes(value) ? [...people, value] : people;
  return (
    <Select value={value || UNASSIGNED} disabled={disabled}
      onValueChange={(next) => onChange(next === UNASSIGNED ? null : next)}>
      <SelectTrigger size="sm" className={cn("w-48", className)} aria-label="Assignee">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
        {options.map((email) => (
          <SelectItem key={email} value={email}>{email === me ? `${email} (you)` : email}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
