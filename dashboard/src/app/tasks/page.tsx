"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { RiAddLine, RiChatHistoryLine, RiNotification3Line, RiNotificationOffLine, RiSettings3Line } from "@remixicon/react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TaskBoard } from "@/components/tasks/TaskBoard";
import { TaskDialog } from "@/components/tasks/TaskDialog";
import { AddChatDialog } from "@/components/tasks/AddChatDialog";
import { BoardSettingsDialog } from "@/components/tasks/BoardSettingsDialog";

const POLL_INTERVAL_MS = 5000;

export default function TasksPage() {
  const { status: sessionStatus } = useSession();
  const router = useRouter();
  const [board, setBoard] = useState<TasksBoardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addChatOpen, setAddChatOpen] = useState(false);
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
      const data = await fetchTasksBoard();
      hasBoardRef.current = true;
      setBoard(data);
    } catch {
      // Transient poll failures are fine — keep showing the last board.
    }
  }, []);

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
      const data = await fetchTasksBoard();
      setBoard(data);
    } finally {
      busyRef.current = false;
    }
  };

  const laneCounts: Record<string, number> = {};
  if (board) {
    for (const lane of board.lanes) laneCounts[lane.id] = board.counts[lane.id] ?? 0;
  }

  return (
    <div className="flex h-full flex-col space-y-2 p-4 lg:p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Tasks</h1>
        <div className="flex items-center gap-1">
          <Button size="sm" onClick={() => { setEditingTask(null); setTaskDialogOpen(true); }}>
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

      {error && <p className="text-xs text-destructive">{error}</p>}

      {board ? (
        <TaskBoard
          board={board}
          onBoardChange={setBoard}
          onRefresh={refresh}
          onEditDraft={(task) => { setEditingTask(task); setTaskDialogOpen(true); }}
          onError={setError}
        />
      ) : (
        <div className="flex gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="w-64 space-y-2">
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
    </div>
  );
}
