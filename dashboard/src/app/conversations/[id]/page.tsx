"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { fetchConversation, basePath } from "../../../lib/api";
import type { Conversation, Turn } from "../../../lib/api";
import {
  RiChat1Line,
  RiAddLine,
  RiLoader4Line,
  RiArrowDownSLine,
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

function formatDuration(ms?: number): string {
  if (!ms) return "-";
  if (ms > 60000) return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  return `${Math.round(ms / 1000)}s`;
}

function formatCost(cost?: { total_cost_usd?: number }): string {
  if (cost?.total_cost_usd != null) return `$${cost.total_cost_usd.toFixed(4)}`;
  return "-";
}

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
  const [detailsOpen, setDetailsOpen] = useState(false);

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
        <div className="flex items-center gap-2 text-muted-foreground text-[13px]">
          <RiLoader4Line size={14} className="animate-spin text-brand-600" />
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

  const metaItems = [
    { label: "Duration", value: formatDuration(conversation.duration_ms) },
    { label: "Turns", value: String(conversation.total_turns) },
    { label: "Model", value: conversation.model || "-" },
    { label: "Cost", value: formatCost(conversation.cost) },
  ];

  return (
    <div className="space-y-2">
      {/* Breadcrumb + actions — single compact row */}
      <div className="flex items-center justify-between gap-2">
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
        <div className="flex items-center gap-1 flex-shrink-0">
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
            <Button asChild size="sm" variant="secondary">
              <a href={`${basePath}/chat?continue=${conversation.conversation_id}`}>
                <RiChat1Line size={14} />
                Continue
              </a>
            </Button>
          )}
          <Button asChild size="sm" variant="outline">
            <a href={`${basePath}/`}>
              <RiAddLine size={14} />
              New
            </a>
          </Button>
        </div>
      </div>

      {/* Title + status row */}
      <div className="flex items-center gap-2">
        {editingTitle ? (
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
            className="text-base font-semibold text-foreground w-full max-w-md"
          />
        ) : (
          <h1
            className="text-base font-heading font-semibold text-foreground truncate cursor-pointer hover:text-brand-600 transition-colors"
            onClick={() => {
              setTitleValue(conversation.title || conversation.prompt?.slice(0, 120) || "");
              setEditingTitle(true);
            }}
            title="Click to rename"
          >
            {conversation.title || conversation.prompt?.slice(0, 120)}
          </h1>
        )}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <Badge variant="secondary" className={cn("text-[10px]", sourceStyles[conversation.source] || "bg-gray-100 text-gray-600")}>
            {sourceLabels[conversation.source] || conversation.source}
          </Badge>
          <Badge
            variant="secondary"
            className={cn(
              "text-[10px]",
              conversation.status === "completed" ? "bg-gray-100 text-gray-600"
                : conversation.status === "error" ? "bg-red-50 text-red-700"
                : "bg-blue-50 text-blue-700"
            )}
          >
            {conversation.status}
          </Badge>
          <ConfidenceBadge confidence={conversation.confidence} />
          {isPolling && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          )}
        </div>
      </div>

      {/* Inline metadata — single compact line */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <ClientTimestamp iso={conversation.started_at} variant="full" className="text-xs" />
        <span className="text-border">|</span>
        {metaItems.map((m) => (
          <span key={m.label}>{m.label}: <span className="text-foreground/70">{m.value}</span></span>
        ))}
        {(conversation.savings || (conversation.metadata && Object.keys(conversation.metadata).length > 0) || conversation.confidence?.reasoning) && (
          <>
            <span className="text-border">|</span>
            <button
              onClick={() => setDetailsOpen(!detailsOpen)}
              className="inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground transition-colors"
            >
              Details
              <RiArrowDownSLine size={14} className={cn("transition-transform", detailsOpen && "rotate-180")} />
            </button>
          </>
        )}
      </div>

      {/* Collapsible details */}
      {detailsOpen && (
        <div className="bg-card border border-border rounded-lg p-3 space-y-3 text-[13px]">
          {conversation.savings && (
            <div>
              <span className="text-xs text-muted-foreground font-medium">Savings</span>
              <div className="flex flex-wrap gap-4 mt-1 text-xs">
                <span>Human cost: <strong className="text-emerald-700">${conversation.savings.estimated_human_cost_usd.toFixed(2)}</strong></span>
                <span>Saved: <strong className="text-emerald-700">${conversation.savings.savings_usd.toFixed(2)}</strong></span>
                <span>Human time: <strong className="text-emerald-700">
                  {conversation.savings.estimated_human_duration_minutes >= 60
                    ? `${Math.floor(conversation.savings.estimated_human_duration_minutes / 60)}h ${Math.round(conversation.savings.estimated_human_duration_minutes % 60)}m`
                    : `${Math.round(conversation.savings.estimated_human_duration_minutes)}m`}
                </strong></span>
                <span>Expertise: <strong>{conversation.savings.expertise_category}</strong></span>
                <span>Rate: <strong>${conversation.savings.median_hourly_wage_usd}/hr</strong></span>
              </div>
            </div>
          )}
          {conversation.metadata && Object.keys(conversation.metadata).length > 0 && (
            <div>
              <span className="text-xs text-muted-foreground font-medium">Metadata</span>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {Object.entries(conversation.metadata).map(([k, v]) => (
                  <span key={k} className="text-xs bg-muted/50 text-muted-foreground px-2 py-0.5 rounded border border-border/50">
                    <span className="text-muted-foreground/60">{k}:</span> {v}
                  </span>
                ))}
              </div>
            </div>
          )}
          {conversation.confidence?.reasoning && (
            <div>
              <span className="text-xs text-muted-foreground font-medium">Confidence</span>
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{conversation.confidence.reasoning}</p>
            </div>
          )}
          {conversation.error && (
            <div>
              <span className="text-xs text-red-500 font-medium">Error</span>
              <pre className="text-xs text-red-600 mt-0.5 bg-red-50 p-2 rounded overflow-x-auto border border-red-100">{conversation.error}</pre>
            </div>
          )}
        </div>
      )}

      {/* Messages — the primary content */}
      {conversation.messages?.length ? (
        <div className="space-y-1.5">
          <h2 className="text-xs font-medium text-muted-foreground">
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
                "rounded-lg px-3 py-2",
                msg.role === "user"
                  ? "bg-blue-50/60"
                  : "bg-card border border-border"
              )}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className={cn(
                  "text-[10px] font-semibold uppercase",
                  msg.role === "user" ? "text-blue-600" : "text-muted-foreground"
                )}>
                  {msg.role}
                </span>
                {msg.timestamp && (
                  <ClientTimestamp iso={msg.timestamp} variant="time" className="text-[10px] text-muted-foreground" />
                )}
              </div>
              {msg.role === "assistant" ? (
                <MarkdownContent content={msg.content} className="text-[13px] text-foreground/80 leading-relaxed" />
              ) : (
                <div className="text-[13px] text-foreground/80 whitespace-pre-wrap leading-relaxed">{msg.content}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <>
          {conversation.prompt && (
            <div className="bg-card border border-border rounded-lg px-3 py-2">
              <h2 className="text-xs font-medium text-muted-foreground mb-1">Prompt</h2>
              <pre className="text-[13px] text-muted-foreground whitespace-pre-wrap leading-relaxed">{conversation.prompt}</pre>
            </div>
          )}
          {conversation.final_response && (
            <div className="bg-card border border-border rounded-lg px-3 py-2">
              <h2 className="text-xs font-medium text-muted-foreground mb-1">Response</h2>
              <MarkdownContent content={conversation.final_response} className="text-[13px] text-muted-foreground leading-relaxed" />
            </div>
          )}
        </>
      )}

      {/* Agent Turns — collapsed by default for long conversations */}
      {turns.length > 0 && (
        <div>
          <h2 className="text-xs font-medium text-muted-foreground mb-1">
            Agent Turns ({turns.length})
          </h2>
          <TurnViewer turns={turns} />
        </div>
      )}
    </div>
  );
}
