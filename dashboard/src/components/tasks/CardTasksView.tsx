"use client";

import { useState } from "react";
import {
  DndContext, DragOverlay, PointerSensor, pointerWithin, useDraggable, useDroppable, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { RiStackLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import ClientTimestamp from "@/components/ClientTimestamp";
import type { Task, TaskCardItem, TasksBoardResponse } from "@/lib/api";
import { priorityDisplay, taskDot, taskTimestamp } from "./taskDisplay";
import { TaskDeadlineBadge } from "./TaskDeadline";
import { AssigneeBadge, useBoardExtras } from "./boardExtras";
import { StarButton } from "./TaskStar";
import { matchesFilters } from "./cardFilters";
import { canMove } from "./transitions";
import { useTaskBoardActions } from "./useTaskBoardActions";

/** Status groups of the Tasks view, in board order. */
export const TASK_GROUPS = [
  { id: "pending", name: "Pending" },
  { id: "in_progress", name: "In progress" },
  { id: "needs_input", name: "Needs input" },
  { id: "done", name: "Done" },
] as const;

export type TaskGroupId = (typeof TASK_GROUPS)[number]["id"];

/** Which group a task belongs to. Any lane column (todo, a custom lane...)
 * means it hasn't started yet, so it is Pending. */
export function taskGroup(task: Task): TaskGroupId {
  if (task.column === "working") return "in_progress";
  if (task.column === "needs_input") return "needs_input";
  if (task.column === "done") return "done";
  return "pending";
}

/** Board column a drop on `group` lands the task in. Pending means the task's
 * own staging lane (or the first one). In progress is never a drop target:
 * a task starts by sending its prompt from the chat. */
function targetColumn(task: Task, group: TaskGroupId, laneIds: string[]): string | null {
  if (group === "pending") {
    return task.task_lane && laneIds.includes(task.task_lane) ? task.task_lane : (laneIds[0] ?? null);
  }
  if (group === "in_progress") return null;
  return group;
}

/** Whether a task can be dropped on a group. Same rules as the classic board
 * (see transitions.ts), plus the two card-board specifics the backend allows:
 * a card task can be ticked done without a run, and only a task that has run
 * can go back to Needs input. */
export function canDropOnGroup(task: Task, group: TaskGroupId, laneIds: string[]): boolean {
  if (task.human_task || taskGroup(task) === group) return false;
  const to = targetColumn(task, group, laneIds);
  if (!to) return false;
  if (to === "done" && task.task_card_id) return true;
  if (to === "needs_input" && !task.status) return false;
  return canMove(task, task.column, to, laneIds);
}

interface CardTasksViewProps {
  board: TasksBoardResponse;
  onOpenTask: (task: Task) => void;
  onOpenCard: (card: TaskCardItem) => void;
  /** Optimistically replace board state; server truth reconciles via polling. */
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onError: (message: string | null) => void;
  /** View-only member of a shared board: no drag. */
  readOnly?: boolean;
  includedTagIds?: string[];
  excludedTagIds?: string[];
}

function TaskRow({ task, card, onOpenTask, onOpenCard, onToggleStar, me, draggable }: {
  task: Task;
  card: TaskCardItem | undefined;
  onOpenTask: (task: Task) => void;
  onOpenCard: (card: TaskCardItem) => void;
  onToggleStar: (task: Task) => void;
  me: string | null;
  draggable: boolean;
}) {
  const dot = taskDot(task);
  const priority = priorityDisplay(task.task_priority);
  const { setNodeRef, listeners, isDragging } = useDraggable({ id: task.conversation_id, disabled: !draggable });
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      role="button"
      tabIndex={0}
      onClick={() => onOpenTask(task)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenTask(task); }
      }}
      data-task-id={task.conversation_id}
      className={cn(
        "group rounded-xl border border-border bg-card px-3.5 py-3 cursor-pointer hover:border-input transition-colors touch-none",
        isDragging && "opacity-40",
      )}
    >
      <div className="flex items-start gap-2">
        {dot && <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1">
            <div className="line-clamp-2 min-w-0 flex-1 break-words text-[14px] font-medium leading-5">
              {task.title || task.prompt || "New task"}
            </div>
            <StarButton task={task} onToggle={onToggleStar}
              className={cn("h-5 w-5", !task.starred && "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100")} />
          </div>
          {card && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onOpenCard(card); }}
              title={`Open card: ${card.title}`}
              className="mt-1.5 inline-flex max-w-full items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] leading-4 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
            >
              <RiStackLine className="h-3 w-3 shrink-0" />
              <span className="truncate">{card.title}</span>
            </button>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] leading-4 text-muted-foreground">
            <ClientTimestamp iso={taskTimestamp(task)} variant="short" placeholder="—" />
            {priority && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">{priority.symbol} {priority.label}</span>
            )}
            <TaskDeadlineBadge task={task} />
            {task.assignee && <AssigneeBadge email={task.assignee} me={me} className="ml-auto" />}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Card boards, inverted: every task from every card, grouped by status
 * (Pending, In progress, Needs input, Done). Each task links back to its card. */
