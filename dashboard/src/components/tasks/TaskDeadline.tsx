"use client";

import { RiCalendarLine, RiDeleteBack2Line } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Calendar } from "@/components/ui/calendar";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import type { Task } from "@/lib/api";
import { deadlineDisplay, parseDeadline, toDeadlineString } from "./taskDisplay";

/** Calendar picker + remove option for the Deadline submenu of the card
 * actions menu — shared by the desktop and mobile cards. */
export function DeadlineMenuItems({ task, onSetDeadline, onPicked }: {
  task: Task;
  onSetDeadline: (task: Task, deadline: string | null) => void;
  /** Called after a date is picked or cleared, to close the menu. */
  onPicked?: () => void;
}) {
  const selected = parseDeadline(task.task_deadline) ?? undefined;
  return (
    <>
      <Calendar
        mode="single"
        selected={selected}
        defaultMonth={selected}
        onSelect={(date) => {
          if (!date) return;
          onSetDeadline(task, toDeadlineString(date));
          onPicked?.();
        }}
      />
      {task.task_deadline && (
        <>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => { onSetDeadline(task, null); onPicked?.(); }}>
            <RiDeleteBack2Line className="h-3.5 w-3.5" /> Remove deadline
          </DropdownMenuItem>
        </>
      )}
    </>
  );
}

/** Deadline badge on a task card: date + days remaining, red when the
 * deadline is less than 2 days away (or already missed). */
export function TaskDeadlineBadge({ task, className }: { task: Task; className?: string }) {
  const display = deadlineDisplay(task.task_deadline);
  if (!display) return null;
  return (
    <span
      title={`Deadline: ${display.fullDate}`}
      className={cn(
        "flex shrink-0 items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] leading-4 text-muted-foreground",
        display.urgent && "bg-destructive/10 text-destructive",
        className,
      )}
    >
      <RiCalendarLine className="h-3 w-3 shrink-0" />
      {display.dateLabel} · {display.remainingLabel}
    </span>
  );
}
