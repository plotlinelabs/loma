"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  fetchTaskBoards, fetchTasksBoard, PERSONAL_BOARD_ID, updateTask,
  type Task, type TaskBoardSummary, type TaskCardItem,
} from "@/lib/api";
import { useBoardExtras } from "./boardExtras";

const RESET_NOTE = "Its column, tags and assignee are reset; the chat and status are kept.";
const PRIVACY_NOTE =
  "Everyone with access to that board will be able to read this task's whole chat. "
  + "Its personal column and tags are cleared; the chat and status are kept.";

/** Move one of your tasks to another board you can edit (into a card, on a card board). */
export function MoveToBoardDialog({ task, open, onOpenChange, onMoved }: {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMoved: () => void;
}) {
  const [boards, setBoards] = useState<TaskBoardSummary[] | null>(null);
  const [boardId, setBoardId] = useState("");
  const [cards, setCards] = useState<TaskCardItem[] | null>(null);
  const [cardId, setCardId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { myEmail } = useBoardExtras();
  const currentBoardId = task?.task_board_id || PERSONAL_BOARD_ID;
  // Only its creator can pull a task onto a personal board (that hides it from everyone else).
  const isCreator = !task?.owner || task.owner === myEmail;
  const target = boards?.find((b) => b.id === boardId);
  const needsCard = !!target?.card_mode;

  useEffect(() => {
    if (!open) return;
    setBoardId(""); setCards(null); setCardId(""); setError(null); setBoards(null);
    fetchTaskBoards()
      .then(({ boards: all }) => setBoards(all.filter((b) =>
        b.role !== "viewer" && b.id !== currentBoardId && (isCreator || b.id !== PERSONAL_BOARD_ID))))
      .catch(() => setError("Could not load boards"));
  }, [open, currentBoardId, isCreator]);

  useEffect(() => {
    setCards(null); setCardId("");
    if (!boardId || !needsCard) return;
    fetchTasksBoard("", boardId)
      .then((data) => setCards(data.cards ?? []))
      .catch(() => setError("Could not load cards"));
  }, [boardId, needsCard]);

  const move = async () => {
    if (!task || !boardId || (needsCard && !cardId)) return;
    setBusy(true);
    setError(null);
    try {
      await updateTask(task.conversation_id, needsCard
        ? { task_board_id: boardId, task_card_id: cardId }
        : { task_board_id: boardId });
      onOpenChange(false);
      onMoved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not move task");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move to board</DialogTitle>
          <DialogDescription>
            Move “{task?.title || task?.prompt || "this task"}” to another board.
            {needsCard && " On a card board it goes into a card, and from its next run it also reads the card's fields and notes."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="move-board">Board</Label>
            <Select value={boardId} onValueChange={setBoardId} disabled={!boards?.length}>
              <SelectTrigger id="move-board" className="w-full">
                <SelectValue placeholder={boards === null ? "Loading..." : boards.length ? "Pick a board" : "No other boards you can edit"} />
              </SelectTrigger>
              <SelectContent>
                {(boards ?? []).map((b) => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          {needsCard && (
            <div className="space-y-1.5">
              <Label htmlFor="move-card">Card</Label>
              <Select value={cardId} onValueChange={setCardId} disabled={!cards?.length}>
                <SelectTrigger id="move-card" className="w-full">
                  <SelectValue placeholder={cards === null ? "Loading..." : cards.length ? "Pick a card" : "This board has no cards yet"} />
                </SelectTrigger>
                <SelectContent>
                  {(cards ?? []).map((c) => <SelectItem key={c.card_id} value={c.card_id}>{c.title}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
          {target && (
            <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              {target.shared && "Everyone with access to that board will be able to read this task's whole chat. "}
              {RESET_NOTE}
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button onClick={() => void move()} disabled={busy || !boardId || (needsCard && !cardId)}>Move task</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Pick one of your personal-board tasks and move it into `card`. */
export function AddExistingTaskDialog({ card, open, onOpenChange, onMoved }: {
  card: TaskCardItem;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMoved: () => void;
}) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setTasks(null); setQuery(""); setSelected(null); setError(null);
    fetchTasksBoard("", PERSONAL_BOARD_ID)
      .then((data) => setTasks(data.tasks))
      .catch(() => setError("Could not load your tasks"));
  }, [open]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (tasks ?? [])
      .filter((t) => !needle || `${t.title ?? ""} ${t.prompt}`.toLowerCase().includes(needle))
      .sort((a, b) => Number(a.column === "done") - Number(b.column === "done"));
  }, [tasks, query]);

  const move = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await updateTask(selected, { task_board_id: card.board_id, task_card_id: card.card_id });
      onOpenChange(false);
      onMoved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not move task");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add an existing task</DialogTitle>
          <DialogDescription>Move one of your own tasks from “My tasks” into “{card.title}”.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your tasks..."
            aria-label="Search your tasks" className="h-8" />
          <ul className="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border" role="listbox"
            aria-label="Your tasks">
            {tasks === null && !error && <li className="px-3 py-2 text-[13px] text-muted-foreground">Loading...</li>}
            {tasks !== null && shown.length === 0 && (
              <li className="px-3 py-2 text-[13px] text-muted-foreground">No tasks on your personal board.</li>
            )}
            {shown.map((t) => (
              <li key={t.conversation_id}>
                <button type="button" role="option" aria-selected={selected === t.conversation_id}
                  onClick={() => setSelected(t.conversation_id)}
                  className={cn("flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted/60",
                    selected === t.conversation_id && "bg-muted")}>
                  <span className={cn("min-w-0 flex-1 truncate", t.column === "done" && "text-muted-foreground line-through")}>
                    {t.title || t.prompt || "New task"}
                  </span>
                  {t.total_turns > 0 && <span className="shrink-0 text-[11px] text-muted-foreground">{t.total_turns} turns</span>}
                </button>
              </li>
            ))}
          </ul>
          <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">{PRIVACY_NOTE}</p>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button onClick={() => void move()} disabled={busy || !selected}>Move into card</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
