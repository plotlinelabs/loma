"use client";

import { useEffect, useState } from "react";
import { RiAddLine, RiSidebarFoldLine, RiSidebarUnfoldLine } from "@remixicon/react";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { fetchNeedsYouTasks, type NeedsYouTask, type TaskBoardSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { BoardTile, NeedsYouCount, boardShortcut } from "@/components/tasks/BoardSidebar";

/** Quick switcher (Cmd/Ctrl+K): jump to a board, or straight to a task that
 * is waiting on you on any board. */
export function BoardCommand({
  open,
  onOpenChange,
  boards,
  currentId,
  mac,
  sidebarCollapsed,
  onSelectBoard,
  onOpenTask,
  onCreateBoard,
  onToggleSidebar,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  boards: TaskBoardSummary[];
  currentId: string;
  mac: boolean;
  sidebarCollapsed: boolean;
  onSelectBoard: (boardId: string) => void;
  onOpenTask: (task: NeedsYouTask) => void;
  onCreateBoard: () => void;
  onToggleSidebar: () => void;
}) {
  const [tasks, setTasks] = useState<NeedsYouTask[]>([]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchNeedsYouTasks().then((list) => { if (!cancelled) setTasks(list); }).catch(() => {});
    return () => { cancelled = true; };
  }, [open]);

  const boardName = new Map(boards.map((b) => [b.id, b.name]));
  // Tasks on a board you can no longer open are left out.
  const waiting = tasks.filter((task) => boardName.has(task.board_id));
  const run = (action: () => void) => () => { onOpenChange(false); action(); };
  const SidebarIcon = sidebarCollapsed ? RiSidebarUnfoldLine : RiSidebarFoldLine;

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Jump to a board or task"
      description="Search your boards and the tasks waiting on you."
      className="sm:max-w-lg"
    >
      <Command>
        <CommandInput placeholder="Jump to a board or task…" />
        <CommandList className="max-h-[26rem]">
          <CommandEmpty>No boards or tasks found.</CommandEmpty>
          <CommandGroup heading="Boards">
            {boards.map((board, index) => (
              <CommandItem key={board.id} value={`board ${board.name} ${board.id}`} onSelect={run(() => onSelectBoard(board.id))}>
                <BoardTile name={board.name} active={board.id === currentId} className="h-5 w-5 text-[10px]" />
                <span className="min-w-0 truncate">{board.name}</span>
                <NeedsYouCount count={board.needs_you ?? 0} />
                <CommandShortcut className="tracking-normal">{boardShortcut(index, mac)}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
          {waiting.length > 0 && (
            <CommandGroup heading="Waiting on you">
              {waiting.map((task) => (
                <CommandItem
                  key={task.conversation_id}
                  value={`task ${task.title ?? ""} ${boardName.get(task.board_id)} ${task.conversation_id}`}
                  onSelect={run(() => onOpenTask(task))}
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                    <span className={cn("h-2 w-2 rounded-full", task.status === "completed" ? "bg-amber-500" : "bg-destructive")} />
                  </span>
                  <span className="min-w-0 truncate">{task.title || "Untitled task"}</span>
                  <CommandShortcut className="max-w-[40%] shrink-0 truncate tracking-normal">
                    {boardName.get(task.board_id)}
                  </CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandGroup heading="Actions">
            <CommandItem value="action new board create" onSelect={run(onCreateBoard)}>
              <RiAddLine className="mx-0.5" /> New board
              <CommandShortcut />
            </CommandItem>
            <CommandItem value="action toggle hide show boards sidebar" onSelect={run(onToggleSidebar)}>
              <SidebarIcon className="mx-0.5" /> {sidebarCollapsed ? "Show boards sidebar" : "Hide boards sidebar"}
              <CommandShortcut />
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
