"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  RiArrowDownSLine, RiArrowGoBackLine, RiArrowRightSLine, RiArrowUpSLine, RiCheckLine,
  RiExternalLinkLine, RiMoreLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  basePath, canRunTask, fetchConversation, starTask, unstarTask, updateTask,
  type BoardLane, type Task, type TaskBoardRole, type TasksBoardResponse,
} from "@/lib/api";
import { useAgentModels } from "@/hooks/useAgentModels";
import { cn } from "@/lib/utils";
import { AssigneeBadge, AssigneeSelect, useBoardExtras } from "./boardExtras";
import { EditableTaskTitle, TaskConversation } from "./TaskChatDrawer";
import { TaskAge, TaskSummary, WaitingChip } from "./TaskCardParts";
import { TaskCardMenu } from "./TaskCardMenu";
import { DeadlineMenuItems, TaskDeadlineBadge } from "./TaskDeadline";
import { PriorityMenuItems } from "./TaskPriority";
import { StarButton } from "./TaskStar";
import { WAITING_LABELS, deadlineDisplay, isDraft, isParked, priorityDisplay } from "./taskDisplay";
import { isStoppedColumn } from "./transitions";
import { useTaskBoardActions } from "./useTaskBoardActions";

/** Sections of the list, in triage order. Lanes slot in after Running. */
const SECTION_META: Record<string, { title: string; dot: string; tone?: string }> = {
  needs_input: { title: "Needs you", dot: "bg-amber-500", tone: "text-amber-800 dark:text-amber-300" },
  review: { title: "Ready to review", dot: "bg-emerald-500" },
  working: { title: "Running", dot: "bg-blue-500" },
  done: { title: "Done", dot: "bg-muted-foreground/50" },
};
/** Ready to review shows this many before "Show N more". */
const REVIEW_PREVIEW = 3;

interface Section { id: string; title: string; dot: string; tone?: string; tasks: Task[]; lane?: BoardLane }

interface TaskTriageViewProps {
  board: TasksBoardResponse;
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onError: (message: string | null) => void;
  includedTagIds: string[];
  excludedTagIds: string[];
  readOnly?: boolean;
  /** Your role on this board, for who may message a task. */
  boardRole?: TaskBoardRole | null;
  onLoadMoreDone?: () => void;
  /** Task to select, e.g. a new draft or one voice mode opened. */
  selectId?: string | null;
  /** The task shown in the pane, for the page (voice mode reloads it). */
  onSelectedChange?: (task: Task | null) => void;
  /** Bumped when something outside (voice mode) changed the open task. */
  reloadToken?: number;
  /** Rendered under the list (the quick-add composer). */
  footer?: React.ReactNode;
}

/** Desktop "List" view of a task board: sections on the left in the order
 * work needs you, the open task on the right with its controls. */
