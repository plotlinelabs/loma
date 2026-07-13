"use client";

import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  RiPlayLine,
  RiCheckLine,
  RiMoreLine,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import ClientTimestamp from "@/components/ClientTimestamp";
import type { AgentModel, BoardLane, Task, TaskPriority, TaskTag } from "@/lib/api";
import { isDraft as isDraftTask, isStaged as isStagedTask, taskDot, taskTimestamp } from "./taskDisplay";
import { TaskCardMenu } from "./TaskCardMenu";
import { TaskPriorityTag } from "./TaskPriority";

interface TaskCardProps {
  task: Task;
  lanes: BoardLane[];
  tags: TaskTag[];
  models: AgentModel[];
  onOpen: (task: Task) => void;
  onStart: (task: Task) => void;
  onMarkDone: (task: Task) => void;
  onReopen: (task: Task) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onRemoveFromBoard: (task: Task) => void;
  onDeleteDraft: (task: Task) => void;
  onSetModel: (task: Task, model: string) => void;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
  onSetTags: (task: Task, tagIds: string[]) => void;
  onCreateTag: (task: Task, name: string) => void;
}

export function TaskCard({
  task, lanes, tags, models, onOpen, onStart, onMarkDone, onReopen,
  onMoveToLane, onRemoveFromBoard, onDeleteDraft, onSetModel, onSetPriority, onSetTags, onCreateTag,
}: TaskCardProps) {
  const isStaged = isStagedTask(task);
  const isDraft = isDraftTask(task);
  const isParked = isStaged && !isDraft;
  const [menuOpen, setMenuOpen] = useState(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.conversation_id, data: { task } });

  const dot = taskDot(task);
  const timestamp = taskTimestamp(task);

  const assignedTags = tags.filter((tag) => (task.task_tag_ids || []).includes(tag.id));
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      onClick={() => onOpen(task)}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuOpen(true);
      }}
      className={cn(
        "group rounded-md border bg-card px-3 py-2 cursor-pointer",
        "hover:bg-muted/50 touch-none select-none",
        isDragging && "opacity-40",
      )}
    >
      <div className="flex items-start gap-2">
        {dot && <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 break-words text-[13px]">
            {task.title || task.prompt}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            <ClientTimestamp iso={timestamp} variant="short" placeholder="—" />
            {task.total_turns > 0 && <span>{task.total_turns} turns</span>}
          </div>
          <div className="mt-1 flex items-center gap-1 overflow-hidden">
            <TaskPriorityTag task={task} onSetPriority={onSetPriority} />
            {assignedTags.slice(0, 2).map((tag) => <span key={tag.id} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{tag.name}</span>)}
            {assignedTags.length > 2 && <span className="text-[10px] text-muted-foreground">+{assignedTags.length - 2}</span>}
          </div>
        </div>
        <div
          // Hover-revealed on pointer devices; always visible on touch
          // (hover never fires there) and while focused via keyboard.
          className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {isDraft && !!task.prompt.trim() && (
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
          <TaskCardMenu task={task} lanes={lanes} tags={tags} models={models}
            open={menuOpen} onOpenChange={setMenuOpen}
            onMoveToLane={onMoveToLane} onReopen={onReopen}
            onRemoveFromBoard={onRemoveFromBoard} onDeleteDraft={onDeleteDraft}
            onSetModel={onSetModel} onSetPriority={onSetPriority} onSetTags={onSetTags} onCreateTag={onCreateTag}>
              <Button variant="ghost" size="icon" className="h-6 w-6">
                <RiMoreLine className="h-3.5 w-3.5" />
              </Button>
          </TaskCardMenu>
        </div>
      </div>
    </div>
  );
}
