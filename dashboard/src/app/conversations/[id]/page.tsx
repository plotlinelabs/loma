"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { fetchConversation, basePath } from "../../../lib/api";
import type { Conversation, Turn } from "../../../lib/api";
import ChatContextMenu from "../../../components/ChatContextMenu";
import ConfidenceBadge from "../../../components/ConfidenceBadge";
import TurnViewer from "../../../components/TurnViewer";
import MarkdownContent from "../../../components/MarkdownContent";
import ClientTimestamp from "../../../components/ClientTimestamp";
import { useConversationPolling } from "../../../hooks/useConversationPolling";
import { useUser } from "../../../lib/UserContext";

const sourceLabels: Record<string, string> = {
  slack_mention: "Slack Mention",
  slack_dm: "Slack DM",
  pylon_webhook: "Pylon",
  dashboard: "Dashboard",
  flow: "Flow",
  task_step: "Task",
};

const sourceStyles: Record<string, string> = {
  slack_mention: "bg-blue-50 text-blue-700",
  slack_dm: "bg-indigo-50 text-indigo-700",
  pylon_webhook: "bg-amber-50 text-amber-700",
  dashboard: "bg-brand-50 text-brand-700",
  flow: "bg-emerald-50 text-emerald-700",
  task_step: "bg-purple-50 text-purple-700",
};

