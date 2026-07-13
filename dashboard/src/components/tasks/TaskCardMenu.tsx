"use client";

import { useState } from "react";
import { RiCheckLine, RiMoreLine, RiPriceTag3Line } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSub,
  DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AgentModel, BoardLane, Task, TaskTag } from "@/lib/api";

export interface TaskCardMenuProps {
  task: Task; lanes: BoardLane[]; tags: TaskTag[]; models: AgentModel[];
  open?: boolean; onOpenChange?: (open: boolean) => void;
  onMoveToLane: (task: Task, laneId: string) => void;
  onSetModel: (task: Task, model: string) => void;
  onSetTags: (task: Task, tagIds: string[]) => void;
  onCreateTag: (task: Task, name: string) => void;
  children: React.ReactNode;
}

export function TaskCardMenu({ task, lanes, tags, models, open, onOpenChange, onMoveToLane,
  onSetModel, onSetTags, onCreateTag, children }: TaskCardMenuProps) {
  const [query, setQuery] = useState("");
  const currentTags = task.task_tag_ids || [];
  const matching = tags.filter((tag) => tag.name.toLowerCase().includes(query.trim().toLowerCase()));
  const canCreate = query.trim() && !tags.some((tag) => tag.name.toLowerCase() === query.trim().toLowerCase());
  const toggleTag = (id: string) => onSetTags(task,
    currentTags.includes(id) ? currentTags.filter((tagId) => tagId !== id) : [...currentTags, id]);

  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>Lane</DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {lanes.map((lane) => (
              <DropdownMenuItem key={lane.id} onClick={() => onMoveToLane(task, lane.id)}>
                <span className="flex-1">{lane.name}</span>{task.column === lane.id && <RiCheckLine />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
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
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function TaskMenuButton() {
  return <Button variant="ghost" size="icon" className="h-6 w-6"><RiMoreLine className="h-3.5 w-3.5" /></Button>;
}
