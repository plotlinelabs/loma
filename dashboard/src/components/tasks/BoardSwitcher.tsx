"use client";

import { RiAddLine, RiArrowDownSLine, RiCheckLine, RiTeamLine, RiUserAddLine } from "@remixicon/react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { TaskBoardSummary } from "@/lib/api";

const ROLE_LABEL: Record<string, string> = { editor: "Editor", viewer: "View only" };

/** Board picker in the Tasks header: your own board, boards you own, and
 * boards shared with you. */
export function BoardSwitcher({
  boards,
  current,
  onSelect,
  onCreate,
  onManage,
}: {
  boards: TaskBoardSummary[];
  current: TaskBoardSummary | undefined;
  onSelect: (boardId: string) => void;
  onCreate: () => void;
  onManage: () => void;
}) {
  const title = !current || !current.shared ? "Tasks" : current.name;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex min-w-0 items-center gap-1 rounded-md px-1 -mx-1 text-lg font-semibold hover:bg-muted"
          aria-label="Switch board"
        >
          {current?.shared && <RiTeamLine className="h-4 w-4 shrink-0 text-muted-foreground" />}
          <span className="truncate">{title}</span>
          <RiArrowDownSLine className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Boards</DropdownMenuLabel>
        {boards.map((board) => (
          <DropdownMenuItem key={board.id} onSelect={() => onSelect(board.id)}>
            {board.shared
              ? <RiTeamLine className="h-3.5 w-3.5 text-muted-foreground" />
              : <span className="w-3.5" />}
            <span className="min-w-0 flex-1 truncate">{board.name}</span>
            {board.role !== "owner" && (
              <span className="text-[11px] text-muted-foreground">{ROLE_LABEL[board.role]}</span>
            )}
            {board.id === current?.id && <RiCheckLine className="h-3.5 w-3.5" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onCreate}>
          <RiAddLine className="h-3.5 w-3.5" /> New board
        </DropdownMenuItem>
        {current?.shared && current.role === "owner" && (
          <DropdownMenuItem onSelect={onManage}>
            <RiUserAddLine className="h-3.5 w-3.5" /> Share “{current.name}”
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
