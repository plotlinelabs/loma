"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RiExternalLinkLine, RiLoader4Line, RiPencilLine } from "@remixicon/react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { usePetSettingsOpen } from "@/components/PetCompanion";
import { HumanTaskPanel } from "./HumanTaskPanel";
import ChatWithArtifacts from "@/components/ChatWithArtifacts";
import { rebuildItemsFromConversation, type ChatItem } from "@/components/ChatPanel";
import type { Artifact } from "@/components/ArtifactViewer";
import { basePath, fetchConversation, starTask, unstarTask, updateTask, type ChatFile, type Task } from "@/lib/api";
import { AssigneeSelect, useBoardExtras } from "./boardExtras";
import { StarButton } from "./TaskStar";

/** Loads and renders one conversation inside the drawer. Keyed by
 * conversation_id from the parent so switching tasks resets all state. */
function DrawerConversation({
  conversationId,
  onStreamComplete,
  readOnly = false,
  reloadToken = 0,
}: {
  conversationId: string;
  onStreamComplete?: (conversationId: string) => void;
  readOnly?: boolean;
  /** Bumped by the parent to re-fetch the conversation in place (e.g. voice
   * mode steered or started this task while the drawer stayed open). */
  reloadToken?: number;
}) {
  const [loading, setLoading] = useState(true);
  const [humanTask, setHumanTask] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initialItems, setInitialItems] = useState<ChatItem[] | undefined>();
  const [initialArtifacts, setInitialArtifacts] = useState<Artifact[] | undefined>();
  const [initialStatus, setInitialStatus] = useState<string | undefined>();
  const [draftPrompt, setDraftPrompt] = useState<string | null>(null);
  const [draftFiles, setDraftFiles] = useState<ChatFile[] | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [toolConfig, setToolConfig] = useState<import("@/lib/api").ToolConfig | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchConversation(conversationId);
        if (cancelled) return;
        setHumanTask(!!data.conversation.human_task);
        setModel(data.conversation.model || null);
        setToolConfig(data.conversation.tool_config || null);
        if (data.conversation.task_status === "todo" && !data.conversation.status) {
          // Unstarted board draft: nothing has been sent yet — put the staged
          // prompt in the composer instead of rendering it as a sent message
          // (mirrors /chat's handling).
          setInitialItems([]);
          setDraftPrompt(data.conversation.prompt);
          setDraftFiles(data.conversation.draft_files || null);
        } else {
          // A reload may find a once-draft task now running: drop the staged
          // composer prompt and render the real transcript instead.
          setDraftPrompt(null);
          setDraftFiles(null);
          const { items, artifacts } = rebuildItemsFromConversation(
            data.conversation.messages,
            data.conversation.prompt,
            data.conversation.final_response,
            data.turns,
            data.artifacts,
          );
          setInitialItems(items);
          setInitialArtifacts(artifacts);
          setInitialStatus(data.conversation.status);
        }
      } catch (e) {
        console.error("Failed to load conversation in task drawer:", e);
        if (!cancelled) setError("Failed to load conversation");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // reloadToken re-runs the fetch in place; loading is left as-is so the
    // transcript updates without flashing the full-panel spinner.
  }, [conversationId, reloadToken]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center bg-muted/30">
        <div className="flex items-center gap-2 text-muted-foreground">
          <RiLoader4Line size={16} className="animate-spin text-brand-600" />
          Loading conversation...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (humanTask) return <HumanTaskPanel conversationId={conversationId} />;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ChatWithArtifacts
        initialItems={initialItems}
        initialArtifacts={initialArtifacts}
        conversationId={conversationId}
        initialPrompt={draftPrompt || undefined}
        initialFiles={draftFiles || undefined}
        initialModel={model || undefined}
        initialToolConfig={toolConfig}
        initialStatus={initialStatus}
        draftStorageKey={`loma-task-draft-${conversationId}`}
        onStreamComplete={onStreamComplete}
        readOnly={readOnly}
      />
    </div>
  );
}

/** Right-side drawer that opens a board task's conversation in place, so the
 * board never loses its tab. Unsent composer text is drafted to localStorage
 * (per conversation) and restored if the drawer is reopened. */
