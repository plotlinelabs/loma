"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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
import { basePath, deleteConversation, updateTask, type BoardLane, type Task, type TasksBoardResponse } from "@/lib/api";
import { canMove, rankBetween } from "./transitions";
import { TaskColumn } from "./TaskColumn";
import { TaskCard } from "./TaskCard";

const SYSTEM_COLUMNS = [
  { id: "working", name: "Working" },
  { id: "needs_input", name: "Needs input" },
  { id: "done", name: "Done" },
];

interface TaskBoardProps {
  board: TasksBoardResponse;
  /** Optimistically replace board state; server truth reconciles via polling. */
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onEditDraft: (task: Task) => void;
  /** Open the new-task dialog with a lane preselected. */
  onAddTask: (laneId: string) => void;
  onError: (message: string | null) => void;
}

export function TaskBoard({ board, onBoardChange, onRefresh, onEditDraft, onAddTask, onError }: TaskBoardProps) {
  const router = useRouter();
  const [activeTask, setActiveTask] = useState<Task | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const laneIds = useMemo(() => board.lanes.map((lane) => lane.id), [board.lanes]);
  const columns: Array<BoardLane | { id: string; name: string }> = useMemo(
    () => [...board.lanes, ...SYSTEM_COLUMNS],
    [board.lanes],
  );

  const tasksByColumn = useMemo(() => {
    const map: Record<string, Task[]> = {};
    for (const column of columns) map[column.id] = [];
    for (const task of board.tasks) {
      (map[task.column] ??= []).push(task);
    }
    return map;
  }, [board.tasks, columns]);

  const startTask = (task: Task) => {
    router.push(`${basePath}/chat?continue=${task.conversation_id}&start=1`);
  };

  const mutate = async (
    apply: (tasks: Task[]) => Task[],
    request: () => Promise<unknown>,
  ) => {
    const snapshot = board;
    onError(null);
    onBoardChange({ ...board, tasks: apply(board.tasks) });
    try {
      await request();
      onRefresh();
    } catch (e) {
      onBoardChange(snapshot);
      onError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const markDone = (task: Task) =>
    mutate(
      (tasks) => tasks.map((t) =>
        t.conversation_id === task.conversation_id
          ? { ...t, task_status: "done" as const, column: "done" }
          : t),
      () => updateTask(task.conversation_id, { task_status: "done" }),
    );

  const reopen = (task: Task) =>
    mutate(
      (tasks) => tasks.map((t) =>
        t.conversation_id === task.conversation_id
          ? { ...t, task_status: "active" as const, column: t.status === "running" ? "working" : "needs_input" }
          : t),
      () => updateTask(task.conversation_id, { task_status: "active" }),
    );

  // Moving into a staging lane also *parks* active tasks (todo + lane) so a
  // needs-input chat can be shelved and recontinued later.
  const moveToLane = (task: Task, laneId: string, rank?: number) =>
    mutate(
      (tasks) => tasks.map((t) =>
        t.conversation_id === task.conversation_id
          ? {
              ...t,
              task_status: "todo" as const,
              task_lane: laneId,
              column: laneId,
              task_rank: rank ?? t.task_rank,
            }
          : t),
      () => updateTask(task.conversation_id, {
        ...(task.task_status === "active" ? { task_status: "todo" as const } : {}),
        task_lane: laneId,
        ...(rank !== undefined ? { task_rank: rank } : {}),
      }),
    );

  const reorderInLane = (task: Task, rank: number) =>
    mutate(
      (tasks) => tasks
        .map((t) => (t.conversation_id === task.conversation_id ? { ...t, task_rank: rank } : t)),
      () => updateTask(task.conversation_id, { task_rank: rank }),
    );

  const removeFromBoard = (task: Task) =>
    mutate(
      (tasks) => tasks.filter((t) => t.conversation_id !== task.conversation_id),
      () => updateTask(task.conversation_id, { task_status: null }),
    );

  const deleteDraft = (task: Task) =>
    mutate(
      (tasks) => tasks.filter((t) => t.conversation_id !== task.conversation_id),
      () => deleteConversation(task.conversation_id),
    );

  const openTask = (task: Task) => {
    // Only unstarted drafts open the details editor; anything with history
    // (including chats parked in a lane) opens the conversation.
    if (task.task_status === "todo" && !task.status) {
      onEditDraft(task);
    } else {
      router.push(`${basePath}/chat?continue=${task.conversation_id}`);
    }
  };

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

    if (target === "working") {
      startTask(task);
      return;
    }
    if (target === "done") {
      markDone(task);
      return;
    }

    // Staged lane: position relative to the card we dropped on (or lane end).
    const laneTasks = (tasksByColumn[target] ?? []).filter(
      (t) => t.conversation_id !== task.conversation_id,
    );
    let index = laneTasks.findIndex((t) => t.conversation_id === overId);
    if (index === -1) index = laneTasks.length;
    const before = index > 0 ? laneTasks[index - 1].task_rank : null;
    const after = index < laneTasks.length ? laneTasks[index].task_rank : null;
    const rank = rankBetween(before, after);

    if (target === task.column) {
      reorderInLane(task, rank);
    } else {
      moveToLane(task, target, rank);
    }
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
                onOpen={openTask}
                onStart={startTask}
                onMarkDone={markDone}
                onReopen={reopen}
                onMoveToLane={moveToLane}
                onRemoveFromBoard={removeFromBoard}
                onDeleteDraft={deleteDraft}
              />
            ))}
          </TaskColumn>
        ))}
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="rounded-md border bg-card px-3 py-2 text-[13px] shadow-md">
            {activeTask.title || activeTask.prompt}
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
