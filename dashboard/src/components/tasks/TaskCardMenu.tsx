"use client";

import { useState } from "react";
import { RiCheckLine, RiPriceTag3Line } from "@remixicon/react";
import { Input } from "@/components/ui/input";
import {
  ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSub,
  ContextMenuSubContent, ContextMenuSubTrigger, ContextMenuTrigger,
} from "@/components/ui/context-menu";
import type { AgentModel, BoardLane, Task, TaskTag } from "@/lib/api";

export interface TaskCardMenuProps {
  task: Task; lanes: BoardLane[]; tags: TaskTag[]; models: AgentModel[];
  onMoveToLane: (task: Task, laneId: string) => void;
  onSetModel: (task: Task, model: string) => void;
  onSetTags: (task: Task, tagIds: string[]) => void;
  onCreateTag: (task: Task, name: string) => void;
  children: React.ReactNode;
}

/** Right-click settings menu for a task card. A Radix ContextMenu (not a
 * DropdownMenu) so it only opens on contextmenu events — a plain left click
 * falls through to the card's own onClick, which opens the conversation. */
export function TaskCardMenu({ task, lanes, tags, models, onMoveToLane,
  onSetModel, onSetTags, onCreateTag, children }: TaskCardMenuProps) {
  const [query, setQuery] = useState("");
  const currentTags = task.task_tag_ids || [];
  const matching = tags.filter((tag) => tag.name.toLowerCase().includes(query.trim().toLowerCase()));
  const canCreate = query.trim() && !tags.some((tag) => tag.name.toLowerCase() === query.trim().toLowerCase());
  const toggleTag = (id: string) => onSetTags(task,
    currentTags.includes(id) ? currentTags.filter((tagId) => tagId !== id) : [...currentTags, id]);

  return (
    <ContextMenu onOpenChange={(open) => { if (!open) setQuery(""); }}>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-56">
        <ContextMenuSub>
          <ContextMenuSubTrigger>Lane</ContextMenuSubTrigger>
          <ContextMenuSubContent>
            {lanes.map((lane) => (
              <ContextMenuItem key={lane.id} onClick={() => onMoveToLane(task, lane.id)}>
                <span className="flex-1">{lane.name}</span>{task.column === lane.id && <RiCheckLine />}
              </ContextMenuItem>
            ))}
          </ContextMenuSubContent>
        </ContextMenuSub>
        <ContextMenuSub>
          <ContextMenuSubTrigger disabled={task.status === "running"} title={task.status === "running" ? "Model cannot change while running" : undefined}>Model</ContextMenuSubTrigger>
          <ContextMenuSubContent className="max-h-72 w-64 overflow-y-auto">
            {models.map((model) => (
              <ContextMenuItem key={model.id} onClick={() => onSetModel(task, model.id)}>
                <span className="flex-1 truncate">{model.label}</span>{task.model === model.id && <RiCheckLine />}
              </ContextMenuItem>
            ))}
          </ContextMenuSubContent>
        </ContextMenuSub>
        <ContextMenuSub>
          <ContextMenuSubTrigger><RiPriceTag3Line /> Tags</ContextMenuSubTrigger>
          <ContextMenuSubContent className="w-60" onKeyDown={(e) => e.stopPropagation()}>
            <div className="p-1" onClick={(e) => e.stopPropagation()}>
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search or create tag" className="h-8" />
            </div>
            {matching.map((tag) => (
              <ContextMenuItem key={tag.id} onSelect={(e) => { e.preventDefault(); toggleTag(tag.id); }}>
                <span className="flex-1">{tag.name}</span>{currentTags.includes(tag.id) && <RiCheckLine />}
              </ContextMenuItem>
            ))}
            {canCreate && (
              <ContextMenuItem onClick={() => { onCreateTag(task, query.trim()); setQuery(""); }}>
                Create “{query.trim()}”
              </ContextMenuItem>
            )}
          </ContextMenuSubContent>
        </ContextMenuSub>
      </ContextMenuContent>
    </ContextMenu>
  );
}
