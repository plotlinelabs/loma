"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type Modifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { RiAddLine, RiArrowDownSLine, RiCheckboxLine, RiDraggable } from "@remixicon/react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useBoards } from "@/lib/BoardsContext";
import { useTaskAttention } from "@/lib/TaskAttentionContext";
import { useIsMobile } from "@/hooks/useIsMobile";
import { PERSONAL_BOARD_ID, type TaskBoardSummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import { BoardEmoji, NeedsYouCount, boardShortcut, useIsMac } from "./tasks/boardBits";
import { EmojiPicker } from "./tasks/EmojiPicker";

/** Board list open or folded under "Tasks", remembered on this device. */
const OPEN_STORAGE_KEY = "loma-nav-boards-open";

const openListeners = new Set<() => void>();
const subscribeOpen = (notify: () => void) => {
  openListeners.add(notify);
  return () => { openListeners.delete(notify); };
};
const readOpen = () => window.localStorage.getItem(OPEN_STORAGE_KEY) !== "0";

/** Boards only move up and down. */
const verticalOnly: Modifier = ({ transform }) => ({ ...transform, x: 0 });

const canSetEmoji = (board: TaskBoardSummary) => board.id === PERSONAL_BOARD_ID || board.role === "owner";

/** Shared row look, matching the other nav items. */
export const NAV_ROW = cn(
  "relative flex items-center rounded-lg text-[13px] font-medium transition-colors duration-150",
  "max-md:text-[16px] max-md:[&_svg]:h-5 max-md:[&_svg]:w-5",
);
export const NAV_ACTIVE = "bg-sidebar-accent text-sidebar-accent-foreground";
export const NAV_IDLE = "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground";

function BoardRow({
  board,
  index,
  active,
  mac,
  onOpen,
}: {
  board: TaskBoardSummary;
  index: number;
  active: boolean;
  mac: boolean;
  onOpen: () => void;
}) {
  const { setEmoji } = useBoards();
  const [pickerOpen, setPickerOpen] = useState(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: board.id });
  const count = board.needs_you ?? 0;
  const shortcut = boardShortcut(index, mac);
  const editable = canSetEmoji(board);

  const emoji = <BoardEmoji emoji={board.emoji} />;
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      // Mouse/touch drag from anywhere on the row; keyboard drag from the handle.
      onPointerDown={listeners?.onPointerDown as React.PointerEventHandler<HTMLDivElement> | undefined}
      className={cn(
        "group/board relative flex h-8 items-center rounded-lg pl-1 pr-2 max-md:h-10",
        active ? NAV_ACTIVE : NAV_IDLE,
        isDragging && "z-10 bg-sidebar-accent shadow-md",
      )}
    >
      <button
        type="button"
        {...attributes}
        onKeyDown={listeners?.onKeyDown as React.KeyboardEventHandler<HTMLButtonElement> | undefined}
        aria-label={`Reorder ${board.name}`}
        className="relative z-[1] flex h-6 w-4 shrink-0 cursor-grab items-center justify-center text-sidebar-foreground/40 opacity-0 outline-none transition-opacity focus-visible:opacity-100 group-hover/board:opacity-100 active:cursor-grabbing pointer-coarse:hidden"
      >
        <RiDraggable className="h-3.5 w-3.5" />
      </button>
      {editable ? (
        <EmojiPicker value={board.emoji} open={pickerOpen} onOpenChange={setPickerOpen} onPick={(e) => void setEmoji(board, e)}>
          <button
            type="button"
            aria-label={`Change ${board.name} emoji`}
            title="Change emoji"
            onPointerDown={(e) => e.stopPropagation()}
            className="relative z-[1] flex h-6 w-6 shrink-0 items-center justify-center rounded-md outline-none hover:bg-sidebar-foreground/10 focus-visible:ring-2 focus-visible:ring-ring"
          >
            {emoji}
          </button>
        </EmojiPicker>
      ) : (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center">{emoji}</span>
      )}
      <button
        type="button"
        onClick={onOpen}
        aria-current={active ? "page" : undefined}
        title={shortcut ? `${board.name} (${shortcut})` : board.name}
        className="flex h-full min-w-0 flex-1 items-center gap-2 pl-1.5 text-left text-[13px] font-medium outline-none max-md:text-[15px] focus-visible:underline after:absolute after:inset-0 after:content-['']"
      >
        <span className="min-w-0 flex-1 truncate">{board.name}</span>
      </button>
      <NeedsYouCount count={count} className="pointer-events-none relative" />
    </div>
  );
}

/** "Tasks" plus the user's boards (emoji, needs-you count, drag to reorder).
 * Collapsed rail: just the board emojis, with an amber dot when one needs you. */
