"use client";

import { useEffect, useState } from "react";
import { RiArrowUpLine, RiCloseLine, RiExternalLinkLine, RiLoader4Line, RiPencilLine } from "@remixicon/react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import ChatWithArtifacts from "@/components/ChatWithArtifacts";
import { rebuildItemsFromConversation, type ChatItem } from "@/components/ChatPanel";
import type { Artifact } from "@/components/ArtifactViewer";
import { basePath, fetchConversation, updateTask, type ChatFile, type Task } from "@/lib/api";

const HISTORY_BATCH_SIZE = 40;

/** Loads and renders one conversation inside the drawer. Keyed by
 * conversation_id from the parent so switching tasks resets all state. */
function DrawerConversation({ conversationId }: { conversationId: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [initialItems, setInitialItems] = useState<ChatItem[] | undefined>();
  const [initialArtifacts, setInitialArtifacts] = useState<Artifact[] | undefined>();
  const [initialStatus, setInitialStatus] = useState<string | undefined>();
  const [draftPrompt, setDraftPrompt] = useState<string | null>(null);
  const [draftFiles, setDraftFiles] = useState<ChatFile[] | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [historyItemCount, setHistoryItemCount] = useState(0);
  const [visibleHistoryLimit, setVisibleHistoryLimit] = useState(HISTORY_BATCH_SIZE);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchConversation(conversationId);
        if (cancelled) return;
        setModel(data.conversation.model || null);
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
          setHistoryItemCount(items.length);
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
      {historyItemCount > visibleHistoryLimit && (
        <div className="flex flex-shrink-0 justify-center border-b border-border bg-muted/30 px-3 py-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setVisibleHistoryLimit((count) => count + HISTORY_BATCH_SIZE)}
          >
            <RiArrowUpLine size={15} />
            Load earlier messages ({historyItemCount - visibleHistoryLimit} remaining)
          </Button>
        </div>
      )}
      <ChatWithArtifacts
        initialItems={initialItems}
        initialArtifacts={initialArtifacts}
        conversationId={conversationId}
        initialPrompt={draftPrompt || undefined}
        initialFiles={draftFiles || undefined}
        initialModel={model || undefined}
        initialStatus={initialStatus}
        draftStorageKey={`loma-task-draft-${conversationId}`}
        historyItemLimit={visibleHistoryLimit}
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
  embedded = false,
}: {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskChange?: (task: Task) => void;
  embedded?: boolean;
}) {
  const content = (
    <>
      <div className="flex flex-shrink-0 items-center gap-1 border-b border-border py-2.5 pl-4 pr-12">
        {task && (
          <EditableTaskTitle
            key={`${task.conversation_id}:${task.title || task.prompt}`}
            task={task}
            onTaskChange={onTaskChange}
          />
        )}
        {embedded && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="text-muted-foreground"
            onClick={() => onOpenChange(false)}
            aria-label="Close task drawer"
          >
            <RiCloseLine size={16} />
          </Button>
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
        />
      )}
    </>
  );

  if (embedded) {
    if (!open || !task) return null;
    return (
      <aside className="flex min-w-[420px] flex-1 flex-col overflow-hidden border-l border-border bg-background">
        {content}
      </aside>
    );
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[min(1100px,92vw)]"
      >
        {content}
      </SheetContent>
    </Sheet>
  );
}

function EditableTaskTitle({ task, onTaskChange }: { task: Task; onTaskChange?: (task: Task) => void }) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState(task.title || task.prompt || "New task");

  const saveTitle = async () => {
    if (!task) return;
    const nextTitle = title.trim() || "New task";
    setTitle(nextTitle);
    setEditingTitle(false);
    if (nextTitle === task.title) return;
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