export function TaskTriageView({
  board, onBoardChange, onRefresh, onError, includedTagIds, excludedTagIds,
  readOnly = false, boardRole, onLoadMoreDone, selectId, onSelectedChange, reloadToken = 0, footer,
}: TaskTriageViewProps) {
  const { models } = useAgentModels();
  const { myEmail } = useBoardExtras();
  const actions = useTaskBoardActions({
    board, onBoardChange, onRefresh, onEditDraft: () => {}, onError, includedTagIds, excludedTagIds,
  });
  const { tasksByColumn, markDone, reopen } = actions;

  const [showAllReview, setShowAllReview] = useState(false);
  const [showDone, setShowDone] = useState(false);

  const sections: Section[] = useMemo(() => {
    const meta = (id: string) => SECTION_META[id];
    const list: Section[] = [
      { id: "needs_input", ...meta("needs_input"), tasks: tasksByColumn.needs_input ?? [] },
      { id: "review", ...meta("review"), tasks: tasksByColumn.review ?? [] },
      { id: "working", ...meta("working"), tasks: tasksByColumn.working ?? [] },
      ...board.lanes.map((lane) => ({
        id: lane.id, title: board.lanes.length === 1 && lane.name.trim().toLowerCase() === "todo" ? "Up next" : lane.name,
        dot: "border-[1.5px] border-muted-foreground/60 bg-transparent", tasks: tasksByColumn[lane.id] ?? [], lane,
      })),
      { id: "done", ...meta("done"), tasks: tasksByColumn.done ?? [] },
    ];
    return list;
  }, [tasksByColumn, board.lanes]);

  // What the list shows, in order: the keyboard walks this.
  const visibleBySection = useMemo(() => sections.map((section) => {
    if (section.id === "review" && !showAllReview) return section.tasks.slice(0, REVIEW_PREVIEW);
    if (section.id === "done" && !showDone) return [];
    return section.tasks;
  }), [sections, showAllReview, showDone]);
  const order = useMemo(() => visibleBySection.flat(), [visibleBySection]);

  // ── Selection (kept in the URL as ?task= so it survives a reload) ──
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("task"));
  // A task the page asks for (new draft, voice) wins once per request.
  const [seenSelectId, setSeenSelectId] = useState(selectId);
  if (selectId !== seenSelectId) {
    setSeenSelectId(selectId);
    if (selectId) setSelectedId(selectId);
  }
  const allTasks = useMemo(() => sections.flatMap((s) => s.tasks), [sections]);
  // Nothing (or something gone) selected: the first task, which is the first
  // one that needs you when any does.
  const selected = allTasks.find((t) => t.conversation_id === selectedId) ?? order[0] ?? null;
  const activeId = selected?.conversation_id ?? null;
  useEffect(() => { onSelectedChange?.(selected); }, [selected, onSelectedChange]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (activeId) url.searchParams.set("task", activeId);
    else url.searchParams.delete("task");
    window.history.replaceState(window.history.state, "", url);
  }, [activeId]);

  const step = useCallback((delta: number) => {
    if (!order.length) return;
    const index = order.findIndex((t) => t.conversation_id === activeId);
    const next = order[Math.min(order.length - 1, Math.max(0, (index < 0 ? 0 : index) + delta))];
    setSelectedId(next.conversation_id);
    document.getElementById(`triage-row-${next.conversation_id}`)?.scrollIntoView({ block: "nearest" });
  }, [order, activeId]);

  const canMarkDone = (task: Task) => !readOnly && !task.human_task &&
    (task.column === "working" || isStoppedColumn(task.column) || isParked(task));
  const finish = useCallback((task: Task) => {
    // Move on to the next task before this one leaves the list.
    const index = order.findIndex((t) => t.conversation_id === task.conversation_id);
    const next = order[index + 1] ?? order[index - 1] ?? null;
    void markDone(task);
    setSelectedId(next?.conversation_id ?? null);
  }, [order, markDone]);

  const paneRef = useRef<HTMLDivElement>(null);
  const focusReply = () => paneRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();

  // J/K (or arrows) move, E marks done, R replies. Typing anywhere wins.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      // Escape from the reply box hands the keyboard back to the list.
      if (event.key === "Escape" && target instanceof HTMLTextAreaElement && paneRef.current?.contains(target)) {
        target.blur();
        document.querySelector<HTMLButtonElement>(`#triage-row-${CSS.escape(activeId ?? "")} button`)?.focus({ preventScroll: true });
        return;
      }
      if (target?.closest("input, textarea, select, [contenteditable='true'], [role='dialog'], [role='menu']")) return;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "arrowdown") { event.preventDefault(); step(1); }
      else if (key === "k" || key === "arrowup") { event.preventDefault(); step(-1); }
      else if (key === "e" && selected && canMarkDone(selected)) { event.preventDefault(); finish(selected); }
      else if (key === "r" && selected) { event.preventDefault(); focusReply(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const replaceTask = (updated: Task) => onBoardChange({
    ...board,
    tasks: board.tasks.map((t) => t.conversation_id === updated.conversation_id ? updated : t),
  });

  const sectionOf = (task: Task) => sections.find((s) => s.tasks.some((t) => t.conversation_id === task.conversation_id));

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden rounded-2xl border border-border bg-card">
      <div className="flex w-[420px] shrink-0 flex-col border-r border-border bg-background">
        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2" role="list" aria-label="Tasks">
          {sections.map((section, i) => {
            const shown = visibleBySection[i];
            const hidden = section.tasks.length - shown.length;
            const isNeedsYou = section.id === "needs_input";
            if (!section.tasks.length && !isNeedsYou) return null;
            return (
              <section key={section.id} aria-label={section.title} className="mb-2">
                {section.id === "done" ? (
                  <button type="button" onClick={() => setShowDone((v) => !v)} aria-expanded={showDone}
                    className="flex h-10 w-full items-center gap-2 rounded-lg px-2.5 text-[14px] font-semibold text-foreground/80 hover:bg-muted/60">
                    {showDone ? <RiArrowDownSLine className="h-4 w-4" /> : <RiArrowRightSLine className="h-4 w-4" />}
                    Done <span className="font-normal tabular-nums text-muted-foreground">{section.tasks.length}{onLoadMoreDone ? "+" : ""}</span>
                  </button>
                ) : (
                  <h2 className={cn("flex items-center gap-2 px-2.5 pb-1 pt-2 text-[13px] font-semibold", section.tone)}>
                    <span aria-hidden className={cn("h-2 w-2 rounded-full", section.dot)} />
                    {section.title}
                    <span className="font-normal tabular-nums text-muted-foreground">{section.tasks.length}</span>
                  </h2>
                )}
                {isNeedsYou && !section.tasks.length && (
                  <p className="px-2.5 pb-2 text-[14px] text-muted-foreground">Nothing needs you right now.</p>
                )}
                {shown.map((task) => (
                  <TriageRow key={task.conversation_id} task={task} me={myEmail}
                    selected={task.conversation_id === activeId}
                    onSelect={() => setSelectedId(task.conversation_id)}
                    onStart={isDraft(task) && !readOnly ? () => { setSelectedId(task.conversation_id); setTimeout(focusReply, 300); } : undefined} />
                ))}
                {section.id === "review" && hidden > 0 && (
                  <button type="button" onClick={() => setShowAllReview(true)}
                    className="ml-11 h-8 rounded-md px-2 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground">
                    Show {hidden} more
                  </button>
                )}
                {section.id === "done" && showDone && onLoadMoreDone && (
                  <button type="button" onClick={onLoadMoreDone}
                    className="ml-11 h-8 rounded-md px-2 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground">
                    Load more
                  </button>
                )}
              </section>
            );
          })}
        </div>
        {footer}
      </div>

      <div ref={paneRef} className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <TaskPane
            key={selected.conversation_id}
            task={selected}
            board={board}
            boardRole={boardRole}
            readOnly={readOnly}
            models={models}
            actions={actions}
            position={(() => {
              const section = sectionOf(selected);
              const index = section ? section.tasks.findIndex((t) => t.conversation_id === selected.conversation_id) : -1;
              return section && index >= 0 ? { index: index + 1, total: section.tasks.length } : null;
            })()}
            canMarkDone={canMarkDone(selected)}
            onMarkDone={() => finish(selected)}
            onReopen={() => void reopen(selected)}
            onPrev={() => step(-1)}
            onNext={() => step(1)}
            onTaskChange={replaceTask}
            onRefresh={onRefresh}
            onError={onError}
            reloadToken={reloadToken}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
            <p className="text-[16px] font-medium">No task selected</p>
            <p className="max-w-sm text-[14px] text-muted-foreground">
              Pick a task on the left, or describe a new one below and Loma starts right away.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function TriageRow({ task, me, selected, onSelect, onStart }: {
  task: Task; me: string | null; selected: boolean; onSelect: () => void; onStart?: () => void;
}) {
  const priority = priorityDisplay(task.task_priority);
  const deadline = deadlineDisplay(task.task_deadline);
  const blocked = task.column === "needs_input";
  return (
    <div id={`triage-row-${task.conversation_id}`} role="listitem"
      className={cn("group flex items-start gap-1 rounded-xl", selected ? "bg-card shadow-sm ring-1 ring-border" : "hover:bg-muted/50")}>
      <button type="button" onClick={onSelect} aria-current={selected ? "true" : undefined}
        className="flex min-w-0 flex-1 gap-3 rounded-xl px-2.5 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span aria-hidden className={cn("mt-[7px] h-2 w-2 shrink-0 rounded-full",
          task.column === "working" ? "bg-blue-500 animate-pulse"
            : blocked ? (task.waiting_reason === "failed" || task.waiting_reason === "stopped" ? "bg-red-500" : "bg-amber-500")
            : task.column === "review" ? "bg-emerald-500"
            : task.column === "done" ? "bg-muted-foreground/40"
            : "border-[1.5px] border-muted-foreground/50")} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-baseline gap-2">
            <span className={cn("min-w-0 flex-1 truncate text-[15px] leading-[21px]", blocked ? "font-semibold" : "font-medium")}>
              {task.title || task.prompt || "New task"}
            </span>
            <TaskAge task={task} />
          </span>
          <TaskSummary task={task} className="line-clamp-1" />
          {(blocked || priority || deadline || task.assignee) && (
            <span className="mt-1 flex flex-wrap items-center gap-1.5">
              <WaitingChip task={task} />
              {priority && <span className="rounded-md bg-muted px-2 py-0.5 text-xs leading-5 text-muted-foreground">{priority.symbol} {priority.label}</span>}
              <TaskDeadlineBadge task={task} />
              {task.assignee && <AssigneeBadge email={task.assignee} me={me} />}
            </span>
          )}
        </span>
      </button>
      {onStart && (
        <Button variant="outline" size="sm" className="mr-2 mt-2.5 h-8 shrink-0" onClick={onStart}>Start</Button>
      )}
    </div>
  );
}

