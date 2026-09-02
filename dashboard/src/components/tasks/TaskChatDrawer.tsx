"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RiExternalLinkLine, RiLoader4Line, RiPencilLine } from "@remixicon/react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import ChatWithArtifacts from "@/components/ChatWithArtifacts";
import { rebuildItemsFromConversation, type ChatItem } from "@/components/ChatPanel";
import type { Artifact } from "@/components/ArtifactViewer";
import { basePath, fetchConversation, updateTask, type ChatFile, type Task } from "@/lib/api";

/** Loads and renders one conversation inside the drawer. Keyed by
 * conversation_id from the parent so switching tasks resets all state. */
function DrawerConversation({
  conversationId,
  onStreamComplete,
}: {
  conversationId: string;
  onStreamComplete?: (conversationId: string) => void;
}) {
  const [loading, setLoading] = useState(true);
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
  }, [conversationId]);

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
}: {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskChange?: (task: Task) => void;
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

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[min(1100px,92vw)]"
      >
        <div className="flex flex-shrink-0 items-center gap-1 border-b border-border py-2.5 pl-4 pr-12">
          {task && (
            <EditableTaskTitle
              key={`${task.conversation_id}:${task.title || task.prompt}`}
              task={task}
              onTaskChange={onTaskChange}
            />
          )}
          {task && (
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
        {task && (
          <DrawerConversation
            key={task.conversation_id}
            conversationId={task.conversation_id}
            onStreamComplete={handleStreamComplete}
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