export function TaskChatDrawer({
  task,
  open,
  onOpenChange,
  onTaskChange,
  readOnly = false,
  canRename = true,
  reloadToken = 0,
}: {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskChange?: (task: Task) => void;
  /** View-only member of the task's board: transcript only, no composer. */
  readOnly?: boolean;
  /** False for view-only board members. */
  canRename?: boolean;
  /** Bumped when something outside the drawer (e.g. voice mode) changed this
   * task, so the open transcript reloads in place. */
  reloadToken?: number;
}) {
  // Refs so the delayed title fetch below reads the latest task/callback, not
  // the ones captured when the stream started.
  const taskRef = useRef(task);
  const onTaskChangeRef = useRef(onTaskChange);
  useEffect(() => {
    taskRef.current = task;
    onTaskChangeRef.current = onTaskChange;
  }, [task, onTaskChange]);

  // After a run finishes, server-side enrichment generates a title
  // asynchronously — poll once shortly after so the drawer header and board
  // card pick it up (mirrors handleStreamComplete on the chat page).
  const handleStreamComplete = useCallback((conversationId: string) => {
    setTimeout(async () => {
      try {
        const data = await fetchConversation(conversationId);
        const nextTitle = data.conversation?.title;
        const current = taskRef.current;
        if (
          nextTitle &&
          current &&
          current.conversation_id === conversationId &&
          nextTitle !== current.title
        ) {
          onTaskChangeRef.current?.({ ...current, title: nextTitle });
        }
      } catch {
        // Board polling will pick the title up on its next pass.
      }
    }, 4000);
  }, []);

  const petSettingsOpen = usePetSettingsOpen();
  const { myEmail, role, assignable } = useBoardExtras();
  const [assignError, setAssignError] = useState<string | null>(null);
  // Shared-board tasks can be assigned to an owner/editor: it marks whose task it is.
  const showAssignee = !!task?.task_board_id && assignable.length > 0;
  const canAssign = role === "owner" || role === "editor";
  const assign = async (email: string | null) => {
    if (!task) return;
    setAssignError(null);
    try {
      const { task: updated } = await updateTask(task.conversation_id, { task_assignee: email });
      if (updated) onTaskChange?.(updated);
    } catch (e) {
      setAssignError(e instanceof Error ? e.message : "Could not assign task");
    }
  };

  // Private star: the task also shows on your own board.
  const toggleStar = async (target: Task) => {
    setAssignError(null);
    const starred = !target.starred;
    onTaskChange?.({ ...target, starred });
    try {
      await (starred ? starTask(target.conversation_id) : unstarTask(target.conversation_id));
    } catch (e) {
      onTaskChange?.({ ...target, starred: !starred });
      setAssignError(e instanceof Error ? e.message : "Could not update star");
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        onInteractOutside={(event) => {
          // A closing picker can dispatch its outside event after open becomes false.
          if (petSettingsOpen || (event.target instanceof Element && event.target.closest("[data-pet-settings]"))) event.preventDefault();
        }}
        onEscapeKeyDown={(event) => { if (petSettingsOpen) event.preventDefault(); }}
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[min(1100px,92vw)]"
      >
        <div className="flex flex-shrink-0 items-center gap-1 border-b border-border py-2.5 pl-4 pr-12">
          {task?.human_task && <SheetTitle className="flex-1 text-sm">{task.title}</SheetTitle>}
          {task && !task.human_task && !canRename && (
            <SheetTitle className="min-w-0 flex-1 truncate font-heading text-base font-semibold">
              {task.title || task.prompt || "New task"}
            </SheetTitle>
          )}
          {task && !task.human_task && canRename && (
            <EditableTaskTitle
              key={`${task.conversation_id}:${task.title || task.prompt}`}
              task={task}
              onTaskChange={onTaskChange}
            />
          )}
          {task && readOnly && task.owner && (
            <span className="shrink-0 truncate text-xs text-muted-foreground"
              title="You have view-only access to this board, so you can read this task but not message it">by {task.owner}</span>
          )}
          {task && !task.human_task && !!task.task_board_id && (
            <StarButton task={task} onToggle={(t) => void toggleStar(t)} className="h-7 w-7" iconClassName="h-4 w-4" />
          )}
          {task && showAssignee && (
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="text-xs text-muted-foreground">Assignee</span>
              <AssigneeSelect key={task.conversation_id} value={task.assignee} people={assignable} me={myEmail}
                disabled={!canAssign} onChange={(email) => void assign(email)} className="h-7 w-44 text-xs" />
            </div>
          )}
          {task && !readOnly && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-sm" className="text-muted-foreground" asChild>
                  <a
                    href={`${basePath}/chat?continue=${task.conversation_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="Open in full tab"
                  >
                    <RiExternalLinkLine size={16} />
                  </a>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Open in full tab</TooltipContent>
            </Tooltip>
          )}
        </div>
        {assignError && <p className="border-b border-border px-4 py-1.5 text-xs text-destructive">{assignError}</p>}
        {task && (
          <DrawerConversation
            key={task.conversation_id}
            conversationId={task.conversation_id}
            onStreamComplete={handleStreamComplete}
            readOnly={readOnly}
            reloadToken={reloadToken}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function EditableTaskTitle({ task, onTaskChange }: { task: Task; onTaskChange?: (task: Task) => void }) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState(task.title || task.prompt || "New task");

  const saveTitle = async () => {
    if (!task) return;
    setEditingTitle(false);
    const fallback = task.title || task.prompt || "New task";
    const nextTitle = title.trim();
    // Empty or unchanged input is a no-op — saving would store the display
    // fallback as a real user title and lock out auto-titling (title_edited).
    if (!nextTitle || nextTitle === fallback) {
      setTitle(fallback);
      return;
    }
    setTitle(nextTitle);
    try {
      const { task: updatedTask } = await updateTask(task.conversation_id, { title: nextTitle });
      onTaskChange?.(updatedTask || { ...task, title: nextTitle });
    } catch {
      setTitle(task.title || task.prompt || "New task");
    }
  };

  return (
    <div className="min-w-0 flex-1">
            {editingTitle ? (
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                onBlur={() => void saveTitle()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void saveTitle();
                  if (event.key === "Escape") {
                    setTitle(task?.title || task?.prompt || "New task");
                    setEditingTitle(false);
                  }
                }}
                aria-label="Task title"
                maxLength={200}
                autoFocus
                className="h-8 w-full max-w-md rounded-md border border-input bg-background px-2 font-heading text-base font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            ) : (
              <div className="flex min-w-0 items-center gap-1">
                <SheetTitle
                  className="min-w-0 truncate font-heading text-base font-semibold"
                  onClick={() => setEditingTitle(true)}
                  title="Click to rename"
                >
                  {task ? task.title || task.prompt || "New task" : "Task"}
                </SheetTitle>
                {task && (
                  <Button variant="ghost" size="icon-xs" className="shrink-0 text-muted-foreground" onClick={() => setEditingTitle(true)} aria-label="Rename task">
                    <RiPencilLine size={14} />
                  </Button>
                )}
              </div>
            )}
    </div>
  );
}
