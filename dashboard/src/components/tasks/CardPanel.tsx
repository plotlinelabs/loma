"use client";

import { useState, type ReactNode } from "react";
import {
  DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  RiArrowRightSLine, RiCheckLine, RiCloseLine, RiDeleteBinLine, RiDraggable, RiExternalLinkLine, RiInboxArchiveLine,
  RiMoreLine, RiSparkling2Line,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  createTask,
  deleteConversation,
  deleteTaskCard,
  starTask,
  unstarTask,
  updateTask,
  updateTaskCard,
  type BoardField,
  type CardFieldValue,
  type Task,
  type TaskCardItem,
  type TasksBoardResponse,
} from "@/lib/api";
import { DEPLOY_QUEUED_LABEL, isDraft, isParked } from "./taskDisplay";
import { TaskSummary, WaitingChip } from "./TaskCardParts";
import { formatFieldValue, isEmptyValue } from "./cardDisplay";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { AssigneeBadge } from "./boardExtras";
import { StarButton } from "./TaskStar";
import { AddExistingTaskDialog } from "./MoveTaskDialogs";
import { rankBetween, reopenedColumn } from "./transitions";

const shortName = (email: string) => email.split("@")[0];

interface CardPanelProps {
  board: TasksBoardResponse;
  /** Card being viewed; kept on close for the exit animation. */
  card: TaskCardItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  /** Open one of the card's tasks in the chat drawer. */
  onOpenTask: (task: Task) => void;
  readOnly?: boolean;
  myEmail: string | null;
}

const NONE = "__none__";

/** One editor per field type. Saves on blur / change; `null` clears. */
function FieldInput({ field, value, disabled, onSave }: {
  field: BoardField;
  value: CardFieldValue | undefined;
  disabled: boolean;
  onSave: (value: CardFieldValue) => void;
}) {
  const id = `card-field-${field.id}`;
  if (field.type === "checkbox") {
    return <Switch id={id} checked={value === true} disabled={disabled} onCheckedChange={(checked) => onSave(checked)} />;
  }
  if (field.type === "select") {
    return (
      <Select
        value={typeof value === "string" && value ? value : NONE}
        onValueChange={(next) => onSave(next === NONE ? null : next)}
        disabled={disabled}
      >
        <SelectTrigger id={id} size="sm" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>None</SelectItem>
          {field.options.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}
        </SelectContent>
      </Select>
    );
  }
  if (field.type === "multi_select") {
    const selected = Array.isArray(value) ? value : [];
    return (
      <div id={id} className="flex flex-wrap gap-1">
        {field.options.map((option) => {
          const on = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              disabled={disabled}
              aria-pressed={on}
              onClick={() => onSave(on ? selected.filter((o) => o !== option) : [...selected, option])}
              className={cn(
                "rounded-md border px-2 py-0.5 text-xs",
                on ? "border-foreground/30 bg-foreground/10 text-foreground" : "border-border text-muted-foreground",
              )}
            >
              {option}
            </button>
          );
        })}
      </div>
    );
  }
  const stored = value === null || value === undefined ? "" : String(value);
  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed === stored) return;
    if (!trimmed) return onSave(null);
    onSave(field.type === "number" ? Number(trimmed) : trimmed);
  };
  const input = (
    <Input
      // Remount when the saved value changes so polling never clobbers typing.
      key={stored}
      id={id}
      type={field.type === "number" ? "number" : field.type === "date" ? "date" : field.type === "link" ? "url" : "text"}
      step={field.type === "number" ? "any" : undefined}
      defaultValue={stored}
      disabled={disabled}
      placeholder={field.type === "link" ? "https://" : field.type === "person" ? "Name or email" : "Empty"}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      className="h-8"
    />
  );
  if (field.type !== "link" || !stored) return input;
  return (
    <div className="flex items-center gap-1">
      {input}
      <Button asChild variant="ghost" size="icon" className="h-8 w-8 shrink-0 text-muted-foreground">
        <a href={stored} target="_blank" rel="noopener noreferrer" aria-label={`Open ${field.name}`}>
          <RiExternalLinkLine className="h-4 w-4" />
        </a>
      </Button>
    </div>
  );
}

function taskLabel(task: Task): { text: string; className: string } | null {
  if (task.status === "queued") return { text: DEPLOY_QUEUED_LABEL, className: "text-muted-foreground" };
  if (task.column === "working") return { text: "Loma running", className: "text-blue-600" };
  if (task.column === "needs_input") return { text: "Needs you", className: "text-amber-600" };
  if (task.column === "review") return { text: "Ready to review", className: "text-emerald-600" };
  if (isParked(task)) return { text: "Paused", className: "text-muted-foreground" };
  return null;
}