export function CardTasksView({
  board, onOpenTask, onOpenCard, onBoardChange, onRefresh, onError, readOnly = false,
  includedTagIds = [], excludedTagIds = [],
}: CardTasksViewProps) {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const { laneIds, markDone, reopen, moveToLane, toggleStar } = useTaskBoardActions({
    board, onBoardChange, onRefresh, onError, onEditDraft: () => {}, onOpenChat: onOpenTask,
  });

  // Dragging a task between groups changes its status, e.g. Needs input ->
  // Pending parks it (it stops counting as needing input until someone replies).
  const handleDragEnd = ({ over }: DragEndEvent) => {
    const task = activeTask;
    setActiveTask(null);
    if (!task || !over) return;
    const group = String(over.id) as TaskGroupId;
    if (!canDropOnGroup(task, group, laneIds)) return;
    if (group === "done") void markDone(task);
    else if (group === "needs_input") void reopen(task);
    else if (group === "pending") void moveToLane(task, targetColumn(task, group, laneIds)!);
  };

  const { myEmail, assignedToMe, cardFilters, filterMatch } = useBoardExtras();
  const cardsById = new Map((board.cards ?? []).map((card) => [card.card_id, card]));
  // Card field filters (and saved views) hide the tasks of cards they filter out.
  const fields = board.fields ?? [];
  const assigneesByCard: Record<string, string[]> = {};
  for (const task of board.tasks) {
    if (!task.task_card_id || !task.assignee || task.column === "done") continue;
    const list = (assigneesByCard[task.task_card_id] ??= []);
    if (!list.includes(task.assignee)) list.push(task.assignee);
  }
  const hiddenCards = new Set<string>();
  if (cardFilters.length > 0) {
    for (const card of cardsById.values()) {
      if (!matchesFilters(card, cardFilters, filterMatch, { fields, assigneesByCard })) hiddenCards.add(card.card_id);
    }
  }

  const groups: Record<TaskGroupId, Task[]> = { pending: [], in_progress: [], needs_input: [], done: [] };
  for (const task of board.tasks) {
    if (assignedToMe && task.assignee !== myEmail) continue;
    if (task.task_card_id && hiddenCards.has(task.task_card_id)) continue;
    const tagIds = task.task_tag_ids || [];
    if (includedTagIds.length && !includedTagIds.some((id) => tagIds.includes(id))) continue;
    if (excludedTagIds.some((id) => tagIds.includes(id))) continue;
    groups[taskGroup(task)].push(task);
  }

  return (
    <DndContext
      sensors={readOnly ? [] : sensors}
      collisionDetection={pointerWithin}
      onDragStart={({ active }: DragStartEvent) =>
        setActiveTask(board.tasks.find((t) => t.conversation_id === active.id) ?? null)}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="flex flex-1 gap-4 overflow-x-auto pb-4" data-view="tasks">
        {TASK_GROUPS.map((group) => (
          <TaskGroupColumn
            key={group.id}
            id={group.id}
            name={group.name}
            count={groups[group.id].length}
            droppable={activeTask ? canDropOnGroup(activeTask, group.id, laneIds) : undefined}
          >
            {groups[group.id].map((task) => (
              <TaskRow
                key={task.conversation_id}
                task={task}
                card={task.task_card_id ? cardsById.get(task.task_card_id) : undefined}
                onOpenTask={onOpenTask}
                onOpenCard={onOpenCard}
                onToggleStar={toggleStar}
                me={myEmail}
                draggable={!readOnly && !task.human_task}
              />
            ))}
          </TaskGroupColumn>
        ))}
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="rounded-xl border bg-card px-3.5 py-3 text-[14px] font-medium shadow-md">
            <div className="line-clamp-2 break-words">{activeTask.title || activeTask.prompt || "New task"}</div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}

function TaskGroupColumn({ id, name, count, droppable, children }: {
  id: TaskGroupId;
  name: string;
  count: number;
  /** Whether the current drag can drop here (undefined = no drag active). */
  droppable?: boolean;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id, disabled: droppable === false });
  return (
    <div className="flex min-w-[220px] flex-1 basis-0 flex-col" data-group={name}>
      <div className="mb-2.5 flex items-baseline gap-2 px-2">
        <span className="text-[13px] font-semibold text-foreground">{name}</span>
        <span className="text-[11px] tabular-nums text-muted-foreground/80">{count}</span>
      </div>
      <div
        ref={setNodeRef}
        className={cn(
          "flex min-h-24 flex-1 flex-col gap-2 rounded-xl p-1.5 bg-foreground/[0.025] transition-colors",
          droppable === false && "opacity-50",
          droppable && isOver && "bg-primary/10 ring-1 ring-primary/40",
        )}
      >
        {children}
        {count === 0 && (
          <p className="px-2 py-3 text-center text-[12px] text-muted-foreground/70">No tasks</p>
        )}
      </div>
    </div>
  );
}