export function NavBoards({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  const isMobile = useIsMobile();
  const { boards, currentBoardId, openBoard, createBoard, reorder } = useBoards();
  const { needsInputCount } = useTaskAttention();
  const mac = useIsMac();
  const open = useSyncExternalStore(subscribeOpen, readOpen, () => true);
  const toggleOpen = () => {
    window.localStorage.setItem(OPEN_STORAGE_KEY, open ? "0" : "1");
    openListeners.forEach((notify) => notify());
  };

  const sensors = useSensors(
    // Phones: press and hold to drag, so a swipe still scrolls the drawer.
    useSensor(PointerSensor, {
      activationConstraint: isMobile ? { delay: 250, tolerance: 6 } : { distance: 6 },
    }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const ids = boards.map((b) => b.id);
    reorder(arrayMove(ids, ids.indexOf(String(active.id)), ids.indexOf(String(over.id))));
  };

  const onTasks = pathname.startsWith("/tasks");
  const open_ = (id: string) => { openBoard(id); onNavigate(); };

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <Link
              href="/tasks"
              prefetch
              onClick={onNavigate}
              aria-label="Tasks"
              className={cn(NAV_ROW, "mx-auto w-10 justify-center py-1.5", onTasks ? NAV_ACTIVE : NAV_IDLE)}
            >
              <RiCheckboxLine size={16} className={onTasks ? "text-sidebar-primary" : undefined} />
            </Link>
          </TooltipTrigger>
          <TooltipContent side="right">Tasks</TooltipContent>
        </Tooltip>
        {boards.map((board, index) => {
          const count = board.needs_you ?? 0;
          const shortcut = boardShortcut(index, mac);
          const active = onTasks && board.id === currentBoardId;
          const hint = [board.name, count > 0 && `${count} waiting on you`, shortcut].filter(Boolean).join(" · ");
          return (
            <Tooltip key={board.id}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => open_(board.id)}
                  aria-label={hint}
                  aria-current={active ? "page" : undefined}
                  className={cn(NAV_ROW, "mx-auto h-8 w-10 justify-center", active ? NAV_ACTIVE : NAV_IDLE)}
                >
                  <BoardEmoji emoji={board.emoji} />
                  {count > 0 && <span className="absolute right-1.5 top-1 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-sidebar" />}
                </button>
              </TooltipTrigger>
              <TooltipContent side="right">{hint}</TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    );
  }

  return (
    <div>
      <div className={cn(NAV_ROW, "group/tasks", onTasks && !open ? NAV_ACTIVE : NAV_IDLE)}>
        <Link
          href="/tasks"
          prefetch
          onClick={onNavigate}
          aria-current={onTasks ? "page" : undefined}
          className="flex min-w-0 flex-1 items-center gap-2.5 py-2 pl-3 max-md:gap-3 max-md:py-2.5"
        >
          <RiCheckboxLine size={16} className={cn("shrink-0", onTasks && "text-sidebar-primary")} />
          <span>Tasks</span>
        </Link>
        {!open && needsInputCount > 0 && <NeedsYouCount count={needsInputCount} />}
        <button
          type="button"
          onClick={toggleOpen}
          aria-expanded={open}
          aria-label={open ? "Hide boards" : "Show boards"}
          title={open ? "Hide boards" : "Show boards"}
          className="mx-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/60 outline-none hover:bg-sidebar-foreground/10 hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RiArrowDownSLine className={cn("h-4 w-4 transition-transform", !open && "-rotate-90")} />
        </button>
      </div>

      {open && (
        <div className="mt-0.5 space-y-px pl-3" aria-label="Boards" role="group">
          <DndContext sensors={sensors} collisionDetection={closestCenter} modifiers={[verticalOnly]} onDragEnd={onDragEnd}>
            <SortableContext items={boards.map((b) => b.id)} strategy={verticalListSortingStrategy}>
              {boards.map((board, index) => (
                <BoardRow
                  key={board.id}
                  board={board}
                  index={index}
                  mac={mac}
                  active={onTasks && board.id === currentBoardId}
                  onOpen={() => open_(board.id)}
                />
              ))}
            </SortableContext>
          </DndContext>
          <button
            type="button"
            onClick={() => { createBoard(); onNavigate(); }}
            className={cn(NAV_ROW, NAV_IDLE, "h-8 w-full gap-2 pl-5 pr-2 text-sidebar-foreground/70 max-md:h-10")}
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center"><RiAddLine className="h-3.5 w-3.5" /></span>
            <span className="pl-0.5">New board</span>
          </button>
        </div>
      )}
    </div>
  );
}
