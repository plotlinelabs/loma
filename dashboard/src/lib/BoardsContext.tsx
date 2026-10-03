"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import {
  PERSONAL_BOARD_ID,
  fetchTaskBoards,
  updateBoardPrefs,
  updateTaskBoard,
  type TaskBoardSummary,
} from "./api";

const POLL_INTERVAL_MS = 5000;
/** Last board opened on this device; the Tasks page reopens it. */
export const BOARD_STORAGE_KEY = "loma-task-board";

/** Something the nav or the quick switcher asks the Tasks page to do. */
export type BoardRequest =
  | { kind: "board"; boardId: string; taskId?: string }
  | { kind: "create" };

interface BoardsValue {
  /** Every board the user can open, in their own order, with needs-you counts. */
  boards: TaskBoardSummary[];
  reload: () => Promise<void>;
  /** Board open on the Tasks page (or last opened, elsewhere). */
  currentBoardId: string;
  setCurrentBoardId: (id: string) => void;
  /** Open a board (and optionally one of its tasks), going to /tasks if needed. */
  openBoard: (boardId: string, taskId?: string) => void;
  /** Open the "new board" dialog on the Tasks page. */
  createBoard: () => void;
  /** Pending request for the Tasks page; it calls consumeRequest once handled. */
  request: BoardRequest | null;
  consumeRequest: () => void;
  reorder: (ids: string[]) => void;
  setEmoji: (board: TaskBoardSummary, emoji: string) => Promise<void>;
  /** Quick switcher (Cmd/Ctrl+K). */
  commandOpen: boolean;
  setCommandOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

const BoardsContext = createContext<BoardsValue | null>(null);

export function useBoards() {
  const value = useContext(BoardsContext);
  if (!value) throw new Error("useBoards must be used inside BoardsProvider");
  return value;
}

export function BoardsProvider({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [boards, setBoards] = useState<TaskBoardSummary[]>([]);
  const [currentBoardId, setCurrentBoardId] = useState<string>(PERSONAL_BOARD_ID);
  const [request, setRequest] = useState<BoardRequest | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);
  // Bumped on every reorder so a poll that started earlier can't snap the
  // list back to the old order.
  const listVersion = useRef(0);

  useEffect(() => {
    setCurrentBoardId(window.localStorage.getItem(BOARD_STORAGE_KEY) || PERSONAL_BOARD_ID);
  }, []);

  const reload = useCallback(async () => {
    const version = listVersion.current;
    try {
      const list = (await fetchTaskBoards()).boards;
      if (version === listVersion.current) setBoards(list);
    } catch {
      // Keep the last list; transient poll failures are fine.
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    void reload();
    const interval = setInterval(() => { if (!document.hidden) void reload(); }, POLL_INTERVAL_MS);
    const onVisible = () => { if (!document.hidden) void reload(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [status, reload]);

  const goToTasks = useCallback((next: BoardRequest) => {
    setRequest(next);
    if (!pathname.startsWith("/tasks")) router.push("/tasks");
  }, [pathname, router]);

  const openBoard = useCallback((boardId: string, taskId?: string) => {
    window.localStorage.setItem(BOARD_STORAGE_KEY, boardId);
    setCurrentBoardId(boardId);
    goToTasks({ kind: "board", boardId, taskId });
  }, [goToTasks]);

  const createBoard = useCallback(() => goToTasks({ kind: "create" }), [goToTasks]);
  const consumeRequest = useCallback(() => setRequest(null), []);

  const reorder = useCallback((ids: string[]) => {
    const version = ++listVersion.current;
    setBoards((list) => {
      const byId = new Map(list.map((b) => [b.id, b]));
      return ids.map((id) => byId.get(id)).filter((b): b is TaskBoardSummary => !!b);
    });
    updateBoardPrefs({ order: ids })
      .catch(() => {})
      .finally(() => { if (version === listVersion.current) void reload(); });
  }, [reload]);

  const setEmoji = useCallback(async (board: TaskBoardSummary, emoji: string) => {
    ++listVersion.current;
    setBoards((list) => list.map((b) => (b.id === board.id ? { ...b, emoji } : b)));
    try {
      if (board.id === PERSONAL_BOARD_ID) await updateBoardPrefs({ personal_emoji: emoji });
      else await updateTaskBoard(board.id, { emoji });
    } finally {
      void reload();
    }
  }, [reload]);

  return (
    <BoardsContext.Provider value={{
      boards, reload, currentBoardId, setCurrentBoardId, openBoard, createBoard,
      request, consumeRequest, reorder, setEmoji, commandOpen, setCommandOpen,
    }}>
      {children}
    </BoardsContext.Provider>
  );
}
