"use client";
import type { ToolConfig } from "@/lib/api";

import PetCompanion from "@/components/PetCompanion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { RiAddLine, RiChatHistoryLine, RiCloseLine, RiFilter3Line, RiNotification3Line, RiNotificationOffLine, RiSearchLine, RiSettings3Line, RiUserAddLine, RiUserLine } from "@remixicon/react";
import {
  getPushState,
  isPushConfigured,
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
  type PushState,
} from "@/lib/push";
import {
  BoardNotFoundError,
  PERSONAL_BOARD_ID,
  basePath,
  canRunTask,
  createTask,
  createTaskCard,
  saveBoardSettings,
  fetchTaskBoards,
  fetchTasksBoard,
  updateTask,
  type Task,
  type TaskBoardSummary,
  type TaskCardItem,
  type TasksBoardResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TaskBoard } from "@/components/tasks/TaskBoard";
import { MobileTaskBoard } from "@/components/tasks/MobileTaskBoard";
import { QuickAddTask } from "@/components/tasks/QuickAddTask";
import { TaskDialog } from "@/components/tasks/TaskDialog";
import { AddChatDialog } from "@/components/tasks/AddChatDialog";
import { TaskChatDrawer } from "@/components/tasks/TaskChatDrawer";
import { BoardSettingsDialog } from "@/components/tasks/BoardSettingsDialog";
import { InstallHint } from "@/components/tasks/InstallHint";
import { AgentAttention } from "@/components/tasks/AgentAttention";
import { BoardSwitcher } from "@/components/tasks/BoardSwitcher";
import { ManageBoardDialog } from "@/components/tasks/ManageBoardDialog";
import { CardBoard } from "@/components/tasks/CardBoard";
import { CardTasksView } from "@/components/tasks/CardTasksView";
import { CardPanel } from "@/components/tasks/CardPanel";
import { MoveToBoardDialog } from "@/components/tasks/MoveTaskDialogs";
import { BoardExtrasContext, assignablePeople, type BoardExtras } from "@/components/tasks/boardExtras";
import { useIsMobile } from "@/hooks/useIsMobile";
import { MobileTopBarActions, MobileTopBarTitle } from "@/components/mobile/MobileChrome";
import { MobileBoardActions } from "@/components/mobile/MobileBoardActions";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 5000;
// Last board opened on this device, so the page reopens where you left off.
const BOARD_STORAGE_KEY = "loma-task-board";
// Card boards: last view (cards or tasks) used on each board.
const CARD_VIEW_STORAGE_PREFIX = "loma-card-board-view:";
type CardBoardView = "cards" | "tasks";
const readCardView = (id: string): CardBoardView =>
  typeof window !== "undefined" && window.localStorage.getItem(CARD_VIEW_STORAGE_PREFIX + id) === "tasks"
    ? "tasks"
    : "cards";

