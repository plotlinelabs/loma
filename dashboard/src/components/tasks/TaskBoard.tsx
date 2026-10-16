"use client";

import { Fragment, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import type { Task, TasksBoardResponse } from "@/lib/api";
import { canMove, isStoppedColumn, rankBetween } from "./transitions";
import { useTaskBoardActions } from "./useTaskBoardActions";
import { TaskColumn } from "./TaskColumn";
import { TaskCard } from "./TaskCard";
import { useAgentModels } from "@/hooks/useAgentModels";

interface TaskBoardProps {
  board: TasksBoardResponse;
  /** Optimistically replace board state; server truth reconciles via polling. */
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onEditDraft: (task: Task) => void;
  /** Create a new task and open it in the side drawer. */
  onAddTask: (laneId: string) => void;
  /** Open a non-draft task's chat in the side drawer instead of a new tab. */
  onOpenChat?: (task: Task) => void;
  onError: (message: string | null) => void;
  includedTagIds: string[];
  excludedTagIds: string[];
  /** View-only member of a shared board: no drag, adds or card actions. */
  readOnly?: boolean;
  /** Load more Done tasks; set only while the server has more. */
  onLoadMoreDone?: () => void;
}

/** Needs you and Ready to review share one column on the board. */
const STOPPED_COLUMN = { id: "needs_input", name: "Needs you · Ready to review" };

/** The board column a task's real column is shown in. */
const displayColumn = (column: string) => (isStoppedColumn(column) ? STOPPED_COLUMN.id : column);

export function TaskBoard({ board, onBoardChange, onRefresh, onEditDraft, onAddTask, onOpenChat, onError, includedTagIds, excludedTagIds, readOnly = false, onLoadMoreDone }: TaskBoardProps) {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const { models } = useAgentModels();

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const {
    laneIds, columns, tasksByColumn,
    startTask, markDone, reopen, moveToLane, reorderInColumn,
    removeFromBoard, deleteDraft, forkTask, openTask, setTaskModel, setTaskPriority, setTaskDeadline, setTaskTags, createAndAssignTag,
    toggleStar,
  } = useTaskBoardActions({ board, onBoardChange, onRefresh, onEditDraft, onError, onOpenChat, includedTagIds, excludedTagIds });

  const displayColumns = columns
    .filter((column) => column.id !== "review")
    .map((column) => (column.id === STOPPED_COLUMN.id ? STOPPED_COLUMN : column));
  const tasksByDisplayColumn: Record<string, Task[]> = {
    ...tasksByColumn,
    [STOPPED_COLUMN.id]: [...(tasksByColumn.needs_input ?? []), ...(tasksByColumn.review ?? [])],
  };
  const firstReviewId = (tasksByColumn.needs_input ?? []).length > 0 ? tasksByColumn.review?.[0]?.conversation_id : undefined;

  const resolveColumn = (overId: string): string | null => {
    if (tasksByDisplayColumn[overId] !== undefined) return overId;
    const overTask = board.tasks.find((t) => t.conversation_id === overId);
    return overTask ? displayColumn(overTask.column) : null;
  };

  const handleDragStart = (event: DragStartEvent) => {
    const task = board.tasks.find((t) => t.conversation_id === event.active.id);
    setActiveTask(task ?? null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const task = activeTask;
    setActiveTask(null);
    if (!task || !event.over) return;

    const overId = String(event.over.id);
    const target = resolveColumn(overId);
    const from = displayColumn(task.column);
    if (!target || !canMove(task, from, target, laneIds)) return;

    // Position relative to the card we dropped on (or column end). Ranks are
    // per real column, so a reorder in the shared column uses the task's own.
    const rankColumn = target === from ? task.column : target;
    const columnTasks = (tasksByColumn[rankColumn] ?? []).filter(
      (t) => t.conversation_id !== task.conversation_id,
    );
    let index = columnTasks.findIndex((t) => t.conversation_id === overId);
    if (index === -1) index = columnTasks.length;
    const before = index > 0 ? columnTasks[index - 1].task_rank : null;
    const after = index < columnTasks.length ? columnTasks[index].task_rank : null;
    const rank = rankBetween(before, after);

    // Same-column drop = manual reorder, in any column — check this before
    // the transition branches or a reorder inside Working would start a task.
    if (target === from) {
      reorderInColumn(task, rank);
      return;
    }

    if (target === "working") {
      startTask(task);
      return;
    }
    if (target === "done") {
      markDone(task);
      return;
    }
    if (isStoppedColumn(target)) {
      // Only reachable from Done (see canMove) — same effect as Reopen.
      reopen(task);
      return;
    }

    moveToLane(task, target, rank);
  };

  return (
    <DndContext
      sensors={readOnly ? [] : sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="flex flex-1 gap-4 overflow-x-auto pb-4">
        {displayColumns.map((column) => (
          <TaskColumn
            key={column.id}
            id={column.id}
            name={column.name}
            tasks={tasksByDisplayColumn[column.id] ?? []}
            droppable={
              activeTask
                ? canMove(activeTask, displayColumn(activeTask.column), column.id, laneIds)
                : undefined
            }
            onAddTask={!readOnly && laneIds.includes(column.id) ? () => onAddTask(column.id) : undefined}
            onLoadMore={column.id === "done" ? onLoadMoreDone : undefined}
          >
            {(tasksByDisplayColumn[column.id] ?? []).map((task) => (
              <Fragment key={task.conversation_id}>
                {task.conversation_id === firstReviewId && (
                  <div className="px-2 pt-2 text-xs font-medium text-muted-foreground" data-testid="task-column-review-divider">
                    Ready to review · {tasksByColumn.review?.length ?? 0}
                  </div>
                )}
                <TaskCard
                  task={task}
                  lanes={board.lanes}
                  tags={board.tags}
                  models={models}
                  readOnly={readOnly}
                  onOpen={openTask}
                  onStart={startTask}
                  onMarkDone={markDone}
                  onReopen={reopen}
                  onMoveToLane={moveToLane}
                  onRemoveFromBoard={removeFromBoard}
                  onDeleteDraft={deleteDraft}
                  onFork={forkTask}
                  onSetModel={setTaskModel}
                  onSetPriority={setTaskPriority}
                  onSetDeadline={setTaskDeadline}
                  onSetTags={setTaskTags}
                  onCreateTag={createAndAssignTag}
                  onToggleStar={toggleStar}
                />
              </Fragment>
            ))}
          </TaskColumn>
        ))}
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="rounded-lg border bg-card px-3 py-2 text-[13px] shadow-md">
            <div className="line-clamp-2 break-words">{activeTask.title || activeTask.prompt || "New task"}</div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
