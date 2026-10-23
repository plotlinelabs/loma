"use client";

import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  RiArrowGoBackLine,
  RiPlayLine,
  RiCheckLine,
  RiMoreLine,
  RiStarFill,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { AgentModel, BoardLane, Task, TaskPriority, TaskTag } from "@/lib/api";
import { isDraft as isDraftTask, isStaged as isStagedTask, taskDot } from "./taskDisplay";
import { TaskAge, TaskPrChips, TaskSummary, WaitingChip } from "./TaskCardParts";

const CREW_ROLE: Record<string, string> = { cos: "Chief of Staff", pm: "PM", worker: "Worker" };
import { isStoppedColumn } from "./transitions";
import { TaskCardMenu } from "./TaskCardMenu";
import { TaskPriorityTag } from "./TaskPriority";
import { TaskDeadlineBadge } from "./TaskDeadline";
import { AssigneeBadge, useBoardExtras } from "./boardExtras";
import { StarButton, StarSource } from "./TaskStar";

interface TaskCardProps {
  task: Task;
  lanes: BoardLane[];
  tags: TaskTag[];
  models: AgentModel[];
  /** Hide card actions (view-only shared board). */
  readOnly?: boolean;
  onOpen: (task: Task) => void;
  onStart: (task: Task) => void;
  onMarkDone: (task: Task) => void;
  onReopen: (task: Task) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onRemoveFromBoard: (task: Task) => void;
  onDeleteDraft: (task: Task) => void;
  onFork: (task: Task) => void;
  onSetModel: (task: Task, model: string) => void;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
  onSetDeadline: (task: Task, deadline: string | null) => void;
  onSetTags: (task: Task, tagIds: string[]) => void;
  onCreateTag: (task: Task, name: string) => void;
  onToggleStar: (task: Task) => void;
}

