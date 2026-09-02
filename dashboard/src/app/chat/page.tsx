"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useState, useCallback, Suspense } from "react";
import {
  RiPencilLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiPushpinFill,
  RiPushpinLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { ChatItem } from "../../components/ChatPanel";
import type { Artifact } from "../../components/ArtifactViewer";
import type { ChatFile } from "../../lib/api";
import { rebuildItemsFromConversation } from "../../components/ChatPanel";
import ChatContextMenu from "../../components/ChatContextMenu";
import { CostChip } from "../../components/CostChip";
import ChatWithArtifacts from "../../components/ChatWithArtifacts";
import { fetchConversation, fetchFlow, basePath } from "../../lib/api";
import { useUser } from "../../lib/UserContext";

// ── Chat Page Content ───────────────────────────────────────────────────────

function ChatPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, isPinned, togglePin, projects, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject } = useUser();
  const continueId = searchParams.get("continue");
  const flowId = searchParams.get("flow");
  const taskId = null; // Tasks system removed
  const promptParam = searchParams.get("prompt");
  const autoSendParam = searchParams.get("autoSend") === "true";
  // Tasks board start flow: ?continue=<id>&start=1 auto-sends a staged draft's prompt
  const startParam = searchParams.get("start") === "1";
  const skillContextParam = searchParams.get("skillContext");
  const [initialItems, setInitialItems] = useState<ChatItem[] | undefined>();
  const [initialArtifacts, setInitialArtifacts] = useState<Artifact[] | undefined>();
  const [initialStatus, setInitialStatus] = useState<string | undefined>();
  const [loading, setLoading] = useState(!!continueId || !!flowId || !!taskId);
  const [promptPreview, setPromptPreview] = useState("");
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Draft prompt/files for staged board tasks — prefill the composer (or auto-send with ?start=1)
  const [taskDraftPrompt, setTaskDraftPrompt] = useState<string | null>(null);
  const [taskDraftFiles, setTaskDraftFiles] = useState<ChatFile[] | null>(null);
  const [taskModel, setTaskModel] = useState<string | null>(null);
  const [pinnedAgentId, setPinnedAgentId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<"todo" | "active" | "done" | null>(null);
  const [conversationOwner, setConversationOwner] = useState<string | null>(null);
  const [conversationShared, setConversationShared] = useState(false);

  // Track the active conversation ID — starts from URL param but also updates
  // when a fresh chat creates a new conversation (via onConversationCreated callback)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(continueId);

  // Keep activeConversationId in sync if the URL param changes (e.g. navigation)
  useEffect(() => {
    if (continueId) setActiveConversationId(continueId);
  }, [continueId]);

  // Callback for ChatPanel to notify us when a new conversation is created
  const handleConversationCreated = useCallback((newId: string) => {
    setActiveConversationId(newId);
    setConversationOwner(user?.email || null);
  }, [user?.email]);

  // After stream completes, poll for the async-generated title
  const handleStreamComplete = useCallback((convId: string) => {
    setTimeout(async () => {
      try {
        const data = await fetchConversation(convId);
        if (data.conversation?.title) {
          setConversationTitle(data.conversation.title);
        }
        window.dispatchEvent(new Event("conversations-updated"));
      } catch {}
    }, 4000);
  }, []);

  useEffect(() => {
    if (flowId) {
      loadFlow();
    } else if (continueId) {
      loadConversation();
    }

    async function loadFlow() {
      try {
        const data = await fetchFlow(flowId!);
        const flow = data.flow;
        const assistantMsg = [
          `Here's the current configuration for **${flow.name}** (\`${flow.flow_id}\`):`,
          "",
          `- **Schedule:** ${flow.frequency || flow.cron || "One-time"}`,
          `- **Timezone:** ${flow.timezone}`,
          `- **Status:** ${flow.status}`,
          "",
          "**Current prompt:**",
          `> ${flow.prompt.split("\n").join("\n> ")}`,
          "",
          "What would you like to change?",
        ].join("\n");
        setInitialItems([{ role: "assistant", content: assistantMsg }]);
        setPromptPreview(`Editing: ${flow.name}`);
      } catch (e) {
        console.error("Failed to load flow for editing:", e);
        setError("Failed to load flow");
      } finally {
        setLoading(false);
      }
    }

    async function loadConversation() {
      try {
        const data = await fetchConversation(continueId!);
        setConversationOwner(data.conversation.metadata?.user_name || null);
        setConversationShared(data.conversation.metadata?.visibility === "shared");
        setPinnedAgentId(data.conversation.metadata?.agent_id || null);
        setTaskStatus(data.conversation.task_status || null);
        if (data.conversation.task_status === "todo" && !data.conversation.status) {
          // Unstarted board draft: nothing has been sent yet. Don't rebuild
          // items (the fallback would render the draft prompt as an already-
          // sent message) — put the draft in the composer instead. Parked
          // chats (staged after running) fall through to the normal rebuild.
          setInitialItems([]);
          setTaskDraftPrompt(data.conversation.prompt);
          setTaskDraftFiles(data.conversation.draft_files || null);
          setTaskModel(data.conversation.model || null);
          setConversationTitle(data.conversation.title || null);
          setPromptPreview(
            data.conversation.prompt.length > 80
              ? data.conversation.prompt.slice(0, 80) + "..."
              : data.conversation.prompt
          );
          return;
        }
        const { items, artifacts: restoredArtifacts } = rebuildItemsFromConversation(
          data.conversation.messages,
          data.conversation.prompt,
          data.conversation.final_response,
          data.turns,
          data.artifacts,
        );
        setInitialItems(items);
        setInitialArtifacts(restoredArtifacts);
        setInitialStatus(data.conversation.status);
        setConversationTitle(data.conversation.title || null);
        setProjectId(data.conversation.project_id || null);
        setPromptPreview(
          data.conversation.prompt.length > 80
            ? data.conversation.prompt.slice(0, 80) + "..."
            : data.conversation.prompt
        );
      } catch (e) {
        console.error("Failed to load conversation for continuation:", e);
        setError("Failed to load conversation");
      } finally {
        setLoading(false);
      }
    }
  }, [continueId, flowId, taskId]);

  // Derive the display title for the header
  const headerTitle = flowId
    ? "Edit Flow"
    : taskId
      ? "Edit Task"
      : activeConversationId
        ? (conversationTitle || promptPreview || "Chat")
        : promptParam
          ? "Action"
          : "New Chat";

  if (loading) {
    return (
      <div className="flex-1 min-h-0 flex flex-col -mb-3">
        <div className="px-3 lg:px-4 py-4 border-b border-border bg-card">
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">{flowId ? "Edit Flow" : taskId ? "Edit Task" : "Continue Chat"}</h1>
          <p className="text-[13px] text-muted-foreground">{flowId ? "Loading flow..." : taskId ? "Loading task..." : "Loading conversation..."}</p>
        </div>
        <div className="flex-1 flex items-center justify-center bg-muted/30">
          <div className="flex items-center gap-2 text-muted-foreground">
            <RiLoader4Line size={16} className="animate-spin text-brand-600" />
            {flowId ? "Loading flow..." : taskId ? "Loading task..." : "Loading conversation..."}
          </div>
        </div>
      </div>
    );
  }

  const showHeader = !!activeConversationId || (!promptParam && !autoSendParam);

  return (
    <div className="flex-1 min-h-0 flex flex-col -mb-3">
      {/* Mobile: no header bar — chat actions live in a floating menu on the
          right (mirrors the floating hamburger on the left). ChatContextMenu
          carries pin/rename/board/project/delete. */}
      {activeConversationId && user?.email === conversationOwner && (
        <div className="md:hidden fixed right-3 top-[max(0.5rem,env(safe-area-inset-top))] z-30 flex items-center gap-2">
          <CostChip conversationId={activeConversationId} />
          <ChatContextMenu
            conversationId={activeConversationId}
            conversationTitle={conversationTitle || promptPreview || "Untitled"}
            isPinned={isPinned(activeConversationId)}
            projectId={projectId}
            taskStatus={taskStatus}
            projects={projects}
            onRename={async (cid, newTitle) => {
              await renameConversation(cid, newTitle);
              setConversationTitle(newTitle);
            }}
            onDelete={async (cid) => {
              await removeConversation(cid);
              router.push(`${basePath}/tasks`);
            }}
            onTogglePin={togglePin}
            onAssignProject={async (cid, pid) => {
              await assignToProject(cid, pid);
              setProjectId(pid);
            }}
            onRemoveProject={async (cid) => {
              await unassignFromProject(cid);
              setProjectId(null);
            }}
            onCreateProject={async (name) => { await addProject(name); }}
            canShare={!!user?.email && user.email === conversationOwner}
            isShared={conversationShared}
            onSharingChange={setConversationShared}
            triggerClassName="h-9 w-9 flex items-center justify-center rounded-full border border-border bg-background/80 backdrop-blur text-muted-foreground press-scale"
          />
        </div>
      )}

      {/* Header with title and action buttons — desktop only; hidden for auto-sent prompts until conversation starts */}
      {showHeader && <div className="hidden md:block px-3 lg:px-4 py-3 border-b border-border bg-card flex-shrink-0">
        <div className="flex items-center gap-2">
          {/* Title area */}
          <div className="min-w-0 flex-1">
            {activeConversationId && editingTitle ? (
              <Input
                type="text"
                value={titleValue}
                onChange={(e) => setTitleValue(e.target.value)}
                onKeyDown={async (e) => {
                  if (e.key === "Enter" && titleValue.trim()) {
                    await renameConversation(activeConversationId, titleValue.trim());
                    setConversationTitle(titleValue.trim());
                    setEditingTitle(false);
                  }
                  if (e.key === "Escape") setEditingTitle(false);
                }}
                onBlur={async () => {
                  if (titleValue.trim() && titleValue.trim() !== (conversationTitle || promptPreview || "")) {
                    await renameConversation(activeConversationId, titleValue.trim());
                    setConversationTitle(titleValue.trim());
                  }
                  setEditingTitle(false);
                }}
                autoFocus
                maxLength={200}
                className="text-base font-semibold text-foreground w-full max-w-md"
              />
            ) : (
              <div className="flex items-center gap-2 min-w-0">
                <h1
                  className={cn(
                    "text-base font-heading font-semibold text-foreground truncate",
                    activeConversationId && user?.email === conversationOwner && "cursor-pointer hover:text-brand-600 transition-colors"
                  )}
                  onClick={() => {
                    if (activeConversationId && user?.email === conversationOwner) {
                      setTitleValue(conversationTitle || promptPreview || "");
                      setEditingTitle(true);
                    }
                  }}
                  title={activeConversationId && user?.email === conversationOwner ? "Click to rename" : undefined}
                >
                  {headerTitle}
                </h1>
                {activeConversationId && user?.email === conversationOwner && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => {
                          setTitleValue(conversationTitle || promptPreview || "");
                          setEditingTitle(true);
                        }}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <RiPencilLine size={14} />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Rename chat</TooltipContent>
                  </Tooltip>
                )}
              </div>
            )}
          </div>

          {/* Action buttons — visible once a conversation exists */}
          {activeConversationId && (
            <div className="flex items-center gap-1 flex-shrink-0">
              {/* Live chat cost */}
              <CostChip conversationId={activeConversationId} className="h-8 mr-1" />
              {/* Pin / Unpin */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => togglePin(activeConversationId)}
                    className={cn(
                      isPinned(activeConversationId)
                        ? "text-amber-500 hover:text-amber-600 hover:bg-amber-50"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted"
                    )}
                  >
                    {isPinned(activeConversationId) ? (
                      <RiPushpinFill size={16} />
                    ) : (
                      <RiPushpinLine size={16} />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{isPinned(activeConversationId) ? "Unpin chat" : "Pin chat"}</TooltipContent>
              </Tooltip>

              {/* More actions (project, etc.) */}
              {user?.email === conversationOwner && <ChatContextMenu
                conversationId={activeConversationId}
                conversationTitle={conversationTitle || promptPreview || "Untitled"}
                isPinned={isPinned(activeConversationId)}
                projectId={projectId}
                taskStatus={taskStatus}
                projects={projects}
                onRename={async (cid, newTitle) => {
                  await renameConversation(cid, newTitle);
                  setConversationTitle(newTitle);
                }}
                onDelete={async (cid) => {
                  await removeConversation(cid);
                  router.push(`${basePath}/tasks`);
                }}
                onTogglePin={togglePin}
                onAssignProject={async (cid, pid) => {
                  await assignToProject(cid, pid);
                  setProjectId(pid);
                }}
                onRemoveProject={async (cid) => {
                  await unassignFromProject(cid);
                  setProjectId(null);
                }}
                onCreateProject={async (name) => { await addProject(name); }}
                canShare={!!user?.email && user.email === conversationOwner}
                isShared={conversationShared}
                onSharingChange={setConversationShared}
                triggerClassName="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              />}

              {/* Delete */}
              {user?.email === conversationOwner && <div className="relative">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setShowDeleteConfirm(true)}
                      className="text-muted-foreground hover:text-red-500 hover:bg-red-50"
                    >
                      <RiDeleteBinLine size={16} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Delete chat</TooltipContent>
                </Tooltip>
                {showDeleteConfirm && (
                  <div className="absolute right-0 top-full mt-1 z-50 bg-card border border-border rounded-lg shadow-lg p-3 w-56 animate-fade-in">
                    <p className="text-[13px] font-medium text-foreground mb-1">Delete this chat?</p>
                    <p className="text-xs text-muted-foreground mb-2">This cannot be undone.</p>
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={async () => {
                          await removeConversation(activeConversationId);
                          router.push(`${basePath}/tasks`);
                        }}
                        className="flex-1 bg-red-600 hover:bg-red-700 text-white"
                      >
                        Delete
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setShowDeleteConfirm(false)}
                        className="flex-1"
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </div>}
            </div>
          )}
        </div>
      </div>}

      {error && (
        <div className="px-3 lg:px-4 py-2">
          <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
        </div>
      )}

      {/* Split pane content area — uses shared ChatWithArtifacts wrapper */}
      <ChatWithArtifacts
        initialItems={initialItems}
        initialArtifacts={initialArtifacts}
        conversationId={activeConversationId || undefined}

        initialPrompt={taskDraftPrompt || promptParam || undefined}
        initialFiles={taskDraftFiles || undefined}
        initialModel={taskModel || undefined}
        initialAgentId={continueId ? pinnedAgentId : undefined}
        autoSend={autoSendParam || (startParam && !!taskDraftPrompt)}
        systemContext={skillContextParam || undefined}
        initialStatus={initialStatus}
        onConversationCreated={handleConversationCreated}
        onStreamComplete={handleStreamComplete}
      />
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex-1 min-h-0 flex flex-col -mb-3">
          <div className="px-3 lg:px-4 py-4 border-b border-border bg-card">
            <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Chat</h1>
          </div>
          <div className="flex-1 bg-muted/30 flex items-center justify-center">
            <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
          </div>
        </div>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}
