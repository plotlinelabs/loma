"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  basePath,
  deleteConversation,
  updateTask,
  createTaskTag,
  type BoardLane,
  type Task,
  type TasksBoardResponse,
} from "@/lib/api";

export const SYSTEM_COLUMNS = [
  { id: "working", name: "Working" },
  { id: "needs_input", name: "Needs input" },
  { id: "done", name: "Done" },
];

const NEEDS_INPUT_COLUMN = SYSTEM_COLUMNS.find((c) => c.id === "needs_input")!;
const TRAILING_SYSTEM_COLUMNS = SYSTEM_COLUMNS.filter((c) => c.id !== "needs_input");

export interface TaskBoardActionsOptions {
  board: TasksBoardResponse;
  /** Optimistically replace board state; server truth reconciles via polling. */
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onEditDraft: (task: Task) => void;
  onError: (message: string | null) => void;
  /** Desktop opens active cards in a new tab; the mobile/standalone PWA
   * navigates in place (window.open opens an in-app browser there). */
  openActiveInNewTab?: boolean;
  selectedTagIds?: string[];
}

/** Shared board state derivations + optimistic mutations, used by both the
 * desktop kanban (TaskBoard) and the mobile inbox (MobileTaskBoard). */
export function useTaskBoardActions({
  board, onBoardChange, onRefresh, onEditDraft, onError,
  openActiveInNewTab = true, selectedTagIds = [],
}: TaskBoardActionsOptions) {
  const router = useRouter();

  const laneIds = useMemo(() => board.lanes.map((lane) => lane.id), [board.lanes]);
  // Needs input sits leftmost — the attention column is the first thing you
  // see — then the staging lanes, then Working/Done.
  const columns: Array<BoardLane | { id: string; name: string }> = useMemo(
    () => [NEEDS_INPUT_COLUMN, ...board.lanes, ...TRAILING_SYSTEM_COLUMNS],
    [board.lanes],
  );

  const tasksByColumn = useMemo(() => {
    const map: Record<string, Task[]> = {};
    for (const column of columns) map[column.id] = [];
    for (const task of board.tasks) {
      if (selectedTagIds.length > 0 && !selectedTagIds.some((id) => (task.task_tag_ids || []).includes(id))) continue;
      (map[task.column] ??= []).push(task);
    }
    // Rank-sort client-side so optimistic reorders move cards immediately
    // (the server emits an effective rank for every card).
    for (const list of Object.values(map)) {
      list.sort((a, b) => (a.task_rank ?? 0) - (b.task_rank ?? 0));
    }
    return map;
  }, [board.tasks, columns, selectedTagIds]);

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
        ...(task.task_status !== "todo" ? { task_status: "todo" as const } : {}),
        task_lane: laneId,
        ...(rank !== undefined ? { task_rank: rank } : {}),
      }),
    );

  const reorderInColumn = (task: Task, rank: number) =>
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

  const setTaskModel = (task: Task, model: string) =>
    mutate(
      (tasks) => tasks.map((t) => t.conversation_id === task.conversation_id ? { ...t, model } : t),
      () => updateTask(task.conversation_id, { model }),
    );

  const setTaskTags = (task: Task, task_tag_ids: string[]) =>
    mutate(
      (tasks) => tasks.map((t) => t.conversation_id === task.conversation_id ? { ...t, task_tag_ids } : t),
      () => updateTask(task.conversation_id, { task_tag_ids }),
    );

  const createAndAssignTag = async (task: Task, name: string) => {
    const snapshot = board;
    try {
      const { tag } = await createTaskTag(name);
      const nextIds = [...(task.task_tag_ids || []), tag.id];
      onBoardChange({
        ...board, tags: [...board.tags, tag],
        tasks: board.tasks.map((t) => t.conversation_id === task.conversation_id ? { ...t, task_tag_ids: nextIds } : t),
      });
      await updateTask(task.conversation_id, { task_tag_ids: nextIds });
      onRefresh();
    } catch (e) {
      onBoardChange(snapshot);
      onError(e instanceof Error ? e.message : "Could not create tag");
    }
  };

  const openTask = (task: Task) => {
    // Only unstarted drafts open the details editor; anything with history
    // (including chats parked in a lane) opens the conversation.
    if (task.task_status === "todo" && !task.status) {
      onEditDraft(task);
      return;
    }
    const url = `${basePath}/chat?continue=${task.conversation_id}`;
    // Running / Needs input / Done cards open in a new tab on desktop so the
    // board stays put; parked lane chats keep in-place navigation.
    const isActiveColumn =
      task.column === "working" || task.column === "needs_input" || task.column === "done";
    if (isActiveColumn && openActiveInNewTab) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      router.push(url);
    }
  };

  return {
    laneIds,
    columns,
    tasksByColumn,
    startTask,
    markDone,
    reopen,
    moveToLane,
    reorderInColumn,
    removeFromBoard,
    deleteDraft,
    setTaskModel,
    setTaskTags,
    createAndAssignTag,
    openTask,
  };
}
