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
  onAddTask?: (anchor: HTMLElement) => void;
  children: React.ReactNode;
}

export function TaskColumn({ id, name, tasks, droppable, onAddTask, children }: TaskColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id,
    disabled: droppable === false,
  });

  return (
    <div className="flex min-w-[200px] flex-1 basis-0 flex-col">
      <div className="mb-2.5 flex items-baseline gap-2 px-2">
        <span className="text-[13px] font-semibold text-foreground">
          {name}
        </span>
        <span className="text-[11px] tabular-nums text-muted-foreground/80">{tasks.length}</span>
        {onAddTask && (
          <button
            onClick={(event) => onAddTask(event.currentTarget)}
            aria-label={`Add task to ${name}`}
            className={cn(
              "ml-auto flex items-center gap-1 rounded-md px-2 py-1.5 text-xs",
              "text-muted-foreground/70 hover:bg-muted hover:text-foreground",
            )}
          >
            <RiAddLine className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <SortableContext
        items={tasks.map((t) => t.conversation_id)}
        strategy={verticalListSortingStrategy}
      >
        <div
          ref={setNodeRef}
          className={cn(
            "flex min-h-24 flex-1 flex-col gap-2 rounded-xl p-1.5 bg-foreground/[0.025] transition-colors",
            isOver && droppable !== false && "bg-foreground/[0.05]",
            droppable === false && "opacity-40",
          )}
        >
          {children}
        </div>
      </SortableContext>
    </div>
  );
}
