"use client";

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
import type { BoardLane, Task, TaskPriority } from "@/lib/api";
import { isDraft as isDraftTask, isStaged as isStagedTask, priorityDisplay, taskDot, taskTimestamp } from "./taskDisplay";
import { PriorityMenuItems, TaskPriorityTag } from "./TaskPriority";

interface MobileTaskCardProps {
  task: Task;
  lanes: BoardLane[];
  onOpen: (task: Task) => void;
  onStart: (task: Task) => void;
  onMarkDone: (task: Task) => void;
  onReopen: (task: Task) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onRemoveFromBoard: (task: Task) => void;
  onDeleteDraft: (task: Task) => void;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
}

/** Touch-first card for the mobile inbox: no drag, actions always visible,
 * larger tap targets. Menus replace drag for moves. */
export function MobileTaskCard({
  task, lanes, onOpen, onStart, onMarkDone, onReopen,
  onMoveToLane, onRemoveFromBoard, onDeleteDraft, onSetPriority,
}: MobileTaskCardProps) {
  const isStaged = isStagedTask(task);
  const isDraft = isDraftTask(task);
  const isParked = isStaged && !isDraft;
  const dot = taskDot(task);
  const timestamp = taskTimestamp(task);

  // Same lane targets as the desktop card: staged cards move between other
  // lanes; needs-input and done cards park into any lane.
  const targetLanes = isStaged
    ? lanes.filter((lane) => lane.id !== task.task_lane)
    : task.column === "needs_input" || task.column === "done" ? lanes : [];

  return (
    <div
      onClick={() => onOpen(task)}
      className="group rounded-md border bg-card px-3 py-2.5 cursor-pointer active:bg-muted/50"
    >
      <div className="flex items-start gap-2">
        {dot && <span className={cn("mt-2 h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1 py-0.5">
          <div className="line-clamp-2 break-words text-[13px]">
            {task.title || task.prompt}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            <ClientTimestamp iso={timestamp} variant="short" placeholder="—" />
            {task.total_turns > 0 && <span>{task.total_turns} turns</span>}
          </div>
          <div className="mt-1 flex items-center gap-1 overflow-hidden">
            <TaskPriorityTag task={task} onSetPriority={onSetPriority} />
          </div>
        </div>
        <div
          className="flex shrink-0 items-center"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {isDraft && !!task.prompt.trim() && (
            <Button
              variant="ghost" size="icon" className="h-8 w-8"
              title="Start"
              onClick={() => onStart(task)}
            >
              <RiPlayLine className="h-4 w-4" />
            </Button>
          )}
          {(task.column === "working" || task.column === "needs_input" || isParked) && (
            <Button
              variant="ghost" size="icon" className="h-8 w-8"
              title="Mark done"
              onClick={() => onMarkDone(task)}
            >
              <RiCheckLine className="h-4 w-4" />
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8">
                <RiMoreLine className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <span className="flex-1">Priority</span>
                  <span className="text-xs text-muted-foreground">{priorityDisplay(task.task_priority)?.label ?? "None"}</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="w-40">
                  <PriorityMenuItems task={task} onSetPriority={onSetPriority} />
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              {targetLanes.length > 0 && (
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
              )}
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
