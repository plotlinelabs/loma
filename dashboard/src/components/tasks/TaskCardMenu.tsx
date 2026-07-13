"use client";

import { useState } from "react";
import {
  RiArrowGoBackLine,
  RiCheckLine,
  RiDeleteBinLine,
  RiLogoutBoxRLine,
  RiPriceTag3Line,
} from "@remixicon/react";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AgentModel, BoardLane, Task, TaskPriority, TaskTag } from "@/lib/api";
import { deadlineDisplay, isDraft as isDraftTask, isStaged as isStagedTask, priorityDisplay } from "./taskDisplay";
import { PriorityMenuItems } from "./TaskPriority";
import { DeadlineMenuItems } from "./TaskDeadline";

export interface TaskCardMenuProps {
  task: Task; lanes: BoardLane[]; tags: TaskTag[]; models: AgentModel[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onReopen: (task: Task) => void;
  onRemoveFromBoard: (task: Task) => void;
  onDeleteDraft: (task: Task) => void;
  onSetModel: (task: Task, model: string) => void;
  onSetPriority: (task: Task, priority: TaskPriority | null) => void;
  onSetDeadline: (task: Task, deadline: string | null) => void;
  onSetTags: (task: Task, tagIds: string[]) => void;
  onCreateTag: (task: Task, name: string) => void;
  children: React.ReactNode;
}

/** One task actions menu shared by the overflow button and card right-click. */
export function TaskCardMenu({ task, lanes, tags, models, open, onOpenChange,
  onMoveToLane, onReopen, onRemoveFromBoard, onDeleteDraft,
  onSetModel, onSetPriority, onSetDeadline, onSetTags, onCreateTag, children }: TaskCardMenuProps) {
  const [query, setQuery] = useState("");
  const isDraft = isDraftTask(task);
  const isStaged = isStagedTask(task);
  const currentTags = task.task_tag_ids || [];
  const targetLanes = isStaged
    ? lanes.filter((lane) => lane.id !== task.task_lane)
    : task.column === "needs_input" || task.column === "done" ? lanes : [];
  const matching = tags.filter((tag) => tag.name.toLowerCase().includes(query.trim().toLowerCase()));
  const canCreate = query.trim() && !tags.some((tag) => tag.name.toLowerCase() === query.trim().toLowerCase());
  const toggleTag = (id: string) => onSetTags(task,
    currentTags.includes(id) ? currentTags.filter((tagId) => tagId !== id) : [...currentTags, id]);

  return (
    <DropdownMenu open={open} onOpenChange={(nextOpen) => {
      onOpenChange(nextOpen);
      if (!nextOpen) setQuery("");
    }}>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {targetLanes.length > 0 && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>Move To</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {targetLanes.map((lane) => (
                <DropdownMenuItem key={lane.id} onClick={() => onMoveToLane(task, lane.id)}>
                  <span className="flex-1">{lane.name}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={task.status === "running"} title={task.status === "running" ? "Model cannot change while running" : undefined}>Model</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-72 w-64 overflow-y-auto">
            {models.map((model) => (
              <DropdownMenuItem key={model.id} onClick={() => onSetModel(task, model.id)}>
                <span className="flex-1 truncate">{model.label}</span>{task.model === model.id && <RiCheckLine />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <span className="flex-1">Priority</span>
            <span className="text-xs text-muted-foreground">{priorityDisplay(task.task_priority)?.label ?? "None"}</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-40">
            <PriorityMenuItems task={task} onSetPriority={onSetPriority} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <span className="flex-1">Deadline</span>
            <span className="text-xs text-muted-foreground">{deadlineDisplay(task.task_deadline)?.dateLabel ?? "None"}</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DeadlineMenuItems task={task} onSetDeadline={onSetDeadline} onPicked={() => onOpenChange(false)} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger><RiPriceTag3Line /> Tags</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-60" onKeyDown={(e) => e.stopPropagation()}>
            <div className="p-1" onClick={(e) => e.stopPropagation()}>
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search or create tag" className="h-8" />
            </div>
            {matching.map((tag) => (
              <DropdownMenuItem key={tag.id} onSelect={(e) => { e.preventDefault(); toggleTag(tag.id); }}>
                <span className="flex-1">{tag.name}</span>{currentTags.includes(tag.id) && <RiCheckLine />}
              </DropdownMenuItem>
            ))}
            {canCreate && (
              <DropdownMenuItem onClick={() => { onCreateTag(task, query.trim()); setQuery(""); }}>
                Create “{query.trim()}”
              </DropdownMenuItem>
            )}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        {task.column === "done" && (
          <DropdownMenuItem onClick={() => onReopen(task)}>
            <RiArrowGoBackLine className="h-3.5 w-3.5" /> Reopen
          </DropdownMenuItem>
        )}
        {!isDraft && (
          <DropdownMenuItem onClick={() => onRemoveFromBoard(task)}>
            <RiLogoutBoxRLine className="h-3.5 w-3.5" /> Remove from board
          </DropdownMenuItem>
        )}
        {isDraft && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => onDeleteDraft(task)}>
              <RiDeleteBinLine className="h-3.5 w-3.5" /> Delete
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