export function TaskCard({
  task, lanes, tags, models, readOnly = false, onOpen, onStart, onMarkDone, onReopen,
  onMoveToLane, onRemoveFromBoard, onDeleteDraft, onFork, onSetModel, onSetPriority, onSetDeadline, onSetTags, onCreateTag,
  onToggleStar,
}: TaskCardProps) {
  // On your own board: a task you starred on a shared board. Its actions
  // only organize your copy (see useTaskBoardActions).
  const isStar = !!task.star;
  // Anyone on a shared board can star its tasks, viewers included.
  const canStar = isStar || !!task.task_board_id;
  const isStaged = isStagedTask(task);
  const isDraft = isDraftTask(task);
  const isParked = isStaged && !isDraft;
  const [menuOpen, setMenuOpen] = useState(false);
  const { myEmail } = useBoardExtras();
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.conversation_id, data: { task } });

  const dot = taskDot(task);

  const assignedTags = tags.filter((tag) => (task.task_tag_ids || []).includes(tag.id));
  // Chips only for what is set: no empty "Priority" placeholders.
  const hasChips = (task.starred && !isStar) || (!!task.waiting_reason && task.waiting_reason !== "finished")
    || !!task.task_priority || !!task.task_deadline || assignedTags.length > 0 || !!task.assignee
    || !!task.pull_requests?.length || !!task.fleet;
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      onClick={() => onOpen(task)}
      onContextMenu={(event) => {
        // Your starred cards always get the menu: it only edits your copy.
        if (readOnly && !isStar) return;
        event.preventDefault();
        setMenuOpen(true);
      }}
      className={cn(
        "group relative rounded-lg border border-border bg-card px-4 py-3.5 cursor-pointer",
        "hover:border-input transition-colors touch-none select-none",
        isDragging && "opacity-40",
      )}
    >
      <div className="flex items-start gap-2.5">
        {dot && <span aria-hidden className={cn("mt-[7px] h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <div className="line-clamp-2 min-w-0 flex-1 break-words text-[15px] font-medium leading-[21px]"
              title={task.title || task.prompt || undefined}>
              {task.title || task.prompt || "New task"}
            </div>
            {!isStar && <TaskAge task={task} />}
          </div>
          {isStar ? <div className="mt-2 pr-16"><StarSource task={task} /></div> : <TaskSummary task={task} className="mt-1" />}
          {hasChips && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 pr-16">
              {task.starred && !isStar && <RiStarFill aria-label="Starred" className="h-3.5 w-3.5 shrink-0 text-amber-500" />}
              {task.fleet && (
                <span className="rounded-md bg-muted px-2 py-0.5 text-xs leading-5 text-muted-foreground" data-testid="task-crew-role">
                  {CREW_ROLE[task.fleet.role] ?? "Crew"}
                </span>
              )}
              <WaitingChip task={task} />
              <TaskPrChips task={task} />
              {/* On a starred card this is your own priority, not the shared task's. */}
              <TaskPriorityTag task={task} onSetPriority={onSetPriority} />
              <TaskDeadlineBadge task={task} />
              {assignedTags.slice(0, 2).map((tag) => <span key={tag.id} className="rounded-md bg-muted px-2 py-0.5 text-xs leading-5 text-muted-foreground">{tag.name}</span>)}
              {assignedTags.length > 2 && <span className="text-xs text-muted-foreground">+{assignedTags.length - 2}</span>}
              {task.assignee && <AssigneeBadge email={task.assignee} me={myEmail} />}
            </div>
          )}
        </div>
        {(!readOnly || canStar) && <div
          // Hover-revealed on pointer devices; always visible on touch
          // (hover never fires there) and while focused via keyboard.
          className="absolute bottom-2.5 right-2.5 flex shrink-0 items-center gap-0.5 rounded bg-card opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {canStar && <StarButton task={task} onToggle={onToggleStar} className="h-7 w-7" />}
          {isStar && (task.column === "done" ? (
            <Button variant="ghost" size="icon" className="h-7 w-7" title="Not done (for me)" onClick={() => onReopen(task)}>
              <RiArrowGoBackLine className="h-4 w-4" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" className="h-7 w-7" title="Mark done (for me only)" onClick={() => onMarkDone(task)}>
              <RiCheckLine className="h-4 w-4" />
            </Button>
          ))}
          {isStar && (
            <TaskCardMenu task={task} lanes={lanes} tags={tags} models={models}
              open={menuOpen} onOpenChange={setMenuOpen}
              onMoveToLane={onMoveToLane} onReopen={onReopen} onMarkDone={onMarkDone}
              onRemoveFromBoard={onRemoveFromBoard} onDeleteDraft={onDeleteDraft}
              onFork={onFork}
              onSetModel={onSetModel} onSetPriority={onSetPriority} onSetDeadline={onSetDeadline} onSetTags={onSetTags} onCreateTag={onCreateTag}>
              <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Organize starred task">
                <RiMoreLine className="h-4 w-4" />
              </Button>
            </TaskCardMenu>
          )}
          {!isStar && !readOnly && <>
          {isDraft && !!task.prompt.trim() && (
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              title="Start"
              onClick={() => onStart(task)}
            >
              <RiPlayLine className="h-4 w-4" />
            </Button>
          )}
          {!task.human_task && (task.column === "working" || isStoppedColumn(task.column) || isParked) && (
            <Button
              variant="ghost" size="icon" className="h-7 w-7"
              title="Mark done"
              onClick={() => onMarkDone(task)}
            >
              <RiCheckLine className="h-4 w-4" />
            </Button>
          )}
          <TaskCardMenu task={task} lanes={lanes} tags={tags} models={models}
            open={menuOpen} onOpenChange={setMenuOpen}
            onMoveToLane={onMoveToLane} onReopen={onReopen}
            onRemoveFromBoard={onRemoveFromBoard} onDeleteDraft={onDeleteDraft}
            onFork={onFork}
            onSetModel={onSetModel} onSetPriority={onSetPriority} onSetDeadline={onSetDeadline} onSetTags={onSetTags} onCreateTag={onCreateTag}>
              <Button variant="ghost" size="icon" className="h-7 w-7">
                <RiMoreLine className="h-4 w-4" />
              </Button>
          </TaskCardMenu>
          </>}
        </div>}
      </div>
    </div>
  );
}
