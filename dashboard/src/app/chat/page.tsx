"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useState, useCallback, Suspense } from "react";
import type { ChatItem } from "../../components/ChatPanel";
import type { Artifact } from "../../components/ArtifactViewer";
import { rebuildItemsFromConversation } from "../../components/ChatPanel";
import ChatContextMenu from "../../components/ChatContextMenu";
import ChatWithArtifacts from "../../components/ChatWithArtifacts";
import { fetchConversation, fetchFlow, basePath } from "../../lib/api";
import { useUser } from "../../lib/UserContext";

// ── Chat Page Content ───────────────────────────────────────────────────────

function ChatPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { isPinned, togglePin, projects, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject } = useUser();
  const continueId = searchParams.get("continue");
  const flowId = searchParams.get("flow");
  const taskId = null; // Tasks system removed
  const promptParam = searchParams.get("prompt");
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
          `- **Channel:** ${flow.channel_name || flow.channel_id}`,
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
      } finally {
        setLoading(false);
      }
    }

    async function loadConversation() {
      try {
        const data = await fetchConversation(continueId!);
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
      <div className="h-[calc(100vh-3rem)] flex flex-col -mx-6 lg:-mx-8 -my-6">
        <div className="px-6 lg:px-8 py-4 border-b border-gray-200 bg-surface">
          <h1 className="text-lg font-semibold text-gray-900">{flowId ? "Edit Flow" : taskId ? "Edit Task" : "Continue Chat"}</h1>
          <p className="text-sm text-gray-500">{flowId ? "Loading flow..." : taskId ? "Loading task..." : "Loading conversation..."}</p>
        </div>
        <div className="flex-1 flex items-center justify-center bg-gray-50">
          <div className="flex items-center gap-2 text-gray-400">
            <svg className="animate-spin w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            {flowId ? "Loading flow..." : taskId ? "Loading task..." : "Loading conversation..."}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col -mx-6 lg:-mx-8 -my-6">
      {/* Header with title and action buttons */}
      <div className="px-6 lg:px-8 py-3 border-b border-gray-200 bg-surface flex-shrink-0">
        <div className="flex items-center gap-3">
          {/* Title area */}
          <div className="min-w-0 flex-1">
            {activeConversationId && editingTitle ? (
              <input
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
                className="text-base font-semibold text-gray-900 bg-gray-50 border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-300 w-full max-w-md"
              />
            ) : (
              <div className="flex items-center gap-2 min-w-0">
                <h1
                  className={`text-base font-semibold text-gray-900 truncate ${activeConversationId ? "cursor-pointer hover:text-brand-600 transition-colors" : ""}`}
                  onClick={() => {
                    if (activeConversationId) {
                      setTitleValue(conversationTitle || promptPreview || "");
                      setEditingTitle(true);
                    }
                  }}
                  title={activeConversationId ? "Click to rename" : undefined}
                >
                  {headerTitle}
                </h1>
                {activeConversationId && (
                  <button
                    onClick={() => {
                      setTitleValue(conversationTitle || promptPreview || "");
                      setEditingTitle(true);
                    }}
                    className="flex-shrink-0 p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                    title="Rename chat"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125" />
                    </svg>
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Action buttons — visible once a conversation exists */}
          {activeConversationId && (
            <div className="flex items-center gap-1 flex-shrink-0">
              {/* Pin / Unpin */}
              <button
                onClick={() => togglePin(activeConversationId)}
                className={`p-2 rounded-lg transition-colors ${
                  isPinned(activeConversationId)
                    ? "text-amber-500 hover:text-amber-600 hover:bg-amber-50"
                    : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                }`}
                title={isPinned(activeConversationId) ? "Unpin chat" : "Pin chat"}
              >
                {isPinned(activeConversationId) ? (
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2z" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 4h6v5l2.5 2.5H13v5.5l-1 2-1-2V11.5H6.5L9 9V4z" />
                  </svg>
                )}
              </button>

              {/* More actions (project, etc.) */}
              <ChatContextMenu
                conversationId={activeConversationId}
                conversationTitle={conversationTitle || promptPreview || "Untitled"}
                isPinned={isPinned(activeConversationId)}
                projectId={projectId}
                projects={projects}
                onRename={async (cid, newTitle) => {
                  await renameConversation(cid, newTitle);
                  setConversationTitle(newTitle);
                }}
                onDelete={async (cid) => {
                  await removeConversation(cid);
                  router.push(`${basePath}/`);
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
                triggerClassName="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              />

              {/* Delete */}
              <div className="relative">
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="p-2 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                  title="Delete chat"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                  </svg>
                </button>
                {showDeleteConfirm && (
                  <div className="absolute right-0 top-full mt-1 z-50 bg-surface border border-gray-200 rounded-lg shadow-lg p-3 w-56 animate-fade-in">
                    <p className="text-sm font-medium text-gray-900 mb-1">Delete this chat?</p>
                    <p className="text-xs text-gray-500 mb-3">This cannot be undone.</p>
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={async () => {
                          await removeConversation(activeConversationId);
                          router.push(`${basePath}/`);
                        }}
                        className="flex-1 px-2.5 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors"
                      >
                        Delete
                      </button>
                      <button
                        onClick={() => setShowDeleteConfirm(false)}
                        className="flex-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Split pane content area — uses shared ChatWithArtifacts wrapper */}
      <ChatWithArtifacts
        initialItems={initialItems}
        initialArtifacts={initialArtifacts}
        conversationId={activeConversationId || undefined}

        initialPrompt={promptParam || undefined}
        initialStatus={initialStatus}
        onConversationCreated={handleConversationCreated}
      />
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="h-[calc(100vh-3rem)] flex flex-col -mx-6 lg:-mx-8 -my-6">
          <div className="px-6 lg:px-8 py-4 border-b border-gray-200 bg-surface">
            <h1 className="text-lg font-semibold text-gray-900">Chat</h1>
            <p className="text-sm text-gray-500">Loading...</p>
          </div>
          <div className="flex-1 bg-gray-50" />
        </div>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}
