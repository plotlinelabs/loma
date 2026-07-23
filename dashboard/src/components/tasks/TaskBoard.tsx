"use client";

import { useState } from "react";
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
import { canMove, rankBetween } from "./transitions";
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
  /** Open the new-task dialog with a lane preselected. */
  onAddTask: (laneId: string) => void;
  onError: (message: string | null) => void;
  selectedTagIds: string[];
}

export function TaskBoard({ board, onBoardChange, onRefresh, onEditDraft, onAddTask, onError, selectedTagIds }: TaskBoardProps) {
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
  } = useTaskBoardActions({ board, onBoardChange, onRefresh, onEditDraft, onError, selectedTagIds });

  const resolveColumn = (overId: string): string | null => {
    if (tasksByColumn[overId] !== undefined) return overId;
    const overTask = board.tasks.find((t) => t.conversation_id === overId);
    return overTask ? overTask.column : null;
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
    if (!target || !canMove(task, task.column, target, laneIds)) return;

    // Position relative to the card we dropped on (or column end).
    const columnTasks = (tasksByColumn[target] ?? []).filter(
      (t) => t.conversation_id !== task.conversation_id,
    );
    let index = columnTasks.findIndex((t) => t.conversation_id === overId);
    if (index === -1) index = columnTasks.length;
    const before = index > 0 ? columnTasks[index - 1].task_rank : null;
    const after = index < columnTasks.length ? columnTasks[index].task_rank : null;
    const rank = rankBetween(before, after);

    // Same-column drop = manual reorder, in any column — check this before
    // the transition branches or a reorder inside Working would start a task.
    if (target === task.column) {
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
    if (target === "needs_input") {
      // Only reachable from Done (see canMove) — same effect as Reopen.
      reopen(task);
      return;
    }

    moveToLane(task, target, rank);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="flex flex-1 gap-4 overflow-x-auto pb-4">
        {columns.map((column) => (
          <TaskColumn
            key={column.id}
            id={column.id}
            name={column.name}
            tasks={tasksByColumn[column.id] ?? []}
            droppable={
              activeTask
                ? canMove(activeTask, activeTask.column, column.id, laneIds)
                : undefined
            }
            onAddTask={laneIds.includes(column.id) ? () => onAddTask(column.id) : undefined}
          >
            {(tasksByColumn[column.id] ?? []).map((task) => (
              <TaskCard
                key={task.conversation_id}
                task={task}
                lanes={board.lanes}
                tags={board.tags}
                models={models}
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
              />
            ))}
          </TaskColumn>
        ))}
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="rounded-md border bg-card px-3 py-2 text-[13px] shadow-md">
            <div className="line-clamp-2 break-words">{activeTask.title || activeTask.prompt}</div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
