"use client";

import { RiCheckLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Task, TaskPriority } from "@/lib/api";
import { TASK_PRIORITIES, priorityDisplay } from "./taskDisplay";

/** Menu items for picking a task priority — shared by the card tag's own
 * dropdown and the Priority submenu in the card actions menu. */
export function PriorityMenuItems({ task, onSetPriority }: {
  task: Task;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
}) {
  return (
    <>
      {TASK_PRIORITIES.map((priority) => (
        <DropdownMenuItem key={priority.id} onClick={() => onSetPriority(task, priority.id)}>
          <span className="w-4 text-center">{priority.symbol}</span>
          <span className="flex-1">{priority.label}</span>
          {task.task_priority === priority.id && <RiCheckLine />}
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => onSetPriority(task, null)}>
        <span className="w-4 text-center">—</span>
        <span className="flex-1">No priority</span>
        {!task.task_priority && <RiCheckLine />}
      </DropdownMenuItem>
    </>
  );
}

/** The clickable priority tag on a task card. Always rendered so priority is
 * settable in one click; unset shows a muted "Priority" affordance. */
export function TaskPriorityTag({ task, onSetPriority }: {
  task: Task;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
}) {
  const display = priorityDisplay(task.task_priority);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title="Set priority"
          // Keep the card's click (open) and pointerdown (drag) handlers out.
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          className={cn(
            "shrink-0 rounded border px-1.5 py-0.5 text-[10px] leading-4 cursor-pointer",
            "hover:bg-muted",
            display
              ? "border-transparent bg-muted text-muted-foreground"
              : "border-dashed text-muted-foreground/60",
          )}
        >
          {display ? `${display.symbol} ${display.label}` : "Priority"}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start" className="w-40"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <PriorityMenuItems task={task} onSetPriority={onSetPriority} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
