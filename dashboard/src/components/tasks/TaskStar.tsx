"use client";

import { RiStarFill, RiStarLine, RiTeamLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import type { Task } from "@/lib/api";

/** Star toggle for a task on a shared board. A star is private: the task
 * also shows on your own board, where you can organize it your way. */
export function StarButton({ task, onToggle, className, iconClassName = "h-3.5 w-3.5" }: {
  task: Task;
  onToggle: (task: Task) => void;
  className?: string;
  iconClassName?: string;
}) {
  const starred = !!task.starred;
  const label = starred ? "Remove star (takes it off My tasks)" : "Star (adds it to My tasks, only you see this)";
  return (
    <button
      type="button"
      title={label}
      aria-label={starred ? "Remove star" : "Star task"}
      aria-pressed={starred}
      onClick={(e) => { e.stopPropagation(); onToggle(task); }}
      onPointerDown={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
        starred && "text-amber-500 hover:text-amber-600",
        className,
      )}
    >
      {starred ? <RiStarFill className={iconClassName} /> : <RiStarLine className={iconClassName} />}
    </button>
  );
}

/** Where a starred task really is on its board, when that is worth knowing. */
export function starStatus(task: Task): { text: string; className: string } | null {
  switch (task.star?.source_column) {
    case "working": return { text: "Running", className: "bg-blue-500/10 text-blue-600 dark:text-blue-400" };
    case "needs_input": return { text: "Needs input", className: "bg-amber-500/10 text-amber-700 dark:text-amber-400" };
    case "done": return { text: "Done on board", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" };
    default: return null;
  }
}

/** Chips on a starred card in My tasks: the board (and card) it comes from,
 * plus its real status there. */
export function StarSource({ task }: { task: Task }) {
  if (!task.star) return null;
  const status = starStatus(task);
  const source = [task.star.board_name, task.star.card_title].filter(Boolean).join(" · ");
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1 text-[10px] leading-4">
      <span title={`Starred from ${source}`}
        className="inline-flex min-w-0 max-w-full items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
        <RiTeamLine className="h-3 w-3 shrink-0" />
        <span className="truncate">{source}</span>
      </span>
      {status && <span className={cn("shrink-0 rounded px-1.5 py-0.5 font-medium", status.className)}>{status.text}</span>}
    </div>
  );
}
