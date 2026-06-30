"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { fetchConversation, basePath } from "../../../lib/api";
import type { Conversation, Turn } from "../../../lib/api";
import {
  RiChat1Line,
  RiAddLine,
  RiLoader4Line,
} from "@remixicon/react";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
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
        <div className="flex items-center gap-2 text-muted-foreground">
          <RiLoader4Line size={16} className="animate-spin text-brand-600" />
          Loading conversation...
        </div>
      </div>
    );
  }

  if (error || !conversation) {
    return (
      <Alert variant="destructive" className="text-center py-20 rounded-xl">
        <AlertDescription>{error || "Conversation not found"}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-2">
      {/* Breadcrumb */}
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href={`${basePath}/`}>Conversations</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{conversation.title || "Conversation"}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Header card */}
      <div className="bg-card border border-border rounded-xl p-3">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="secondary" className={sourceStyles[conversation.source] || "bg-gray-100 text-gray-600"}>
                {sourceLabels[conversation.source] || conversation.source}
              </Badge>
              <Badge
                variant="secondary"
                className={cn(
                  conversation.status === "completed"
                    ? "bg-gray-100 text-gray-600"
                    : conversation.status === "error"
                      ? "bg-red-50 text-red-700"
                      : "bg-blue-50 text-blue-700"
                )}
              >
                {conversation.status}
              </Badge>
              <ConfidenceBadge confidence={conversation.confidence} />
              {/* Live polling indicator */}
              {isPolling && (
                <Badge variant="secondary" className="bg-emerald-50 text-emerald-700 gap-1.5">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                  </span>
                  Live updating
                </Badge>
              )}
            </div>
            {editingTitle ? (
              <div className="flex items-center gap-2">
                <Input
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
                  className="text-lg font-semibold text-foreground leading-snug w-full"
                />
              </div>
            ) : (
              <h1
                className="text-lg font-heading font-semibold text-foreground leading-snug cursor-pointer hover:text-brand-600 transition-colors"
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
              triggerClassName="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            />
            {conversation.status !== "running" && (
              <Button asChild variant="secondary" className="text-brand-600 bg-brand-50 hover:bg-brand-100">
                <a href={`${basePath}/chat?continue=${conversation.conversation_id}`}>
                  <RiChat1Line size={16} />
                  Continue Chat
                </a>
              </Button>
            )}
            <Button asChild variant="secondary">
              <a href={`${basePath}/`}>
                <RiAddLine size={16} />
                New Conversation
              </a>
            </Button>
          </div>
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-2 sm:grid-cols-6 gap-3 text-[13px]">
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Started</span>
            <div className="text-foreground/80 mt-0.5">
              <ClientTimestamp iso={conversation.started_at} variant="full" />
            </div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Duration</span>
            <div className="text-foreground/80 mt-0.5">
              {conversation.duration_ms
                ? conversation.duration_ms > 60000
                  ? `${Math.round(conversation.duration_ms / 60000)}m ${Math.round((conversation.duration_ms % 60000) / 1000)}s`
                  : `${Math.round(conversation.duration_ms / 1000)}s`
                : "-"}
            </div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Turns</span>
            <div className="text-foreground/80 mt-0.5">{conversation.total_turns}</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Model</span>
            <div className="text-foreground/80 mt-0.5 truncate">{conversation.model || "-"}</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Cost</span>
            <div className="text-foreground/80 mt-0.5 tabular-nums">
              {conversation.cost?.total_cost_usd != null
                ? `$${conversation.cost.total_cost_usd.toFixed(4)}`
                : "-"}
            </div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3">
            <span className="text-muted-foreground text-xs font-medium">Account</span>
            <div className="text-foreground/80 mt-0.5 truncate">{conversation.claude_account || "-"}</div>
          </div>
        </div>

        {/* Savings card */}
        {conversation.savings && (
          <div className="mt-2 pt-4 border-t border-border/50">
            <span className="text-xs text-muted-foreground font-medium">Savings Estimate</span>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-[13px] mt-2">
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
            <div className="mt-2 pt-4 border-t border-border/50">
              <span className="text-xs text-muted-foreground font-medium">Metadata</span>
              <div className="flex flex-wrap gap-2 mt-2">
                {Object.entries(conversation.metadata).map(([k, v]) => (
                  <span
                    key={k}
                    className="text-xs bg-muted/50 text-muted-foreground px-2.5 py-1 rounded-lg border border-border/50"
                  >
                    <span className="text-muted-foreground/60">{k}:</span> {v}
                  </span>
                ))}
              </div>
            </div>
          )}

        {/* Confidence reasoning */}
        {conversation.confidence?.reasoning && (
          <div className="mt-2 pt-4 border-t border-border/50">
            <span className="text-xs text-muted-foreground font-medium">Confidence Reasoning</span>
            <p className="text-[13px] text-muted-foreground mt-1 leading-relaxed">
              {conversation.confidence.reasoning}
            </p>
          </div>
        )}

        {/* Error */}
        {conversation.error && (
          <div className="mt-2 pt-4 border-t border-border/50">
            <span className="text-xs text-red-500 font-medium">Error</span>
            <pre className="text-[13px] text-red-600 mt-1 bg-red-50 p-3 rounded-lg overflow-x-auto border border-red-100">
              {conversation.error}
            </pre>
          </div>
        )}
      </div>

      {/* Messages — chronological order (oldest first, like a chat) */}
      {conversation.messages?.length ? (
        <div className="space-y-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">
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
              className={cn(
                "rounded-xl p-3 border",
                msg.role === "user"
                  ? "bg-blue-50 border-blue-200"
                  : "bg-card border-border"
              )}
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={cn(
                  "text-xs font-semibold uppercase",
                  msg.role === "user" ? "text-blue-600" : "text-muted-foreground"
                )}>
                  {msg.role}
                </span>
                {msg.timestamp && (
                  <ClientTimestamp
                    iso={msg.timestamp}
                    variant="time"
                    className="text-xs text-muted-foreground"
                  />
                )}
              </div>
              {msg.role === "assistant" ? (
                <MarkdownContent
                  content={msg.content}
                  className="text-[13px] text-foreground/80 leading-relaxed"
                />
              ) : (
                <div className="text-[13px] text-foreground/80 whitespace-pre-wrap leading-relaxed">
                  {msg.content}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* Fallback for old conversations without messages array */}
          <div className="bg-card border border-border rounded-xl p-3">
            <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">Full Prompt</h2>
            <pre className="text-[13px] text-muted-foreground whitespace-pre-wrap leading-relaxed bg-muted/50 p-3 rounded-lg border border-border/50">
              {conversation.prompt}
            </pre>
          </div>
          {conversation.final_response && (
            <div className="bg-card border border-border rounded-xl p-3">
              <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">
                Final Response
              </h2>
              <MarkdownContent
                content={conversation.final_response}
                className="text-[13px] text-muted-foreground leading-relaxed"
              />
            </div>
          )}
        </>
      )}

      {/* Turns — reverse chronological (latest first for progress tracking) */}
      <div>
        <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">
          Agent Turns ({turns.length})
        </h2>
        <TurnViewer turns={turns} />
      </div>
    </div>
  );
}