/**
 * Card checklist order. Tasks get `task_rank = -createdEpoch` on creation, so
 * sorting by descending rank keeps the oldest-first default while letting a
 * drag slot an explicit rank between two neighbours.
 */
const cardOrderKey = (task: Task) => -(task.task_rank ?? 0);

function SortableTaskRow({ id, disabled, children }: {
  id: string;
  disabled: boolean;
  children: ReactNode;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id, disabled });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "group relative flex items-center gap-2 rounded-lg px-1.5 py-1.5 hover:bg-muted/60",
        isDragging && "z-10 bg-muted/80 opacity-80",
      )}
    >
      {!disabled && (
        <button
          type="button"
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          aria-label="Drag to reorder"
          className="-ml-1 shrink-0 cursor-grab touch-none text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100 active:cursor-grabbing"
        >
          <RiDraggable className="h-3.5 w-3.5" />
        </button>
      )}
      {children}
    </li>
  );
}

function CardPanelBody({ board, card, onOpenChange, onBoardChange, onRefresh, onOpenTask, readOnly, myEmail }:
  Omit<CardPanelProps, "open" | "card"> & { card: TaskCardItem }) {
  const [notes, setNotes] = useState(card.notes);
  const [savedNotes, setSavedNotes] = useState(card.notes);
  const [newTask, setNewTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addExistingOpen, setAddExistingOpen] = useState(false);

  const fields = board.fields ?? [];
  const lomaNotes = card.loma_notes ?? [];
  const cards = board.cards ?? [];
  const tasks = board.tasks
    .filter((task) => task.task_card_id === card.card_id)
    // Open items first, ticked-off ones at the bottom; manual order within each.
    .sort((a, b) => Number(a.column === "done") - Number(b.column === "done")
      || cardOrderKey(a) - cardOrderKey(b)
      || (a.task_created_at ?? "").localeCompare(b.task_created_at ?? ""));
  const done = tasks.filter((task) => task.column === "done").length;
  // Needs you first, then everything still open, then what's done (folded).
  const groupOf = (task: Task) => task.column === "needs_input" ? "needs" : task.column === "done" ? "done" : "open";
  const needsYou = tasks.filter((t) => groupOf(t) === "needs");
  const openTasks = tasks.filter((t) => groupOf(t) === "open");
  const doneTasks = tasks.filter((t) => groupOf(t) === "done");
  const [showDone, setShowDone] = useState(false);
  // The board's "show on card" fields, as tiles at the top.
  const keyFields = fields.filter((f) => f.show_on_card && !isEmptyValue(card.fields[f.id])).slice(0, 3);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const run = async (optimistic: TasksBoardResponse | null, request: () => Promise<unknown>, fallback: string) => {
    setError(null);
    if (optimistic) onBoardChange(optimistic);
    try {
      await request();
    } catch (e) {
      setError(e instanceof Error ? e.message : fallback);
    }
    onRefresh();
  };

  const patchCard = (changes: Partial<TaskCardItem>) =>
    ({ ...board, cards: cards.map((c) => (c.card_id === card.card_id ? { ...c, ...changes } : c)) });
  const patchTask = (id: string, changes: Partial<Task>) =>
    ({ ...board, tasks: board.tasks.map((t) => (t.conversation_id === id ? { ...t, ...changes } : t)) });

  const saveField = (field: BoardField, value: CardFieldValue) => {
    if (typeof value === "number" && Number.isNaN(value)) {
      setError(`“${field.name}” must be a number`);
      return;
    }
    void run(
      patchCard({ fields: { ...card.fields, [field.id]: value } }),
      () => updateTaskCard(card.card_id, { fields: { [field.id]: value } }),
      "Could not save field",
    );
  };

  const toggleDone = (task: Task) => {
    if (task.column === "done") {
      // Un-tick: back to where it was (a task that never ran goes back to a draft).
      const next = task.status ? "active" as const : "todo" as const;
      void run(patchTask(task.conversation_id, { task_status: next, column: next === "todo" ? (task.task_lane ?? "") : reopenedColumn(task) }),
        () => updateTask(task.conversation_id, { task_status: next }), "Could not update task");
    } else {
      void run(patchTask(task.conversation_id, { task_status: "done", column: "done" }),
        () => updateTask(task.conversation_id, { task_status: "done" }), "Could not update task");
    }
  };

  const reorderTask = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const task = tasks.find((t) => t.conversation_id === active.id);
    const target = tasks.find((t) => t.conversation_id === over.id);
    // Rows only reorder within their own group (Needs you / Open / Done).
    if (!task || !target || groupOf(task) !== groupOf(target)) return;
    const group = tasks.filter((t) => groupOf(t) === groupOf(task));
    const to = group.indexOf(target);
    // Dropping on a row moves the dragged task into that row's slot.
    const moved = arrayMove(group, group.indexOf(task), to);
    const before = to > 0 ? cardOrderKey(moved[to - 1]) : null;
    const after = to < moved.length - 1 ? cardOrderKey(moved[to + 1]) : null;
    const rank = -rankBetween(before, after);
    void run(patchTask(task.conversation_id, { task_rank: rank }),
      () => updateTask(task.conversation_id, { task_rank: rank }), "Could not reorder task");
  };

  // Private star: the task also shows on your own board. Viewers can star too.
  const toggleStar = (task: Task) =>
    void run(patchTask(task.conversation_id, { starred: !task.starred }),
      () => (task.starred ? unstarTask(task.conversation_id) : starTask(task.conversation_id)),
      "Could not update star");

  const removeTask = (task: Task) =>
    void run(
      { ...board, tasks: board.tasks.filter((t) => t.conversation_id !== task.conversation_id) },
      // Unstarted items are deleted; started chats just leave the board.
      () => (isDraft(task) ? deleteConversation(task.conversation_id) : updateTask(task.conversation_id, { task_status: null })),
      "Could not remove task",
    );

  const addTask = async (askLoma: boolean) => {
    const text = newTask.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { task } = await createTask(
        askLoma
          ? { prompt: text, start: true, board: card.board_id, card: card.card_id }
          : { prompt: "", title: text, board: card.board_id, card: card.card_id },
      );
      onBoardChange({ ...board, tasks: [...board.tasks, task] });
      setNewTask("");
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add task");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    const warning = tasks.length
      ? ` Its ${tasks.length} task${tasks.length === 1 ? "" : "s"} go back to the boards of the people who created them.`
      : "";
    if (!window.confirm(`Delete "${card.title}"?${warning}`)) return;
    onOpenChange(false);
    await run(
      { ...board, cards: cards.filter((c) => c.card_id !== card.card_id) },
      () => deleteTaskCard(card.card_id),
      "Could not delete card",
    );
  };

  return (
    <>
      <SheetHeader className="flex-shrink-0 gap-2 border-b border-border pr-12">
        <SheetTitle className="sr-only">{card.title}</SheetTitle>
        <SheetDescription className="sr-only">Card details, tasks and notes</SheetDescription>
        <Input
          key={card.title}
          defaultValue={card.title}
          disabled={readOnly}
          maxLength={120}
          aria-label="Card title"
          onBlur={(e) => {
            const title = e.target.value.trim();
            if (!title) { e.target.value = card.title; return; }
            if (title !== card.title) void run(patchCard({ title }), () => updateTaskCard(card.card_id, { title }), "Could not rename card");
          }}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          className="h-10 border-transparent px-1 text-[19px] font-semibold shadow-none hover:border-border md:text-[19px]"
        />
        <div className="flex items-center gap-2">
          <Select
            value={card.lane}
            disabled={readOnly}
            onValueChange={(lane) => void run(patchCard({ lane }), () => updateTaskCard(card.card_id, { lane }), "Could not move card")}
          >
            <SelectTrigger size="sm" className="h-8 w-44 text-[13px]" aria-label="Column"><SelectValue /></SelectTrigger>
            <SelectContent>
              {board.lanes.map((lane) => <SelectItem key={lane.id} value={lane.id}>{lane.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {!readOnly && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="ml-auto h-8 w-8 text-muted-foreground" aria-label="Card options">
                  <RiMoreLine className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem variant="destructive" onClick={() => void remove()}>
                  <RiDeleteBinLine className="h-4 w-4" /> Delete card
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        {keyFields.length > 0 && (
          <dl className="grid grid-cols-3 gap-2">
            {keyFields.map((field) => (
              <div key={field.id} className="min-w-0 rounded-xl bg-muted/60 px-3 py-2">
                <dt className="truncate text-xs text-muted-foreground">{field.name}</dt>
                <dd className="mt-0.5 truncate text-[15px] font-semibold">{formatFieldValue(field, card.fields[field.id])}</dd>
              </div>
            ))}
          </dl>
        )}
      </SheetHeader>

      <Tabs defaultValue="tasks" className="min-h-0 flex-1 gap-0">
        <TabsList className="mx-4 mt-3">
          <TabsTrigger value="tasks">Tasks{tasks.length ? ` ${done}/${tasks.length}` : ""}</TabsTrigger>
          <TabsTrigger value="fields">All fields</TabsTrigger>
          <TabsTrigger value="notes">Notes for Loma</TabsTrigger>
        </TabsList>
        {error && <p className="px-4 pt-2 text-xs text-destructive">{error}</p>}

        <TabsContent value="tasks" className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-[15px]">
          {tasks.length === 0 && (
            <p className="text-[14px] text-muted-foreground">
              No tasks yet. Add a to-do to tick off yourself, or ask Loma to do it.
            </p>
          )}
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={reorderTask}>
          {([
            ["needs", "Needs you", needsYou],
            ["open", "Open", openTasks],
            ["done", "Done", doneTasks],
          ] as const).map(([group, title, groupTasks]) => groupTasks.length > 0 && (
          <section key={group} aria-label={title}>
          {group === "done" ? (
            <button type="button" onClick={() => setShowDone((v) => !v)} aria-expanded={showDone}
              className="flex h-9 w-full items-center gap-1.5 border-t border-border pt-1 text-[14px] font-semibold text-foreground/80">
              <RiArrowRightSLine className={cn("h-4 w-4 transition-transform", showDone && "rotate-90")} />
              Done <span className="font-normal text-muted-foreground">{groupTasks.length}</span>
            </button>
          ) : (
            <h3 className={cn("mb-1 px-1.5 text-[13px] font-semibold", group === "needs" ? "text-amber-800 dark:text-amber-300" : "text-foreground/80")}>
              {title} <span className="font-normal text-muted-foreground">{groupTasks.length}</span>
            </h3>
          )}
          {(group !== "done" || showDone) && (
          <SortableContext items={groupTasks.map((t) => t.conversation_id)} strategy={verticalListSortingStrategy}>
          <ul className="space-y-1">
            {groupTasks.map((task) => {
              const isDone = task.column === "done";
              const label = taskLabel(task);
              const mine = !!myEmail && task.owner === myEmail;
              return (
                <SortableTaskRow key={task.conversation_id} id={task.conversation_id} disabled={!!readOnly}>
                  <button
                    type="button"
                    disabled={readOnly}
                    onClick={() => toggleDone(task)}
                    aria-label={isDone ? "Mark as not done" : "Mark as done"}
                    aria-pressed={isDone}
                    className={cn(
                      "flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded border",
                      isDone ? "border-emerald-500 bg-emerald-500 text-white" : "border-input bg-background",
                    )}
                  >
                    {isDone && <RiCheckLine className="h-3.5 w-3.5" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => onOpenTask(task)}
                    className="flex min-w-0 flex-1 flex-col gap-0.5 py-0.5 text-left"
                  >
                    <span className={cn("truncate text-[15px] leading-[21px]", isDone && "text-muted-foreground line-through")}>
                      {task.title || task.prompt || "New task"}
                    </span>
                    {!isDone && <TaskSummary task={task} className="line-clamp-1" />}
                  </button>
                  {task.column === "needs_input" ? <WaitingChip task={task} /> : label && !isDone && task.column !== "working" && (
                    <span className={cn("shrink-0 text-[13px]", label.className)}>{label.text}</span>
                  )}
                  {!mine && task.owner && !task.assignee && (
                    <span className="shrink-0 text-[13px] text-muted-foreground">{shortName(task.owner)}</span>
                  )}
                  {task.assignee && <AssigneeBadge email={task.assignee} me={myEmail} />}
                  <StarButton task={task} onToggle={toggleStar}
                    className={cn("h-5 w-5", !task.starred && "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100")} />
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() => removeTask(task)}
                      aria-label="Remove task"
                      className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
                    >
                      <RiCloseLine className="h-4 w-4" />
                    </button>
                  )}
                </SortableTaskRow>
              );
            })}
          </ul>
          </SortableContext>
          )}
          </section>
          ))}
          </DndContext>
          {!readOnly && (
            <div className="space-y-2 rounded-2xl border border-border p-3">
              <Input
                value={newTask}
                onChange={(e) => setNewTask(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  // Enter keeps it as a to-do; Cmd/Ctrl+Enter hands it to Loma.
                  void addTask(e.metaKey || e.ctrlKey);
                }}
                placeholder={`Add a task for ${card.title}`}
                aria-label="New task"
                className="h-9 border-0 px-1 text-[16px] shadow-none focus-visible:ring-0 md:text-[16px]"
              />
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" size="sm" className="mr-auto text-muted-foreground"
                  onClick={() => setAddExistingOpen(true)}>
                  <RiInboxArchiveLine className="h-4 w-4" /> Add existing chat
                </Button>
                <Button variant="outline" size="sm" disabled={!newTask.trim() || busy} onClick={() => void addTask(false)}
                  title="Enter">
                  Add as to-do
                </Button>
                <Button size="sm" disabled={!newTask.trim() || busy} onClick={() => void addTask(true)} title="⌘/Ctrl + Enter">
                  <RiSparkling2Line className="h-4 w-4" /> Ask Loma to do it
                </Button>
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="fields" className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {fields.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              This board has no fields yet. Add some in Board settings, for example Value, Owner or Due date.
            </p>
          ) : fields.map((field) => (
            <div key={field.id} className="grid grid-cols-[8rem_1fr] items-center gap-3">
              <Label htmlFor={`card-field-${field.id}`} className="truncate text-[13px] font-normal text-muted-foreground">
                {field.name}
              </Label>
              <div className="min-w-0 space-y-0.5">
                <FieldInput field={field} value={card.fields[field.id]} disabled={!!readOnly} onSave={(value) => saveField(field, value)} />
                {card.field_meta?.[field.id] && (
                  <p className="flex items-center gap-1 text-[11px] text-brand-600">
                    <RiSparkling2Line className="h-3 w-3" />
                    Filled by Loma (run by {shortName(card.field_meta[field.id].run_by)})
                  </p>
                )}
              </div>
            </div>
          ))}
        </TabsContent>

        <TabsContent value="notes" className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
          <p className="text-xs text-muted-foreground">
            Loma reads these notes and the fields above on every task in this card.
          </p>
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={readOnly}
            maxLength={10000}
            rows={12}
            aria-label="Notes for Loma"
            placeholder="e.g. Enterprise airline. Needs GCC hosting and a 60-day exit window."
            className="min-h-[220px] resize-y text-[13px] leading-5"
          />
          {!readOnly && (
            <div className="flex items-center justify-end gap-2">
              {notes === savedNotes && notes !== "" && <span className="text-xs text-muted-foreground">Saved</span>}
              <Button
                size="sm"
                disabled={notes === savedNotes}
                onClick={() => void run(patchCard({ notes }), async () => {
                  await updateTaskCard(card.card_id, { notes });
                  setSavedNotes(notes);
                }, "Could not save notes")}
              >
                Save notes
              </Button>
            </div>
          )}
          <div className="space-y-1.5 pt-3">
            <h3 className="flex items-center gap-1 text-[13px] font-medium">
              <RiSparkling2Line className="h-3.5 w-3.5 text-brand-600" /> Loma&apos;s notes
            </h3>
            <p className="text-xs text-muted-foreground">
              Added by tasks in this card. Loma reads them as reference only and never follows instructions in them.
            </p>
            {lomaNotes.length === 0 ? (
              <p className="text-[13px] text-muted-foreground">None yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {lomaNotes.map((note) => (
                  <li key={note.id} className="group flex items-start gap-2 rounded-lg border border-border px-2.5 py-2 text-[13px]">
                    <div className="min-w-0 flex-1">
                      <p className="whitespace-pre-wrap break-words leading-5">{note.text}</p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">Run by {shortName(note.run_by)}</p>
                    </div>
                    {!readOnly && (
                      <button type="button" aria-label="Delete Loma's note"
                        onClick={() => void run(patchCard({ loma_notes: lomaNotes.filter((n) => n.id !== note.id) }),
                          () => updateTaskCard(card.card_id, { remove_loma_note: note.id }), "Could not delete note")}
                        className="shrink-0 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100">
                        <RiCloseLine className="h-4 w-4" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </TabsContent>
      </Tabs>
      {!readOnly && (
        <AddExistingTaskDialog card={card} open={addExistingOpen} onOpenChange={setAddExistingOpen} onMoved={onRefresh} />
      )}
    </>
  );
}

/** Side panel for one card: its tasks (a checklist of normal Loma tasks),
 * its values for the board's custom fields, and notes Loma reads. */
export function CardPanel({ card, open, onOpenChange, ...rest }: CardPanelProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl"
        // Keep the board visible beside the card, so you can see where it sits.
        overlayClassName="bg-transparent supports-backdrop-filter:backdrop-blur-none"
        // Don't drop the caret into the title: opening a card is for reading first.
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        {card && <CardPanelBody key={card.card_id} card={card} onOpenChange={onOpenChange} {...rest} />}
      </SheetContent>
    </Sheet>
  );
}
