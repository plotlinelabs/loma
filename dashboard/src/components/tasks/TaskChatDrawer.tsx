"use client";

import { useEffect, useState } from "react";
import { RiExternalLinkLine, RiLoader4Line } from "@remixicon/react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import ChatWithArtifacts from "@/components/ChatWithArtifacts";
import { rebuildItemsFromConversation, type ChatItem } from "@/components/ChatPanel";
import type { Artifact } from "@/components/ArtifactViewer";
import { basePath, fetchConversation, type ChatFile, type Task } from "@/lib/api";

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
        initialStatus={initialStatus}
        draftStorageKey={`loma-task-draft-${conversationId}`}
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
}: {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[min(1100px,92vw)]"
      >
        <div className="flex flex-shrink-0 items-center gap-1 border-b border-border py-2.5 pl-4 pr-12">
          <SheetTitle className="min-w-0 flex-1 truncate font-heading text-base font-semibold">
            {task ? task.title || task.prompt : "Task"}
          </SheetTitle>
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
      </SheetContent>
    </Sheet>
  );
}
