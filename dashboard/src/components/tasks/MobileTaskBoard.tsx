"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { RiAddLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import type { Task, TasksBoardResponse } from "@/lib/api";
import { useTaskBoardActions } from "./useTaskBoardActions";
import { MobileTaskCard } from "./MobileTaskCard";

interface MobileTaskBoardProps {
  board: TasksBoardResponse;
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onEditDraft: (task: Task) => void;
  onAddTask: (laneId: string) => void;
  onError: (message: string | null) => void;
}

/** Inbox-style single-column board for phones: a chip scroller switches
 * columns, tap actions replace drag. Desktop keeps the kanban. */
export function MobileTaskBoard({
  board, onBoardChange, onRefresh, onEditDraft, onAddTask, onError,
}: MobileTaskBoardProps) {
  const {
    laneIds, tasksByColumn,
    startTask, markDone, reopen, moveToLane,
    removeFromBoard, deleteDraft, openTask,
  } = useTaskBoardActions({
    board, onBoardChange, onRefresh, onEditDraft, onError,
    openActiveInNewTab: false, // window.open is hostile in the standalone PWA
  });

  // Attention-first column order: Needs input, Working, lanes, Done.
  const columns = useMemo(
    () => [
      { id: "needs_input", name: "Needs input" },
      { id: "working", name: "Working" },
      ...board.lanes,
      { id: "done", name: "Done" },
    ],
    [board.lanes],
  );

  // Default selection: computed once when the board first loads and held in
  // state so the 5s poll never yanks the user off a column they're reading.
  const [selected, setSelected] = useState<string | null>(null);
  const initializedRef = useRef(false);
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    const needsInput = (tasksByColumn["needs_input"] ?? []).length;
    const firstBusyLane = board.lanes.find(
      (lane) => (tasksByColumn[lane.id] ?? []).length > 0,
    );
    setSelected(
      needsInput > 0 ? "needs_input" : firstBusyLane?.id ?? "working",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A lane deleted via settings falls back to the default column.
  const selectedId =
    selected && columns.some((c) => c.id === selected) ? selected : "needs_input";
  const tasks = tasksByColumn[selectedId] ?? [];
  const isLane = laneIds.includes(selectedId);

  return (
    <div className="flex flex-1 flex-col gap-3 min-h-0">
      {/* Column chips */}
      <div className="flex shrink-0 gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {columns.map((column) => {
          const count = (tasksByColumn[column.id] ?? []).length;
          const active = column.id === selectedId;
          return (
            <button
              key={column.id}
              onClick={() => setSelected(column.id)}
              className={cn(
                "flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-[13px]",
                active
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground",
              )}
            >
              {column.id === "needs_input" && count > 0 && (
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
              )}
              {column.name}
              {count > 0 && (
                <span className={cn("text-xs", active ? "opacity-70" : "opacity-60")}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Selected column */}
      <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto pb-4">
        {tasks.map((task) => (
          <MobileTaskCard
            key={task.conversation_id}
            task={task}
            lanes={board.lanes}
            onOpen={openTask}
            onStart={startTask}
            onMarkDone={markDone}
            onReopen={reopen}
            onMoveToLane={moveToLane}
            onRemoveFromBoard={removeFromBoard}
            onDeleteDraft={deleteDraft}
          />
        ))}
        {tasks.length === 0 && (
          <p className="py-6 text-center text-xs text-muted-foreground">Nothing here</p>
        )}
        {isLane && (
          <button
            onClick={() => onAddTask(selectedId)}
            className="flex items-center gap-1 rounded-md px-2 py-2 text-xs text-muted-foreground/70 active:bg-muted"
          >
            <RiAddLine className="h-3.5 w-3.5" />
            Task
          </button>
        )}
      </div>
    </div>
  );
}
