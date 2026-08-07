"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { RiAddLine, RiChatHistoryLine, RiCloseLine, RiFilter3Line, RiNotification3Line, RiNotificationOffLine, RiSearchLine, RiSettings3Line } from "@remixicon/react";
import {
  getPushState,
  isPushConfigured,
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
  type PushState,
} from "@/lib/push";
import {
  basePath,
  createTask,
  fetchTasksBoard,
  updateTask,
  type Task,
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
import { useIsMobile } from "@/hooks/useIsMobile";

const POLL_INTERVAL_MS = 5000;

export default function TasksPage() {
  const { status: sessionStatus } = useSession();
  const router = useRouter();
  const isMobile = useIsMobile();
  const [board, setBoard] = useState<TasksBoardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [newTaskLane, setNewTaskLane] = useState<string | undefined>(undefined);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addChatOpen, setAddChatOpen] = useState(false);
  // Desktop: clicking a non-draft card opens its chat in a side drawer so the
  // board keeps its tab. Task is kept on close for the exit animation.
  const [chatTask, setChatTask] = useState<Task | null>(null);
  const [chatDrawerOpen, setChatDrawerOpen] = useState(false);
  const [includedTagIds, setIncludedTagIds] = useState<string[]>([]);
  const [excludedTagIds, setExcludedTagIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
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
  const refresh = useCallback(async () => {
    // Pause polling when the tab is hidden — but always allow the initial
    // load (a background/occluded tab would otherwise show skeletons forever).
    if (busyRef.current || (document.hidden && hasBoardRef.current)) return;
    try {
      const data = await fetchTasksBoard(searchQuery);
      hasBoardRef.current = true;
      setBoard(data);
    } catch {
      // Transient poll failures are fine — keep showing the last board.
    }
  }, [searchQuery]);

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

  const handleTaskSubmit = async (
    values: { title: string; prompt: string; lane: string; model: string },
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
        });
        conversationId = editingTask.conversation_id;
      } else {
        const { task } = await createTask({
          prompt: values.prompt,
          title: values.title || undefined,
          lane: values.lane,
          model: values.model || undefined,
        });
        conversationId = task.conversation_id;
      }
      if (start) {
        router.push(`${basePath}/chat?continue=${conversationId}&start=1`);
        return;
      }
      const data = await fetchTasksBoard(searchQuery);
      setBoard(data);
    } finally {
      busyRef.current = false;
    }
  };

  const laneCounts: Record<string, number> = {};
  if (board) {
    for (const lane of board.lanes) laneCounts[lane.id] = board.counts[lane.id] ?? 0;
  }
  const activeTagFilterCount = includedTagIds.length + excludedTagIds.length;

  return (
    <div className="flex h-full flex-col space-y-2 p-4 lg:p-6">
      <div className="pwa-header-offset flex items-center justify-between">
        <h1 className="text-lg font-semibold">Tasks</h1>
        <div className="flex items-center gap-1">
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
          <Button size="sm" onClick={() => { setEditingTask(null); setNewTaskLane(undefined); setTaskDialogOpen(true); }}>
            <RiAddLine className="h-4 w-4" />
            New task
          </Button>
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
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground"
                onClick={() => setSettingsOpen(true)}
              >
                <RiSettings3Line className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Board settings</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <InstallHint />

      <div className="relative w-full sm:max-w-sm">
        <RiSearchLine className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="text"
          aria-label="Search tasks"
          placeholder="Search task titles and conversations..."
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

      {board ? (
        isMobile ? (
          <MobileTaskBoard
            board={board}
            onBoardChange={setBoard}
            onRefresh={refresh}
            onEditDraft={(task) => { setEditingTask(task); setTaskDialogOpen(true); }}
            onAddTask={(laneId) => { setEditingTask(null); setNewTaskLane(laneId); setTaskDialogOpen(true); }}
            onError={setError}
            includedTagIds={includedTagIds}
            excludedTagIds={excludedTagIds}
          />
        ) : (
          <>
            <TaskBoard
              board={board}
              onBoardChange={setBoard}
              onRefresh={refresh}
              onEditDraft={(task) => { setEditingTask(task); setTaskDialogOpen(true); }}
              onAddTask={(laneId) => { setEditingTask(null); setNewTaskLane(laneId); setTaskDialogOpen(true); }}
              onOpenChat={(task) => { setChatTask(task); setChatDrawerOpen(true); }}
              onError={setError}
              includedTagIds={includedTagIds}
              excludedTagIds={excludedTagIds}
            />
            {/* Desktop capture box — mirrors the PWA. Mobile renders its own
                inside MobileTaskBoard, so only add it here. Fires the task
                immediately (start: true); it lands in Working on refresh. */}
            <QuickAddTask onAdded={refresh} />
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
      />
      <BoardSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        laneCounts={laneCounts}
        onSaved={refresh}
      />
      <TaskChatDrawer
        task={chatTask}
        open={chatDrawerOpen}
        onOpenChange={(open) => {
          setChatDrawerOpen(open);
          if (!open) refresh();
        }}
      />
    </div>
  );
}
