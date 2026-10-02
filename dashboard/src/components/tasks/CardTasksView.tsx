"use client";

import { RiPauseLine, RiStackLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import ClientTimestamp from "@/components/ClientTimestamp";
import type { Task, TaskCardItem, TasksBoardResponse } from "@/lib/api";
import { priorityDisplay, taskDot, taskTimestamp } from "./taskDisplay";
import { TaskDeadlineBadge } from "./TaskDeadline";
import { AssigneeBadge, useBoardExtras } from "./boardExtras";
import { matchesFilters } from "./cardFilters";

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

interface CardTasksViewProps {
  board: TasksBoardResponse;
  onOpenTask: (task: Task) => void;
  onOpenCard: (card: TaskCardItem) => void;
  /** Park a Needs input task in Pending. Omitted when the board is read-only. */
  onPauseTask?: (task: Task) => void;
  includedTagIds?: string[];
  excludedTagIds?: string[];
}

function TaskRow({ task, card, onOpenTask, onOpenCard, onPauseTask, me }: {
  task: Task;
  card: TaskCardItem | undefined;
  onOpenTask: (task: Task) => void;
  onOpenCard: (card: TaskCardItem) => void;
  onPauseTask?: (task: Task) => void;
  me: string | null;
}) {
  const dot = taskDot(task);
  const priority = priorityDisplay(task.task_priority);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpenTask(task)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenTask(task); }
      }}
      data-task-id={task.conversation_id}
      className="group rounded-xl border border-border bg-card px-3.5 py-3 cursor-pointer hover:border-input transition-colors"
    >
      <div className="flex items-start gap-2">
        {dot && <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />}
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 break-words text-[14px] font-medium leading-5">
            {task.title || task.prompt || "New task"}
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
        {onPauseTask && task.column === "needs_input" && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onPauseTask(task); }}
            aria-label="Pause task"
            title="Pause: move to Pending and pick it up later"
            className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
          >
            <RiPauseLine className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

/** Card boards, inverted: every task from every card, grouped by status
 * (Pending, In progress, Needs input, Done). Each task links back to its card. */
export function CardTasksView({ board, onOpenTask, onOpenCard, onPauseTask, includedTagIds = [], excludedTagIds = [] }: CardTasksViewProps) {
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
    <div className="flex flex-1 gap-4 overflow-x-auto pb-4" data-view="tasks">
      {TASK_GROUPS.map((group) => {
        const tasks = groups[group.id];
        return (
          <div key={group.id} className="flex min-w-[220px] flex-1 basis-0 flex-col" data-group={group.name}>
            <div className="mb-2.5 flex items-baseline gap-2 px-2">
              <span className="text-[13px] font-semibold text-foreground">{group.name}</span>
              <span className="text-[11px] tabular-nums text-muted-foreground/80">{tasks.length}</span>
            </div>
            <div className="flex min-h-24 flex-1 flex-col gap-2 rounded-xl p-1.5 bg-foreground/[0.025]">
              {tasks.map((task) => (
                <TaskRow
                  key={task.conversation_id}
                  task={task}
                  card={task.task_card_id ? cardsById.get(task.task_card_id) : undefined}
                  onOpenTask={onOpenTask}
                  onOpenCard={onOpenCard}
                  onPauseTask={onPauseTask}
                  me={myEmail}
                />
              ))}
              {tasks.length === 0 && (
                <p className="px-2 py-3 text-center text-[12px] text-muted-foreground/70">No tasks</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