function TaskPane({
  task, board, boardRole, readOnly, models, actions, position, canMarkDone,
  onMarkDone, onReopen, onPrev, onNext, onTaskChange, onRefresh, onError, reloadToken,
}: {
  task: Task;
  board: TasksBoardResponse;
  boardRole?: TaskBoardRole | null;
  readOnly: boolean;
  models: ReturnType<typeof useAgentModels>["models"];
  actions: ReturnType<typeof useTaskBoardActions>;
  position: { index: number; total: number } | null;
  canMarkDone: boolean;
  onMarkDone: () => void;
  onReopen: () => void;
  onPrev: () => void;
  onNext: () => void;
  onTaskChange: (task: Task) => void;
  onRefresh: () => void;
  onError: (message: string | null) => void;
  reloadToken: number;
}) {
  const { myEmail, role, assignable, onMoveToBoard } = useBoardExtras();
  const [menuOpen, setMenuOpen] = useState(false);
  const [deadlineOpen, setDeadlineOpen] = useState(false);
  const chatRole = task.star ? task.star.role : boardRole;
  const chatReadOnly = !!task.owner && !!myEmail && !canRunTask(task, myEmail, chatRole);
  const canEdit = !readOnly && !task.human_task;
  const canRename = task.star ? task.star.role !== "viewer" : !readOnly;
  const showAssignee = !!task.task_board_id && assignable.length > 0;
  const canAssign = role === "owner" || role === "editor";
  const priority = priorityDisplay(task.task_priority);
  const deadline = deadlineDisplay(task.task_deadline);
  const boardName = board.board?.name ?? "My tasks";

  const status = task.column === "needs_input"
    ? { text: task.waiting_reason ? `Needs you · ${WAITING_LABELS[task.waiting_reason]}` : "Needs you",
        className: task.waiting_reason === "failed" || task.waiting_reason === "stopped"
          ? "bg-destructive/10 text-destructive" : "bg-amber-500/15 text-amber-800 dark:text-amber-300" }
    : task.column === "review" ? { text: task.waiting_reason === "stopped_by_you" ? "Stopped by you" : "Ready to review",
        className: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300" }
    : task.column === "working" ? { text: "Running", className: "bg-blue-500/10 text-blue-700 dark:text-blue-300" }
    : task.column === "done" ? { text: "Done", className: "bg-muted text-muted-foreground" }
    : { text: isDraft(task) ? "Not started" : "Paused", className: "bg-muted text-muted-foreground" };

  const assign = async (email: string | null) => {
    onError(null);
    try {
      const { task: updated } = await updateTask(task.conversation_id, { task_assignee: email });
      if (updated) onTaskChange(updated);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not assign task");
    }
  };
  const toggleStar = async (target: Task) => {
    const starred = !target.starred;
    onTaskChange({ ...target, starred });
    try {
      await (starred ? starTask(target.conversation_id) : unstarTask(target.conversation_id));
    } catch (e) {
      onTaskChange({ ...target, starred: !starred });
      onError(e instanceof Error ? e.message : "Could not update star");
    }
  };
  // A finished run's title arrives a few seconds later (server enrichment).
  const handleStreamComplete = (conversationId: string) => {
    window.setTimeout(async () => {
      try {
        const data = await fetchConversation(conversationId);
        if (data.conversation?.title && data.conversation.title !== task.title) {
          onTaskChange({ ...task, title: data.conversation.title });
        }
      } catch { /* the board poll picks it up */ }
      onRefresh();
    }, 4000);
  };

  const chip = "h-8 rounded-lg border border-border bg-card px-2.5 text-[13px] text-foreground/90 hover:bg-muted/60 disabled:opacity-60";

  return (
    <>
      <header className="flex shrink-0 flex-col gap-3 border-b border-border px-6 pb-3.5 pt-4">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            {canRename && !task.human_task ? (
              <EditableTaskTitle key={`${task.conversation_id}:${task.title || task.prompt}`} task={task}
                onTaskChange={onTaskChange} Title="h2" className="min-w-0 truncate text-[19px] font-semibold leading-7" />
            ) : (
              <h2 className="truncate text-[19px] font-semibold leading-7">{task.title || task.prompt || "New task"}</h2>
            )}
          </div>
          <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-[13px] font-medium", status.className)}>{status.text}</span>
          <div className="flex shrink-0 items-center gap-1.5">
            {position && <span className="mr-1 text-[13px] tabular-nums text-muted-foreground">{position.index} of {position.total}</span>}
            <Button variant="outline" size="icon" className="h-8 w-8" aria-label="Previous task (K)" title="Previous (K)" onClick={onPrev}>
              <RiArrowUpSLine className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="icon" className="h-8 w-8" aria-label="Next task (J)" title="Next (J)" onClick={onNext}>
              <RiArrowDownSLine className="h-4 w-4" />
            </Button>
            {canMarkDone && (
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={onMarkDone} title="Mark done (E)">
                <RiCheckLine className="h-4 w-4" /> Mark done
              </Button>
            )}
            {!readOnly && task.column === "done" && !task.human_task && (
              <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={onReopen}>
                <RiArrowGoBackLine className="h-4 w-4" /> Reopen
              </Button>
            )}
            {(canEdit || task.star) && (
              <TaskCardMenu task={task} lanes={board.lanes} tags={board.tags} models={models}
                open={menuOpen} onOpenChange={setMenuOpen}
                onMoveToLane={actions.moveToLane} onReopen={actions.reopen}
                onMarkDone={task.star ? actions.markDone : undefined}
                onRemoveFromBoard={actions.removeFromBoard} onDeleteDraft={actions.deleteDraft} onFork={actions.forkTask}
                onSetModel={actions.setTaskModel} onSetPriority={actions.setTaskPriority}
                onSetDeadline={actions.setTaskDeadline} onSetTags={actions.setTaskTags} onCreateTag={actions.createAndAssignTag}>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="More task options">
                  <RiMoreLine className="h-4 w-4" />
                </Button>
              </TaskCardMenu>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canEdit ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" className={chip}><span className="text-muted-foreground">Priority</span> {priority ? `${priority.symbol} ${priority.label}` : "None"}</button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-40">
                <PriorityMenuItems task={task} onSetPriority={actions.setTaskPriority} />
              </DropdownMenuContent>
            </DropdownMenu>
          ) : priority && <span className={chip + " inline-flex items-center"}>{priority.symbol} {priority.label}</span>}
          {canEdit ? (
            <DropdownMenu open={deadlineOpen} onOpenChange={setDeadlineOpen}>
              <DropdownMenuTrigger asChild>
                <button type="button" className={cn(chip, deadline?.urgent && "border-destructive/40 text-destructive")}>
                  <span className={deadline?.urgent ? "" : "text-muted-foreground"}>Due</span> {deadline ? `${deadline.dateLabel} · ${deadline.remainingLabel}` : "None"}
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DeadlineMenuItems task={task} onSetDeadline={actions.setTaskDeadline} onPicked={() => setDeadlineOpen(false)} />
              </DropdownMenuContent>
            </DropdownMenu>
          ) : deadline && <span className={chip + " inline-flex items-center"}>Due {deadline.dateLabel}</span>}
          {showAssignee && (
            <AssigneeSelect key={task.conversation_id} value={task.assignee} people={assignable} me={myEmail}
              disabled={!canAssign || readOnly} onChange={(email) => void assign(email)} className="h-8 w-48 text-[13px]" />
          )}
          {canEdit && onMoveToBoard && !task.star && !task.task_card_id ? (
            <button type="button" className={chip} onClick={() => onMoveToBoard(task)}>
              <span className="text-muted-foreground">Board</span> {boardName}
            </button>
          ) : null}
          {!task.human_task && !!task.task_board_id && <StarButton task={task} onToggle={(t) => void toggleStar(t)} className="h-8 w-8" iconClassName="h-4 w-4" />}
          {!readOnly && (
            <a href={`${basePath}/chat?continue=${task.conversation_id}`} target="_blank" rel="noopener noreferrer"
              className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground">
              <RiExternalLinkLine className="h-4 w-4" /> Open in full tab
            </a>
          )}
        </div>
      </header>
      <TaskConversation
        conversationId={task.conversation_id}
        onStreamComplete={handleStreamComplete}
        readOnly={chatReadOnly || readOnly && !task.star}
        reloadToken={reloadToken}
        autoFocusComposer={false}
      />
      <p className="shrink-0 border-t border-border px-6 py-1.5 text-center text-xs text-muted-foreground">
        J / K next and previous · E mark done · R reply
      </p>
    </>
  );
}
