"use client";

import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { RiAddLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import type { Task } from "@/lib/api";

interface TaskColumnProps {
  id: string;
  name: string;
  tasks: Task[];
  /** Whether the current drag can drop here (undefined = no drag active). */
  droppable?: boolean;
  /** Staging lanes only: create a task directly in this lane. */
  onAddTask?: () => void;
  children: React.ReactNode;
}

export function TaskColumn({ id, name, tasks, droppable, onAddTask, children }: TaskColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id,
    disabled: droppable === false,
  });

  return (
    <div className="flex w-64 shrink-0 flex-col">
      <div className="mb-2 flex items-baseline gap-2 px-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {name}
        </span>
        <span className="text-xs text-muted-foreground/70">{tasks.length}</span>
      </div>
      <SortableContext
        items={tasks.map((t) => t.conversation_id)}
        strategy={verticalListSortingStrategy}
      >
        <div
          ref={setNodeRef}
          className={cn(
            "flex min-h-24 flex-1 flex-col gap-1.5 rounded-lg p-1 transition-colors",
            isOver && droppable !== false && "bg-muted/60",
            droppable === false && "opacity-40",
          )}
        >
          {children}
          {onAddTask && (
            <button
              onClick={onAddTask}
              className={cn(
                "flex items-center gap-1 rounded-md px-2 py-1.5 text-xs",
                "text-muted-foreground/70 hover:bg-muted hover:text-foreground",
              )}
            >
              <RiAddLine className="h-3.5 w-3.5" />
              Task
            </button>
          )}
        </div>
      </SortableContext>
    </div>
  );
}
