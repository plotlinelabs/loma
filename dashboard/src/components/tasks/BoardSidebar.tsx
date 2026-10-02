"use client";

import { RiAddLine, RiSearchLine, RiSidebarFoldLine, RiSidebarUnfoldLine } from "@remixicon/react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { TaskBoardSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Boards 1-9 have a jump shortcut; the label follows the platform. */
export const boardShortcut = (index: number, mac: boolean) =>
  index < 9 ? `${mac ? "⌥" : "Alt+"}${index + 1}` : null;

/** Small square with the board's first letter. */
export function BoardTile({ name, active, className }: { name: string; active?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold uppercase transition-colors",
        active ? "bg-brand-600 text-brand-50" : "bg-brand-100 text-brand-700",
        className,
      )}
    >
      {name.trim().charAt(0) || "?"}
    </span>
  );
}

/** How many of the board's tasks are waiting on you. */
export function NeedsYouCount({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null;
  return (
    <span
      className={cn(
        "flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold tabular-nums text-background",
        className,
      )}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

const ROW = "group flex h-9 w-full items-center rounded-lg text-left text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring";
const ROW_IDLE = "text-muted-foreground hover:bg-muted/70 hover:text-foreground";

/** Desktop board list beside the Tasks board: every board you can open, with
 * how many of its tasks are waiting on you. Collapses to a narrow rail. */
export function BoardSidebar({
  boards,
  currentId,
  collapsed,
  mac,
  onToggleCollapsed,
  onSelect,
  onCreate,
  onSearch,
}: {
  boards: TaskBoardSummary[];
  currentId: string;
  collapsed: boolean;
  mac: boolean;
  onToggleCollapsed: () => void;
  onSelect: (boardId: string) => void;
  onCreate: () => void;
  onSearch: () => void;
}) {
  const toggleLabel = collapsed ? "Show boards" : "Hide boards";
  const searchKeys = mac ? "⌘K" : "Ctrl+K";
  const ToggleIcon = collapsed ? RiSidebarUnfoldLine : RiSidebarFoldLine;
  /** Collapsed rows are icon-only, so their label moves into a tooltip. */
  const withTip = (key: string, label: string, node: React.ReactElement) =>
    collapsed ? (
      <Tooltip key={key}>
        <TooltipTrigger asChild>{node}</TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    ) : node;

  return (
    <aside
      aria-label="Boards"
      className={cn(
        "flex shrink-0 flex-col rounded-xl border border-border bg-card/60 p-1.5 transition-[width] duration-200 max-md:hidden",
        collapsed ? "w-[50px]" : "w-56",
      )}
    >
      <div className={cn("flex h-8 items-center", collapsed ? "justify-center" : "justify-between pl-2")}>
        {!collapsed && <span className="text-[13px] font-semibold text-foreground">Boards</span>}
        {withTip("toggle", toggleLabel, (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={toggleLabel}
            aria-expanded={!collapsed}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ToggleIcon className="h-4 w-4" />
          </button>
        ))}
      </div>

      {withTip("search", `Jump to a board or task (${searchKeys})`, (
        <button
          type="button"
          onClick={onSearch}
          aria-label="Jump to a board or task"
          className={cn(
            "mt-1 flex h-8 items-center rounded-lg border border-border bg-background text-[13px] text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
            collapsed ? "justify-center" : "gap-2 px-2",
          )}
        >
          <RiSearchLine className="h-3.5 w-3.5 shrink-0" />
          {!collapsed && (
            <>
              <span className="flex-1 text-left">Jump to…</span>
              <kbd className="rounded border border-border px-1 font-sans text-[10px] leading-4">{searchKeys}</kbd>
            </>
          )}
        </button>
      ))}

      <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {boards.map((board, index) => {
          const active = board.id === currentId;
          const count = board.needs_you ?? 0;
          const shortcut = boardShortcut(index, mac);
          const hint = [board.name, count > 0 && `${count} waiting on you`, shortcut].filter(Boolean).join(" · ");
          return withTip(board.id, hint, (
            <button
              key={board.id}
              type="button"
              onClick={() => onSelect(board.id)}
              aria-label={collapsed ? hint : undefined}
              aria-current={active ? "page" : undefined}
              className={cn(
                ROW,
                collapsed ? "relative justify-center" : "gap-2 px-1.5",
                active ? "bg-muted font-medium text-foreground" : ROW_IDLE,
              )}
            >
              <BoardTile name={board.name} active={active} />
              {collapsed ? (
                count > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-card" />
              ) : (
                <>
                  <span className="min-w-0 flex-1 truncate">{board.name}</span>
                  {shortcut && (
                    <kbd className="font-sans text-[10px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                      {shortcut}
                    </kbd>
                  )}
                  <NeedsYouCount count={count} />
                </>
              )}
            </button>
          ));
        })}
      </nav>

      {withTip("new", "New board", (
        <button
          type="button"
          onClick={onCreate}
          aria-label={collapsed ? "New board" : undefined}
          className={cn(ROW, ROW_IDLE, "mt-1 shrink-0", collapsed ? "justify-center" : "gap-2 px-1.5")}
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-dashed border-border">
            <RiAddLine className="h-3.5 w-3.5" />
          </span>
          {!collapsed && "New board"}
        </button>
      ))}
    </aside>
  );
}
