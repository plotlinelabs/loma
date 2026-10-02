"use client";

import { useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { RiAddLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { AssigneeBadge, useBoardExtras } from "./boardExtras";
import { Input } from "@/components/ui/input";
import {
  createTaskCard,
  updateTaskCard,
  type BoardField,
  type BoardLane,
  type TaskCardItem,
  type TasksBoardResponse,
} from "@/lib/api";
import { rankBetween } from "./transitions";
import { columnTotal, formatFieldValue, isEmptyValue, summaryField } from "./cardDisplay";
import { matchesFilters } from "./cardFilters";

interface CardBoardProps {
  board: TasksBoardResponse;
  /** Optimistically replace board state; server truth reconciles via polling. */
  onBoardChange: (board: TasksBoardResponse) => void;
  onRefresh: () => void;
  onOpenCard: (card: TaskCardItem) => void;
  onError: (message: string | null) => void;
  /** View-only member: no drag, no adds. */
  readOnly?: boolean;
}

function CardTile({ card, fields, readOnly, onOpen, assignees = [], me = null }: {
  card: TaskCardItem;
  fields: BoardField[];
  readOnly: boolean;
  onOpen: (card: TaskCardItem) => void;
  /** People assigned to the card's open tasks. */
  assignees?: string[];
  me?: string | null;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.card_id, disabled: readOnly });
  const shown = fields.filter((field) => field.show_on_card && !isEmptyValue(card.fields[field.id]));
  const percent = card.task_total ? Math.round((card.task_done / card.task_total) * 100) : 0;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      // View-only: skip the drag wiring (it marks the card aria-disabled),
      // so the card stays a plain clickable element.
      {...(readOnly ? {} : attributes)}
      {...(readOnly ? {} : listeners)}
      onClick={() => onOpen(card)}
      data-card-id={card.card_id}
      className={cn(
        "rounded-xl border border-border bg-card px-3.5 py-3 cursor-pointer",
        "hover:border-input transition-colors touch-none select-none",
        isDragging && "opacity-40",
      )}
    >
      <div className="line-clamp-2 break-words text-[14px] font-medium leading-5">{card.title}</div>
      {shown.length > 0 && (
        <dl className="mt-1.5 space-y-0.5 text-[12px] leading-4">
          {shown.map((field) => (
            <div key={field.id} className="flex gap-1.5">
              <dt className="shrink-0 text-muted-foreground">{field.name}</dt>
              <dd className="min-w-0 truncate">{formatFieldValue(field, card.fields[field.id])}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="mt-2 flex items-center gap-2 text-[11px] leading-4 text-muted-foreground">
        {card.task_total > 0 ? (
          <>
            <div className="h-1 w-12 shrink-0 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${percent}%` }} />
            </div>
            <span className="tabular-nums">{card.task_done}/{card.task_total} tasks</span>
          </>
        ) : (
          <span>No tasks yet</span>
        )}
        {card.task_running > 0 && (
          <span className="flex items-center gap-1 text-blue-600">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
            {card.task_running} running
          </span>
        )}
        {card.task_needs_input > 0 && (
          <span className="flex items-center gap-1 text-amber-600">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            {card.task_needs_input} need input
          </span>
        )}
        {assignees.length > 0 && (
          <span className="ml-auto flex -space-x-1">
            {assignees.slice(0, 3).map((email) => (
              <AssigneeBadge key={email} email={email} me={me} className="ring-2 ring-card" />
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

function CardColumn({ lane, cards, total, canAdd, onAdd, children }: {
  lane: BoardLane;
  cards: TaskCardItem[];
  total: string | null;
  canAdd: boolean;
  onAdd: (title: string) => Promise<void>;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: lane.id });
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");

  const submit = async () => {
    const trimmed = title.trim();
    if (!trimmed) {
      setAdding(false);
      return;
    }
    setTitle("");
    await onAdd(trimmed);
  };

  return (
    <div className="flex min-w-[220px] flex-1 basis-0 flex-col" data-lane={lane.name}>
      <div className="mb-2.5 flex items-baseline gap-2 px-2">
        <span className="text-[13px] font-semibold text-foreground">{lane.name}</span>
        <span className="text-[11px] tabular-nums text-muted-foreground/80">{cards.length}</span>
        {total && <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">{total}</span>}
      </div>
      <SortableContext items={cards.map((c) => c.card_id)} strategy={verticalListSortingStrategy}>
        <div
          ref={setNodeRef}
          className={cn(
            "flex min-h-24 flex-1 flex-col gap-2 rounded-xl p-1.5 bg-foreground/[0.025] transition-colors",
            isOver && "bg-foreground/[0.05]",
          )}
        >
          {children}
          {canAdd && (adding ? (
            <Input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => void submit().then(() => setAdding(false))}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
                if (e.key === "Escape") { setTitle(""); setAdding(false); }
              }}
              placeholder="Card title, then Enter"
              maxLength={120}
              className="h-8 bg-card text-[13px]"
              aria-label={`New card in ${lane.name}`}
            />
          ) : (
            <button
              onClick={() => setAdding(true)}
              className="flex items-center gap-1 rounded-md px-2 py-1.5 text-xs text-muted-foreground/70 hover:bg-muted hover:text-foreground"
            >
              <RiAddLine className="h-3.5 w-3.5" />
              Card
            </button>
          ))}
        </div>
      </SortableContext>
    </div>
  );
}

/** Kanban for a card board: columns hold cards (a deal, a candidate, a
 * project...), each showing the board's "show on card" fields and the
 * progress of the tasks inside it. */
export function CardBoard({ board, onBoardChange, onRefresh, onOpenCard, onError, readOnly = false }: CardBoardProps) {
  const [activeCard, setActiveCard] = useState<TaskCardItem | null>(null);
  const cards = board.cards ?? [];
  const fields = board.fields ?? [];
  const totalField = summaryField(fields);
  const boardId = board.board?.id ?? "";

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const { myEmail, assignedToMe, cardFilters, filterMatch } = useBoardExtras();
  // Assignees of each card's open tasks; "Assigned to me" keeps only cards
  // holding a task assigned to you.
  const assigneesByCard: Record<string, string[]> = {};
  for (const task of board.tasks) {
    if (!task.task_card_id || !task.assignee || task.column === "done") continue;
    const list = (assigneesByCard[task.task_card_id] ??= []);
    if (!list.includes(task.assignee)) list.push(task.assignee);
  }
  const cardsByLane: Record<string, TaskCardItem[]> = {};
  for (const lane of board.lanes) cardsByLane[lane.id] = [];
  for (const card of cards) {
    if (assignedToMe && !board.tasks.some((t) => t.task_card_id === card.card_id && t.assignee === myEmail)) continue;
    if (!matchesFilters(card, cardFilters, filterMatch, { fields, assigneesByCard })) continue;
    (cardsByLane[card.lane] ??= []).push(card);
  }
  for (const list of Object.values(cardsByLane)) list.sort((a, b) => a.rank - b.rank);
  const filtering = assignedToMe || cardFilters.length > 0;

  const addCard = async (laneId: string, title: string) => {
    onError(null);
    try {
      const { card } = await createTaskCard({ board: boardId, title, lane: laneId });
      onBoardChange({ ...board, cards: [...cards, card] });
      onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Could not add card");
    }
  };

  const handleDragStart = (event: DragStartEvent) => {
    setActiveCard(cards.find((c) => c.card_id === event.active.id) ?? null);
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const card = activeCard;
    setActiveCard(null);
    if (!card || !event.over) return;
    const overId = String(event.over.id);
    const target = cardsByLane[overId] !== undefined
      ? overId
      : cards.find((c) => c.card_id === overId)?.lane;
    if (!target || overId === card.card_id) return;

    // Position relative to the card we dropped on (or the column's end).
    const others = (cardsByLane[target] ?? []).filter((c) => c.card_id !== card.card_id);
    let index = others.findIndex((c) => c.card_id === overId);
    if (index === -1) index = others.length;
    const rank = rankBetween(
      index > 0 ? others[index - 1].rank : null,
      index < others.length ? others[index].rank : null,
    );

    const snapshot = board;
    onError(null);
    onBoardChange({
      ...board,
      cards: cards.map((c) => (c.card_id === card.card_id ? { ...c, lane: target, rank } : c)),
    });
    try {
      await updateTaskCard(card.card_id, { lane: target, rank });
      onRefresh();
    } catch (e) {
      onBoardChange(snapshot);
      onError(e instanceof Error ? e.message : "Could not move card");
    }
  };

  return (
    <DndContext
      sensors={readOnly ? [] : sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={(event) => void handleDragEnd(event)}
      onDragCancel={() => setActiveCard(null)}
    >
      <div className="flex flex-1 gap-4 overflow-x-auto pb-4">
        {board.lanes.map((lane) => {
          const laneCards = cardsByLane[lane.id] ?? [];
          return (
            <CardColumn
              key={lane.id}
              lane={lane}
              cards={laneCards}
              total={columnTotal(laneCards, totalField)}
              canAdd={!readOnly}
              onAdd={(title) => addCard(lane.id, title)}
            >
              {laneCards.map((card) => (
                <CardTile key={card.card_id} card={card} fields={fields} readOnly={readOnly} onOpen={onOpenCard}
                  assignees={assigneesByCard[card.card_id]} me={myEmail} />
              ))}
              {filtering && laneCards.length === 0 && (
                <p className="px-2 py-3 text-center text-[12px] text-muted-foreground/70">No matching cards</p>
              )}
            </CardColumn>
          );
        })}
      </div>
      <DragOverlay>
        {activeCard && (
          <div className="rounded-md border bg-card px-3 py-2 text-[13px] shadow-md">
            <div className="line-clamp-2 break-words">{activeCard.title}</div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