export default function ConversationDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const router = useRouter();
  const { isPinned, togglePin, projects, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject } = useUser();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handlePollingData = useCallback((conv: Conversation, newTurns: Turn[]) => {
    setConversation(conv);
    setTurns(newTurns);
  }, []);

  const { isPolling, startPolling } = useConversationPolling({
    conversationId: id,
    onData: handlePollingData,
  });

  useEffect(() => {
    if (!id) return;
    loadConversation();
  }, [id]);

  async function loadConversation() {
    setLoading(true);
    try {
      const data = await fetchConversation(id);
      setConversation(data.conversation);
      setTurns(data.turns);

      // Always start polling — uses fast interval while running, slow
      // background interval when completed so follow-up queries are picked up.
      startPolling(data.conversation.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex items-center gap-2 text-gray-400">
          <svg className="animate-spin w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading conversation...
        </div>
      </div>
    );
  }

  if (error || !conversation) {
    return (
      <div className="text-red-600 text-center py-20 bg-red-50 rounded-xl border border-red-200">
        {error || "Conversation not found"}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Back link */}
      <a
        href={`${basePath}/`}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-brand-600 transition-colors font-medium"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
        </svg>
        Back to conversations
      </a>

      {/* Header card */}
      <div className="bg-surface border border-gray-200 rounded-xl p-6">
        <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${sourceStyles[conversation.source] || "bg-gray-100 text-gray-600"}`}>
                {sourceLabels[conversation.source] || conversation.source}
              </span>
              <span
                className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${
                  conversation.status === "completed"
                    ? "bg-gray-100 text-gray-600"
                    : conversation.status === "error"
                      ? "bg-red-50 text-red-700"
                      : "bg-blue-50 text-blue-700"
                }`}
              >
                {conversation.status}
              </span>
              <ConfidenceBadge confidence={conversation.confidence} />
              {/* Live polling indicator */}
              {isPolling && (
                <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-0.5 rounded-full font-medium bg-emerald-50 text-emerald-700">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                  </span>
                  Live updating
                </span>
              )}
            </div>
            {editingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  onKeyDown={async (e) => {
                    if (e.key === "Enter" && titleValue.trim()) {
                      await renameConversation(conversation.conversation_id, titleValue.trim());
                      setConversation({ ...conversation, title: titleValue.trim() });
                      setEditingTitle(false);
                    }
                    if (e.key === "Escape") setEditingTitle(false);
                  }}
                  autoFocus
                  maxLength={200}
                  className="text-lg font-semibold text-gray-900 leading-snug bg-gray-50 border border-gray-200 rounded-lg px-3 py-1 focus:outline-none focus:ring-2 focus:ring-accent-200 w-full"
                />
              </div>
            ) : (
              <h1
                className="text-lg font-semibold text-gray-900 leading-snug cursor-pointer hover:text-brand-600 transition-colors"
                onClick={() => {
                  setTitleValue(conversation.title || conversation.prompt?.slice(0, 120) || "");
                  setEditingTitle(true);
                }}
                title="Click to rename"
              >
                {conversation.title || conversation.prompt?.slice(0, 120)}
                {!conversation.title && conversation.prompt?.length > 120 ? "..." : ""}
              </h1>
            )}
          </div>
          <div className="flex items-center gap-2">
            <ChatContextMenu
              conversationId={conversation.conversation_id}
              conversationTitle={conversation.title || conversation.prompt?.slice(0, 50) || "Untitled"}
              isPinned={isPinned(conversation.conversation_id)}
              projectId={conversation.project_id}
              projects={projects}
              onRename={async (cid, newTitle) => {
                await renameConversation(cid, newTitle);
                setConversation({ ...conversation, title: newTitle });
              }}
              onDelete={async (cid) => {
                await removeConversation(cid);
                router.push("/");
              }}
              onTogglePin={togglePin}
              onAssignProject={async (cid, pid) => {
                await assignToProject(cid, pid);
                setConversation({ ...conversation, project_id: pid });
              }}
              onRemoveProject={async (cid) => {
                await unassignFromProject(cid);
                setConversation({ ...conversation, project_id: null });
              }}
              onCreateProject={async (name) => { await addProject(name); }}
              triggerClassName="p-1.5 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            />
            {conversation.status !== "running" && (
              <a
                href={`${basePath}/chat?continue=${conversation.conversation_id}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-brand-600 bg-brand-50 hover:bg-brand-100 rounded-lg transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 0 1 .865-.501 48.172 48.172 0 0 0 3.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
                </svg>
                Continue Chat
              </a>
            )}
            <a
              href={`${basePath}/`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              New Conversation
            </a>
          </div>
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-2 sm:grid-cols-6 gap-4 text-sm">
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Started</span>
            <div className="text-gray-700 mt-0.5">
              <ClientTimestamp iso={conversation.started_at} variant="full" />
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Duration</span>
            <div className="text-gray-700 mt-0.5">
              {conversation.duration_ms
                ? conversation.duration_ms > 60000
                  ? `${Math.round(conversation.duration_ms / 60000)}m ${Math.round((conversation.duration_ms % 60000) / 1000)}s`
                  : `${Math.round(conversation.duration_ms / 1000)}s`
                : "-"}
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Turns</span>
            <div className="text-gray-700 mt-0.5">{conversation.total_turns}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Model</span>
            <div className="text-gray-700 mt-0.5 truncate">{conversation.model || "-"}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Cost</span>
            <div className="text-gray-700 mt-0.5 tabular-nums">
              {conversation.cost?.total_cost_usd != null
                ? `$${conversation.cost.total_cost_usd.toFixed(4)}`
                : "-"}
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <span className="text-gray-400 text-xs font-medium">Account</span>
            <div className="text-gray-700 mt-0.5 truncate">{conversation.claude_account || "-"}</div>
          </div>
        </div>

        {/* Savings card */}
        {conversation.savings && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <span className="text-xs text-gray-400 font-medium">Savings Estimate</span>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm mt-2">
              <div className="bg-emerald-50 rounded-lg p-3">
                <span className="text-emerald-600 text-xs font-medium">Human Cost</span>
                <div className="text-emerald-800 mt-0.5 font-semibold tabular-nums">
                  ${conversation.savings.estimated_human_cost_usd.toFixed(2)}
                </div>
              </div>
              <div className="bg-emerald-50 rounded-lg p-3">
                <span className="text-emerald-600 text-xs font-medium">Savings</span>
                <div className="text-emerald-800 mt-0.5 font-semibold tabular-nums">
                  ${conversation.savings.savings_usd.toFixed(2)}
                </div>
              </div>
              <div className="bg-emerald-50 rounded-lg p-3">
                <span className="text-emerald-600 text-xs font-medium">Human Time</span>
                <div className="text-emerald-800 mt-0.5 font-semibold">
                  {conversation.savings.estimated_human_duration_minutes >= 60
                    ? `${Math.floor(conversation.savings.estimated_human_duration_minutes / 60)}h ${Math.round(conversation.savings.estimated_human_duration_minutes % 60)}m`
                    : `${Math.round(conversation.savings.estimated_human_duration_minutes)}m`}
                </div>
              </div>
              <div className="bg-emerald-50 rounded-lg p-3">
                <span className="text-emerald-600 text-xs font-medium">Expertise</span>
                <div className="text-emerald-800 mt-0.5 font-semibold text-xs">
                  {conversation.savings.expertise_category}
                </div>
              </div>
              <div className="bg-emerald-50 rounded-lg p-3">
                <span className="text-emerald-600 text-xs font-medium">Hourly Rate</span>
                <div className="text-emerald-800 mt-0.5 font-semibold tabular-nums">
                  ${conversation.savings.median_hourly_wage_usd}/hr
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Metadata tags */}
        {conversation.metadata &&
          Object.keys(conversation.metadata).length > 0 && (
            <div className="mt-4 pt-4 border-t border-gray-100">
              <span className="text-xs text-gray-400 font-medium">Metadata</span>
              <div className="flex flex-wrap gap-2 mt-2">
                {Object.entries(conversation.metadata).map(([k, v]) => (
                  <span
                    key={k}
                    className="text-xs bg-gray-50 text-gray-600 px-2.5 py-1 rounded-lg border border-gray-100"
                  >
                    <span className="text-gray-400">{k}:</span> {v}
                  </span>
                ))}
              </div>
            </div>
          )}

        {/* Confidence reasoning */}
        {conversation.confidence?.reasoning && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <span className="text-xs text-gray-400 font-medium">Confidence Reasoning</span>
            <p className="text-sm text-gray-600 mt-1 leading-relaxed">
              {conversation.confidence.reasoning}
            </p>
          </div>
        )}

        {/* Error */}
        {conversation.error && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <span className="text-xs text-red-500 font-medium">Error</span>
            <pre className="text-sm text-red-600 mt-1 bg-red-50 p-3 rounded-lg overflow-x-auto border border-red-100">
              {conversation.error}
            </pre>
          </div>
        )}
      </div>

      {/* Messages — chronological order (oldest first, like a chat) */}
      {conversation.messages?.length ? (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold text-gray-900">
            Messages ({conversation.messages.length})
          </h2>
          {[...conversation.messages]
            .sort((a, b) => {
              if (!a.timestamp || !b.timestamp) return 0;
              return a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
            })
            .map((msg, i) => (
            <div
              key={i}
              className={`rounded-xl p-5 border ${
                msg.role === "user"
                  ? "bg-blue-50 border-blue-200"
                  : "bg-surface border-gray-200"
              }`}
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={`text-xs font-semibold uppercase ${
                  msg.role === "user" ? "text-blue-600" : "text-gray-500"
                }`}>
                  {msg.role}
                </span>
                {msg.timestamp && (
                  <ClientTimestamp
                    iso={msg.timestamp}
                    variant="time"
                    className="text-xs text-gray-400"
                  />
                )}
              </div>
              {msg.role === "assistant" ? (
                <MarkdownContent
                  content={msg.content}
                  className="text-sm text-gray-700 leading-relaxed"
                />
              ) : (
                <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                  {msg.content}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* Fallback for old conversations without messages array */}
          <div className="bg-surface border border-gray-200 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">Full Prompt</h2>
            <pre className="text-sm text-gray-600 whitespace-pre-wrap leading-relaxed bg-gray-50 p-4 rounded-lg border border-gray-100">
              {conversation.prompt}
            </pre>
          </div>
          {conversation.final_response && (
            <div className="bg-surface border border-gray-200 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-gray-900 mb-3">
                Final Response
              </h2>
              <MarkdownContent
                content={conversation.final_response}
                className="text-sm text-gray-600 leading-relaxed"
              />
            </div>
          )}
        </>
      )}

      {/* Turns — reverse chronological (latest first for progress tracking) */}
      <div>
        <h2 className="text-sm font-semibold text-gray-900 mb-3">
          Agent Turns ({turns.length})
        </h2>
        <TurnViewer turns={turns} />
      </div>
    </div>
  );
}