export default function TasksPage() {
  const { data: session, status: sessionStatus } = useSession();
  const router = useRouter();
  const isMobile = useIsMobile();
  const [board, setBoard] = useState<TasksBoardResponse | null>(null);
  const [boardId, setBoardId] = useState<string>(() =>
    (typeof window !== "undefined" && window.localStorage.getItem(BOARD_STORAGE_KEY)) || PERSONAL_BOARD_ID,
  );
  const [boards, setBoards] = useState<TaskBoardSummary[]>([]);
  const [manageOpen, setManageOpen] = useState(false);
  const [managingBoard, setManagingBoard] = useState<TaskBoardSummary | null>(null);
  const previousColumns = useRef<Map<string, string> | null>(null);
  const [petCompleted, setPetCompleted] = useState(false);
  useEffect(() => {
    if (!board) return;
    const previous = previousColumns.current;
    const justCompleted = board.tasks.some((task) => task.column === "done" && previous?.has(task.conversation_id) && previous.get(task.conversation_id) !== "done");
    previousColumns.current = new Map(board.tasks.map((task) => [task.conversation_id, task.column]));
    if (!justCompleted) return;
    setPetCompleted(true);
    const timer = setTimeout(() => setPetCompleted(false), 800);
    return () => { clearTimeout(timer); setPetCompleted(false); };
  }, [board]);
  const [error, setError] = useState<string | null>(null);
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [newTaskLane, setNewTaskLane] = useState<string | undefined>(undefined);
  const [dismissingAgentWork, setDismissingAgentWork] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addChatOpen, setAddChatOpen] = useState(false);
  // Desktop: clicking a non-draft card opens its chat in a side drawer so the
  // board keeps its tab. Task is kept on close for the exit animation.
  const [chatTask, setChatTask] = useState<Task | null>(null);
  const [chatDrawerOpen, setChatDrawerOpen] = useState(false);
  // Card boards: the card whose side panel is open (kept on close for the
  // exit animation). Opening one of its tasks swaps the panel for the chat
  // drawer; closing the chat brings the card back.
  const [panelCard, setPanelCard] = useState<TaskCardItem | null>(null);
  const [cardPanelOpen, setCardPanelOpen] = useState(false);
  const returnToCard = useRef(false);
  const [includedTagIds, setIncludedTagIds] = useState<string[]>([]);
  const [excludedTagIds, setExcludedTagIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  // Phones: search is an icon in the top bar; the field only takes a row once opened.
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // Card boards: show the cards, or every task inside them grouped by status.
  const [cardView, setCardView] = useState<CardBoardView>(() => readCardView(boardId));
  useEffect(() => { setCardView(readCardView(boardId)); }, [boardId]);
  const changeCardView = (view: CardBoardView) => {
    setCardView(view);
    window.localStorage.setItem(CARD_VIEW_STORAGE_PREFIX + boardId, view);
  };
  // Shared boards: only show tasks (or cards holding tasks) assigned to me.
  const [assignedToMe, setAssignedToMe] = useState(false);
  // Personal task being moved into a card.
  const [movingTask, setMovingTask] = useState<Task | null>(null);
  // null = push unavailable (unsupported browser, insecure context, or no VAPID keys)
  const [pushState, setPushState] = useState<PushState | null>(null);
  // Pause polling while a mutation is in flight to avoid clobbering optimistic state.
  const busyRef = useRef(false);

  useEffect(() => {
    if (!isPushSupported()) return;
    isPushConfigured().then((configured) => {
      if (configured) getPushState().then(setPushState).catch(() => {});
    });
  }, []);

  const togglePush = async () => {
    try {
      setPushState(
        pushState === "subscribed" ? await unsubscribeFromPush() : await subscribeToPush(),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Push setup failed");
    }
  };

  const hasBoardRef = useRef(false);
  const refreshVersion = useRef(0);

  const loadBoards = useCallback(async () => {
    try {
      setBoards((await fetchTaskBoards()).boards);
    } catch {
      // The switcher keeps its last list; the board itself still loads.
    }
  }, []);

  const selectBoard = useCallback((nextId: string) => {
    window.localStorage.setItem(BOARD_STORAGE_KEY, nextId);
    if (nextId === boardId) return;
    // Fresh board: drop the old one's cards, filters and open drawer.
    ++refreshVersion.current;
    hasBoardRef.current = false;
    setBoard(null);
    setIncludedTagIds([]);
    setExcludedTagIds([]);
    setChatDrawerOpen(false);
    setChatTask(null);
    setCardPanelOpen(false);
    setPanelCard(null);
    returnToCard.current = false;
    setBoardId(nextId);
  }, [boardId]);

  const refresh = useCallback(async () => {
    // Pause polling when the tab is hidden — but always allow the initial
    // load (a background/occluded tab would otherwise show skeletons forever).
    if (busyRef.current || (document.hidden && hasBoardRef.current)) return;
    const version = ++refreshVersion.current;
    try {
      const data = await fetchTasksBoard(searchQuery, boardId);
      hasBoardRef.current = true;
      if (busyRef.current || version !== refreshVersion.current) return;
      setBoard(data);
      // Keep the open drawer's task in sync (e.g. the async-generated title).
      // Only swap when title/prompt changed — a new object remounts the
      // drawer's title editor, which would clobber an in-progress rename.
      setChatTask((current) => {
        if (!current) return current;
        const next = data.tasks.find(
          (t) => t.conversation_id === current.conversation_id,
        );
        return next && (next.title !== current.title || next.prompt !== current.prompt)
          ? next
          : current;
      });
    } catch (e) {
      // Deleted, or access removed: fall back to your own board.
      if (e instanceof BoardNotFoundError && boardId !== PERSONAL_BOARD_ID) {
        selectBoard(PERSONAL_BOARD_ID);
        void loadBoards();
      }
      // Transient poll failures are fine — keep showing the last board.
    }
  }, [searchQuery, boardId, selectBoard, loadBoards]);

  useEffect(() => {
    if (sessionStatus === "authenticated") void loadBoards();
  }, [sessionStatus, loadBoards]);

  useEffect(() => {
    if (sessionStatus !== "authenticated") return;
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    const onVisible = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionStatus, refresh]);

  const dismissAgentWork = async () => {
    ++refreshVersion.current;
    busyRef.current = true;
    setDismissingAgentWork(true);
    setError(null);
    try {
      await saveBoardSettings({ show_agent_work: false });
      setBoard((current) => current ? { ...current, show_agent_work: false } : current);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to dismiss Agent work");
    } finally {
      busyRef.current = false;
      setDismissingAgentWork(false);
    }
  };

  const handleTaskSubmit = async (
    values: { title: string; prompt: string; lane: string; model: string; tool_config: ToolConfig },
    start: boolean,
  ) => {
    busyRef.current = true;
    try {
      let conversationId: string;
      if (editingTask) {
        await updateTask(editingTask.conversation_id, {
          title: values.title,
          prompt: values.prompt,
          task_lane: values.lane,
          model: values.model,
          tool_config: values.tool_config,
        });
        conversationId = editingTask.conversation_id;
      } else {
        const { task } = await createTask({
          prompt: values.prompt,
          title: values.title || undefined,
          lane: values.lane,
          model: values.model || undefined,
          tool_config: values.tool_config,
          board: boardId,
        });
        conversationId = task.conversation_id;
      }
      if (start) {
        router.push(`${basePath}/chat?continue=${conversationId}&start=1`);
        return;
      }
      const data = await fetchTasksBoard(searchQuery, boardId);
      setBoard(data);
    } finally {
      busyRef.current = false;
    }
  };

  const openNewTaskDrawer = async () => {
    if (!board || busyRef.current) return;
    busyRef.current = true;
    setError(null);
    try {
      const todoLane = board.lanes.find(
        (lane) => lane.id === "todo" || lane.name.trim().toLowerCase() === "todo",
      ) || board.lanes[0];
      // No title: the backend leaves title_edited false so the first run's
      // enrichment can name the task. "New task" is a display-only fallback.
      const { task } = await createTask({
        prompt: "",
        lane: todoLane?.id || "todo",
        board: boardId,
      });
      setBoard({ ...board, tasks: [task, ...board.tasks] });
      setChatTask(task);
      setChatDrawerOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create task");
    } finally {
      busyRef.current = false;
    }
  };

  const openNewCard = async () => {
    if (!board || busyRef.current) return;
    busyRef.current = true;
    setError(null);
    try {
      const { card } = await createTaskCard({ board: boardId, title: "New card", lane: board.lanes[0]?.id });
      setBoard({ ...board, cards: [...(board.cards ?? []), card] });
      setPanelCard(card);
      setCardPanelOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create card");
    } finally {
      busyRef.current = false;
    }
  };

  const laneCounts: Record<string, number> = {};
  if (board) {
    for (const lane of board.lanes) laneCounts[lane.id] = board.counts[lane.id] ?? 0;
  }
  const activeTagFilterCount = includedTagIds.length + excludedTagIds.length;
  const currentBoard = board?.board ?? boards.find((b) => b.id === boardId);
  // View-only members see the board but can't add, move or edit cards.
  const readOnly = currentBoard?.role === "viewer";
  // Card board: columns hold cards (deals, candidates...) with tasks inside.
  const cardMode = !!currentBoard?.card_mode;
  const liveCard = board?.cards?.find((c) => c.card_id === panelCard?.card_id) ?? panelCard;
  const canShare = !!currentBoard?.shared && currentBoard.role === "owner";
  const myEmail = session?.user?.email ?? null;
  // Only a task's creator or its assignee can message it; each run uses the
  // sender's accounts. Everyone else on the board reads the chat.
  const chatReadOnly = !!chatTask?.owner && !!myEmail && !canRunTask(chatTask, myEmail, currentBoard?.role);
  const assignable = assignablePeople(currentBoard);
  const assignedFilterOn = assignedToMe && !!currentBoard?.shared;
  const boardExtras = useMemo<BoardExtras>(() => ({
    myEmail,
    role: currentBoard?.role ?? null,
    assignable,
    assignedToMe: assignedFilterOn,
    // Your own tasks can move to another board you can edit.
    onMoveToBoard: cardMode ? undefined : (task: Task) => setMovingTask(task),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [myEmail, currentBoard?.role, assignable.join(","), assignedFilterOn, cardMode]);
  const openManageBoard = (target: TaskBoardSummary | null) => {
    setManagingBoard(target);
    setManageOpen(true);
  };
  const petState = board?.tasks.some((task) => task.column === "needs_input") ? "attention" : petCompleted ? "completed" : board?.tasks.some((task) => task.column === "working") ? "working" : "idle";
  const boardSwitcher = (
    <BoardSwitcher
      boards={boards}
      current={currentBoard}
      onSelect={selectBoard}
      onCreate={() => openManageBoard(null)}
      onManage={() => currentBoard && openManageBoard(currentBoard)}
    />
  );
  const viewOnlyBadge = readOnly && (
    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">View only</span>
  );
  const viewToggle = cardMode && (
    <div role="group" aria-label="Board view" className="inline-flex shrink-0 rounded-md bg-muted p-0.5">
      {(["cards", "tasks"] as const).map((view) => (
        <button
          key={view}
          type="button"
          aria-pressed={cardView === view}
          onClick={() => changeCardView(view)}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
            cardView === view ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {view === "cards" ? "Cards" : "Tasks"}
        </button>
      ))}
    </div>
  );
  const searchVisibleOnPhone = mobileSearchOpen || !!searchQuery;
  const toggleMobileSearch = () => {
    if (searchVisibleOnPhone) {
      setMobileSearchOpen(false);
      setSearchQuery("");
      return;
    }
    setMobileSearchOpen(true);
    // Focus after the row is displayed; focusing also scrolls it into view.
    requestAnimationFrame(() => searchInputRef.current?.focus());
  };

  // Phones: the board name and its actions live in the shell's top bar, so
  // the page spends no rows on a header and the task list starts higher.
  const mobileTopBar = (
    <>
      <MobileTopBarTitle>
        <h1 className="min-w-0">{boardSwitcher}</h1>
        {viewOnlyBadge}
        <PetCompanion size={24} state={petState} />
      </MobileTopBarTitle>
      <MobileTopBarActions>
        <MobileBoardActions
          searchOpen={searchVisibleOnPhone}
          onToggleSearch={toggleMobileSearch}
          searchLabel={cardMode ? "Search cards and tasks" : "Search tasks"}
          onNew={readOnly ? undefined : () => void (cardMode ? openNewCard() : openNewTaskDrawer())}
          newLabel={cardMode ? "New card" : "New task"}
          assignedToMe={currentBoard?.shared ? assignedFilterOn : undefined}
          onToggleAssigned={currentBoard?.shared ? () => setAssignedToMe((on) => !on) : undefined}
          tags={board?.tags ?? []}
          includedTagIds={includedTagIds}
          excludedTagIds={excludedTagIds}
          onIncludeTag={(id, on) => {
            setIncludedTagIds((ids) => on ? [...ids, id] : ids.filter((tagId) => tagId !== id));
            if (on) setExcludedTagIds((ids) => ids.filter((tagId) => tagId !== id));
          }}
          onExcludeTag={(id, on) => {
            setExcludedTagIds((ids) => on ? [...ids, id] : ids.filter((tagId) => tagId !== id));
            if (on) setIncludedTagIds((ids) => ids.filter((tagId) => tagId !== id));
          }}
          onClearTags={() => { setIncludedTagIds([]); setExcludedTagIds([]); }}
          onShare={canShare ? () => currentBoard && openManageBoard(currentBoard) : undefined}
          onAddChat={!readOnly && !cardMode ? () => setAddChatOpen(true) : undefined}
          pushState={pushState}
          onTogglePush={togglePush}
          onSettings={readOnly ? undefined : () => setSettingsOpen(true)}
        />
      </MobileTopBarActions>
    </>
  );

  // Everything above the board. On phones this is handed to MobileTaskBoard
  // so it scrolls with the list: the page column is fixed-height and these
  // rows cannot shrink, so leaving them outside the scroll region collapses
  // the list to 0 and pushes the pinned composer down over the bottom nav.
  const topBar = (
    <div className="flex flex-col gap-2">
      {/* Desktop header row. Phones get the same title and actions in the
          shell's top bar (mobileTopBar); `max-md:hidden` covers the first
          paint, before useIsMobile has resolved. */}
      {!isMobile && <div className="pwa-header-offset flex items-center justify-between gap-2 max-md:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="min-w-0">{boardSwitcher}</h1>
          {viewOnlyBadge}
          <PetCompanion state={petState} />
        </div>
        <div className="flex items-center gap-1">
          {viewToggle && <div className="mr-1">{viewToggle}</div>}
          {currentBoard?.shared && (
            <Button variant={assignedFilterOn ? "secondary" : "ghost"} size="sm" aria-pressed={assignedFilterOn}
              onClick={() => setAssignedToMe((on) => !on)}>
              <RiUserLine className="h-4 w-4" /> Assigned to me
            </Button>
          )}
          {board && board.tags.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant={activeTagFilterCount ? "secondary" : "ghost"} size="sm">
                  <RiFilter3Line className="h-4 w-4" /> Tags{activeTagFilterCount ? ` (${activeTagFilterCount})` : ""}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-48">
                <DropdownMenuLabel>Include</DropdownMenuLabel>
                {board.tags.map((tag) => (
                  <DropdownMenuCheckboxItem key={`include-${tag.id}`} checked={includedTagIds.includes(tag.id)}
                    onCheckedChange={(checked) => {
                      setIncludedTagIds((ids) => checked ? [...ids, tag.id] : ids.filter((id) => id !== tag.id));
                      if (checked) setExcludedTagIds((ids) => ids.filter((id) => id !== tag.id));
                    }}
                    onSelect={(e) => e.preventDefault()}>
                    {tag.name}
                  </DropdownMenuCheckboxItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Exclude</DropdownMenuLabel>
                {board.tags.map((tag) => (
                  <DropdownMenuCheckboxItem key={`exclude-${tag.id}`} checked={excludedTagIds.includes(tag.id)}
                    onCheckedChange={(checked) => {
                      setExcludedTagIds((ids) => checked ? [...ids, tag.id] : ids.filter((id) => id !== tag.id));
                      if (checked) setIncludedTagIds((ids) => ids.filter((id) => id !== tag.id));
                    }}
                    onSelect={(e) => e.preventDefault()}>
                    {tag.name}
                  </DropdownMenuCheckboxItem>
                ))}
                {activeTagFilterCount > 0 && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => { setIncludedTagIds([]); setExcludedTagIds([]); }}>
                      Clear filters
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!readOnly && (
            <Button size="sm" onClick={() => void (cardMode ? openNewCard() : openNewTaskDrawer())}>
              <RiAddLine className="h-4 w-4" />
              {cardMode ? "New card" : "New task"}
            </Button>
          )}
          {canShare && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
                  onClick={() => currentBoard && openManageBoard(currentBoard)}
                  aria-label="Share board"
                >
                  <RiUserAddLine className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Share board</TooltipContent>
            </Tooltip>
          )}
          {!readOnly && !cardMode && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
                  onClick={() => setAddChatOpen(true)}
                >
                  <RiChatHistoryLine className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Add existing chat</TooltipContent>
            </Tooltip>
          )}
          {pushState !== null && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
                  disabled={pushState === "denied"}
                  onClick={togglePush}
                >
                  {pushState === "subscribed"
                    ? <RiNotification3Line className="h-4 w-4 text-brand-600" />
                    : <RiNotificationOffLine className="h-4 w-4" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {pushState === "denied"
                  ? "Notifications blocked in browser settings"
                  : pushState === "subscribed"
                    ? "Notifications on"
                    : "Notify me when a task needs input"}
              </TooltipContent>
            </Tooltip>
          )}
          {!readOnly && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
                  onClick={() => setSettingsOpen(true)}
                  aria-label="Board settings"
                >
                  <RiSettings3Line className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Board settings</TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>}

      <InstallHint />

      {isMobile && viewToggle && <div>{viewToggle}</div>}

      {board && !cardMode && board.show_agent_work !== false && (
        <AgentAttention onDismiss={dismissAgentWork} dismissing={dismissingAgentWork} />
      )}

      <div className={cn("relative w-full sm:max-w-sm", !searchVisibleOnPhone && "max-md:hidden")}>
        <RiSearchLine className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={searchInputRef}
          type="text"
          aria-label={cardMode ? "Search cards and tasks" : "Search tasks"}
          placeholder={cardMode ? "Search cards and their tasks..." : "Search task titles and conversations..."}
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          className="h-9 pl-9 pr-9"
        />
        {searchQuery && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Clear task search"
            className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 text-muted-foreground"
            onClick={() => setSearchQuery("")}
          >
            <RiCloseLine className="h-4 w-4" />
          </Button>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );

  // Only the phone task list renders the top bar itself. Card boards use
  // CardBoard on every screen size, so they keep the page-level top bar.
  const boardOwnsTopBar = !!board && isMobile && !cardMode;

  return (
    <BoardExtrasContext.Provider value={boardExtras}>
    <div className="flex h-full min-h-0 flex-col space-y-2 md:p-4 lg:p-6">
      {mobileTopBar}
      {!boardOwnsTopBar && topBar}

      {board ? (
        cardMode && cardView === "tasks" ? (
          <CardTasksView
            board={board}
            onOpenTask={(task) => { setChatTask(task); setChatDrawerOpen(true); }}
            onOpenCard={(card) => { setPanelCard(card); setCardPanelOpen(true); }}
            includedTagIds={includedTagIds}
            excludedTagIds={excludedTagIds}
          />
        ) : cardMode ? (
          <CardBoard
            board={board}
            onBoardChange={setBoard}
            onRefresh={refresh}
            onOpenCard={(card) => { setPanelCard(card); setCardPanelOpen(true); }}
            onError={setError}
            readOnly={readOnly}
          />
        ) : isMobile ? (
          <MobileTaskBoard
            board={board}
            onBoardChange={setBoard}
            onRefresh={refresh}
            onEditDraft={(task) => { setEditingTask(task); setTaskDialogOpen(true); }}
            onAddTask={(laneId) => { setEditingTask(null); setNewTaskLane(laneId); setTaskDialogOpen(true); }}
            onError={setError}
            includedTagIds={includedTagIds}
            excludedTagIds={excludedTagIds}
            readOnly={readOnly}
            header={topBar}
          />
        ) : (
          <>
            <TaskBoard
              board={board}
              onBoardChange={setBoard}
              onRefresh={refresh}
              onEditDraft={(task) => { setChatTask(task); setChatDrawerOpen(true); }}
              onAddTask={() => void openNewTaskDrawer()}
              onOpenChat={(task) => { setChatTask(task); setChatDrawerOpen(true); }}
              onError={setError}
              includedTagIds={includedTagIds}
              excludedTagIds={excludedTagIds}
              readOnly={readOnly}
            />
            {/* Desktop capture box — mirrors the PWA. Mobile renders its own
                inside MobileTaskBoard, so only add it here. Fires the task
                immediately (start: true); it lands in Working on refresh. */}
            {!readOnly && <QuickAddTask onAdded={refresh} boardId={boardId} />}
          </>
        )
      ) : (
        <div className="flex gap-4">
          {(isMobile ? [0] : [0, 1, 2, 3]).map((i) => (
            <div key={i} className={isMobile ? "flex-1 space-y-2" : "w-64 space-y-2"}>
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ))}
        </div>
      )}

      <TaskDialog
        open={taskDialogOpen}
        onOpenChange={setTaskDialogOpen}
        lanes={board?.lanes ?? []}
        task={editingTask}
        defaultLane={newTaskLane}
        onSubmit={handleTaskSubmit}
      />
      <AddChatDialog
        open={addChatOpen}
        onOpenChange={setAddChatOpen}
        onAdded={refresh}
        boardId={boardId}
      />
      <BoardSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        laneCounts={laneCounts}
        onSaved={refresh}
        boardId={boardId}
        boardName={currentBoard?.shared ? currentBoard.name : undefined}
        shared={!!currentBoard?.shared}
        cardMode={cardMode}
      />
      {board && cardMode && (
        <CardPanel
          board={board}
          card={liveCard}
          open={cardPanelOpen}
          onOpenChange={setCardPanelOpen}
          onBoardChange={setBoard}
          onRefresh={refresh}
          onOpenTask={(task) => {
            returnToCard.current = true;
            setCardPanelOpen(false);
            setChatTask(task);
            setChatDrawerOpen(true);
          }}
          readOnly={readOnly}
          myEmail={myEmail}
        />
      )}
      <ManageBoardDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        board={managingBoard}
        onSaved={(saved) => {
          void loadBoards();
          if (!managingBoard) selectBoard(saved.id);
          else void refresh();
        }}
        onDeleted={() => {
          selectBoard(PERSONAL_BOARD_ID);
          void loadBoards();
        }}
      />
      <TaskChatDrawer
        task={chatTask}
        readOnly={chatReadOnly}
        canRename={!readOnly}
        open={chatDrawerOpen}
        onOpenChange={(open) => {
          setChatDrawerOpen(open);
          if (!open) {
            refresh();
            // Opened from a card: go back to that card.
            if (returnToCard.current && panelCard) setCardPanelOpen(true);
            returnToCard.current = false;
          }
        }}
        onTaskChange={(updatedTask) => {
          setChatTask(updatedTask);
          setBoard((current) => current ? {
            ...current,
            tasks: current.tasks.map((task) =>
              task.conversation_id === updatedTask.conversation_id ? updatedTask : task,
            ),
          } : current);
        }}
      />
      <MoveToBoardDialog
        task={movingTask}
        open={!!movingTask}
        onOpenChange={(open) => { if (!open) setMovingTask(null); }}
        onMoved={refresh}
      />
    </div>
    </BoardExtrasContext.Provider>
  );
}
