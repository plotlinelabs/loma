"use client";

import { RiAddLine, RiSidebarFoldLine, RiSidebarUnfoldLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { TaskBoardSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Two-letter label for a board in the collapsed rail. */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

/** Desktop board list beside the Tasks board: every board you can open, with
 * how many of its tasks are waiting on you. Collapses to a narrow rail. */
export function BoardSidebar({
  boards,
  currentId,
  collapsed,
  onToggleCollapsed,
  onSelect,
  onCreate,
}: {
  boards: TaskBoardSummary[];
  currentId: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (boardId: string) => void;
  onCreate: () => void;
}) {
  const toggleLabel = collapsed ? "Show boards" : "Hide boards";
  return (
    <aside
      aria-label="Boards"
      className={cn("flex shrink-0 flex-col gap-1 max-md:hidden", collapsed ? "w-9" : "w-48")}
    >
      <div className={cn("pwa-header-offset flex h-8 items-center", collapsed ? "justify-center" : "justify-between pl-2")}>
        {!collapsed && (
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Boards</span>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
              onClick={onToggleCollapsed}
              aria-label={toggleLabel}
              aria-expanded={!collapsed}
            >
              {collapsed ? <RiSidebarUnfoldLine className="h-4 w-4" /> : <RiSidebarFoldLine className="h-4 w-4" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">{toggleLabel}</TooltipContent>
        </Tooltip>
      </div>
      <nav className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
        {boards.map((board) => {
          const active = board.id === currentId;
          const count = board.needs_you ?? 0;
          const hint = count > 0 ? `${board.name} · ${count} need${count === 1 ? "s" : ""} you` : board.name;
          if (collapsed) {
            return (
              <Tooltip key={board.id}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => onSelect(board.id)}
                    aria-label={hint}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex h-8 w-9 items-center justify-center rounded-md text-[11px] font-semibold",
                      active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {initials(board.name)}
                    {count > 0 && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-500" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right">{hint}</TooltipContent>
              </Tooltip>
            );
          }
          return (
            <button
              key={board.id}
              type="button"
              onClick={() => onSelect(board.id)}
              title={hint}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-8 items-center gap-2 rounded-md px-2 text-left text-[13px]",
                active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <span className="min-w-0 flex-1 truncate">{board.name}</span>
              {count > 0 && (
                <span className="flex h-5 min-w-[20px] shrink-0 items-center justify-center rounded bg-current/10 px-1 text-[11px] font-semibold tabular-nums">
                  {count}
                </span>
              )}
            </button>
          );
        })}
        {collapsed ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={onCreate}
                aria-label="New board"
                className="flex h-8 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              >
                <RiAddLine className="h-4 w-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">New board</TooltipContent>
          </Tooltip>
        ) : (
          <button
            type="button"
            onClick={onCreate}
            className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
          >
            <RiAddLine className="h-4 w-4" /> New board
          </button>
        )}
      </nav>
    </aside>
  );
}
