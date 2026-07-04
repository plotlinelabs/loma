"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  RiPlayLine,
  RiCheckLine,
  RiMoreLine,
  RiArrowGoBackLine,
  RiDeleteBinLine,
  RiLogoutBoxRLine,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import ClientTimestamp from "@/components/ClientTimestamp";
import type { BoardLane, Task } from "@/lib/api";

const columnDotStyles: Record<string, string> = {
  working: "bg-blue-500 animate-pulse",
  needs_input: "bg-amber-500",
  done: "bg-emerald-500",
};

interface TaskCardProps {
  task: Task;
  lanes: BoardLane[];
  onOpen: (task: Task) => void;
  onStart: (task: Task) => void;
  onMarkDone: (task: Task) => void;
  onReopen: (task: Task) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onRemoveFromBoard: (task: Task) => void;
  onDeleteDraft: (task: Task) => void;
}

export function TaskCard({
  task, lanes, onOpen, onStart, onMarkDone, onReopen,
  onMoveToLane, onRemoveFromBoard, onDeleteDraft,
}: TaskCardProps) {
  const isStaged = task.task_status === "todo";
  const isDraft = isStaged && !task.status;
  const isParked = isStaged && !!task.status;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.conversation_id, data: { task } });

  const isError = task.column === "needs_input" && (task.status === "error" || task.status === "interrupted");
  const dot = isError
    ? "bg-red-500"
    : isParked
      ? "bg-muted-foreground/40" // parked chat: paused, not demanding attention
      : columnDotStyles[task.column];
  const timestamp =
    task.column === "working" ? task.started_at
    : task.column === "needs_input" ? task.finished_at
    : task.column === "done" ? task.task_done_at
    : task.task_staged_at;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      onClick={() => onOpen(task)}
      className={cn(
        "group rounded-md border bg-card px-3 py-2 cursor-pointer",
        "hover:bg-muted/50 touch-none select-none",
        isDragging && "opacity-40",
      )}
    >
      <div className="flex items-start gap-2">
        {dot && <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px]">
            {task.title || task.prompt}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            <ClientTimestamp iso={timestamp} variant="short" placeholder="—" />
            {task.total_turns > 0 && <span>{task.total_turns} turns</span>}
          </div>
        </div>
        <div
          className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {isDraft && (
            <Button
              variant="ghost" size="icon" className="h-6 w-6"
              title="Start"
              onClick={() => onStart(task)}
            >
              <RiPlayLine className="h-3.5 w-3.5" />
            </Button>
          )}
          {(task.column === "working" || task.column === "needs_input" || isParked) && (
            <Button
              variant="ghost" size="icon" className="h-6 w-6"
              title="Mark done"
              onClick={() => onMarkDone(task)}
            >
              <RiCheckLine className="h-3.5 w-3.5" />
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-6 w-6">
                <RiMoreLine className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {(() => {
                // Staged cards move between other lanes; needs-input cards
                // park into any lane to be recontinued later.
                const targetLanes = isStaged
                  ? lanes.filter((lane) => lane.id !== task.task_lane)
                  : task.column === "needs_input" ? lanes : [];
                return targetLanes.length > 0 && (
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>Move to</DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      {targetLanes.map((lane) => (
                        <DropdownMenuItem key={lane.id} onClick={() => onMoveToLane(task, lane.id)}>
                          {lane.name}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                );
              })()}
              {task.column === "done" && (
                <DropdownMenuItem onClick={() => onReopen(task)}>
                  <RiArrowGoBackLine className="h-3.5 w-3.5" />
                  Reopen
                </DropdownMenuItem>
              )}
              {!isDraft && (
                <DropdownMenuItem onClick={() => onRemoveFromBoard(task)}>
                  <RiLogoutBoxRLine className="h-3.5 w-3.5" />
                  Remove from board
                </DropdownMenuItem>
              )}
              {isDraft && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onClick={() => onDeleteDraft(task)}>
                    <RiDeleteBinLine className="h-3.5 w-3.5" />
                    Delete
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </div>
  );
}
