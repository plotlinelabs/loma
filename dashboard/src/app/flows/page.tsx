"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { fetchFlows, fetchLabels, pauseFlow, resumeFlow, deleteFlow, triggerFlow } from "../../lib/api";
import type { Flow } from "../../lib/api";
import { basePath } from "../../lib/api";
import ClientTimestamp from "../../components/ClientTimestamp";
import { cn } from "@/lib/utils";
import { statusColors } from "@/lib/status-colors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import {
  RiAddLine,
  RiCheckLine,
  RiPauseLine,
  RiPlayLine,
  RiPlayCircleLine,
  RiPencilLine,
  RiDeleteBinLine,
  RiTimeLine,
  RiLinksLine,
  RiHashtag,
  RiLockLine,
} from "@remixicon/react";
import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";

type FlowTab = "all" | "drafts" | "disabled";

const STATUS_FILTERS = ["all", "active", "paused", "completed"] as const;
const TRIGGER_TYPE_FILTERS = ["all", "scheduled", "webhook", "slack"] as const;

const LABEL_COLORS = [
  { bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200" },
  { bg: "bg-purple-50", text: "text-purple-700", border: "border-purple-200" },
  { bg: "bg-green-50", text: "text-green-700", border: "border-green-200" },
  { bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200" },
  { bg: "bg-pink-50", text: "text-pink-700", border: "border-pink-200" },
  { bg: "bg-teal-50", text: "text-teal-700", border: "border-teal-200" },
  { bg: "bg-red-50", text: "text-red-700", border: "border-red-200" },
  { bg: "bg-cyan-50", text: "text-cyan-700", border: "border-cyan-200" },
  { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200" },
  { bg: "bg-indigo-50", text: "text-indigo-700", border: "border-indigo-200" },
];

function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return Math.abs(hash);
}

function getLabelColor(label: string) {
  const index = hashString(label) % LABEL_COLORS.length;
  return LABEL_COLORS[index];
}

function LabelPill({ label, small }: { label: string; small?: boolean }) {
  const color = getLabelColor(label);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-medium border",
        small ? "px-1.5 py-0 text-[10px]" : "px-2 py-0.5 text-xs",
        color.bg, color.text, color.border
      )}
    >
      {label}
    </span>
  );
}

function statusBadge(status: string) {
  return (
    <Badge variant="outline" className={cn("rounded-md", statusColors[status] || statusColors.completed)}>
      {status}
    </Badge>
  );
}

function flowModelLabel(model: string | null | undefined) {
  if (!model) return "Claude default";
  const [provider, modelId] = model.split("/", 2);
  if (provider === "anthropic") return `Claude · ${modelId || model}`;
  if (provider === "openai") return `OpenAI · ${modelId || model}`;
  if (provider === "opencode-go") return `OpenCode Go · ${modelId || model}`;
  return model;
}

function SkeletonCard() {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5 mb-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-5 w-14 rounded-md" />
          </div>
          <div className="flex gap-4 mt-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-28" />
          </div>
        </div>
        <div className="flex gap-1.5">
          <Skeleton className="h-7 w-7 rounded-lg" />
          <Skeleton className="h-7 w-7 rounded-lg" />
          <Skeleton className="h-7 w-7 rounded-lg" />
        </div>
      </div>
    </Card>
  );
}

export default function FlowsPage() {
  const { data: session } = useSession();
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");
  const [allLabels, setAllLabels] = useState<string[]>([]);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"mine" | "all">("all");
  const [triggerTypeFilter, setTriggerTypeFilter] = useState<string>("all");
  const [activeTab, setActiveTab] = useState<FlowTab>("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { loadFlows(); loadLabels(); }, []);

  async function loadFlows() {
    setLoading(true);
    setError(null);
    try { const data = await fetchFlows(); setFlows(data.flows); }
    catch (e) { console.error("Failed to load flows:", e); setError("Failed to load flows"); }
    finally { setLoading(false); }
  }

  async function loadLabels() {
    try { const data = await fetchLabels(); setAllLabels(data.labels); }
    catch (e) { console.error("Failed to load labels:", e); }
  }

  async function handlePauseResume(flow: Flow) {
    const newStatus = flow.status === "active" ? "paused" : "active";
    setFlows((prev) => prev.map((f) => (f.flow_id === flow.flow_id ? { ...f, status: newStatus } : f)));
    try {
      if (flow.status === "active") await pauseFlow(flow.flow_id);
      else if (flow.status === "paused") await resumeFlow(flow.flow_id);
      await loadFlows();
    } catch (e) {
      console.error("Failed to update flow:", e);
      setFlows((prev) => prev.map((f) => (f.flow_id === flow.flow_id ? { ...f, status: flow.status } : f)));
    }
  }

  async function handleDelete(flow: Flow) {
    if (!confirm(`Delete flow "${flow.name}"? This cannot be undone.`)) return;
    setFlows((prev) => prev.filter((f) => f.flow_id !== flow.flow_id));
    try { await deleteFlow(flow.flow_id); }
    catch (e) { console.error("Failed to delete flow:", e); await loadFlows(); }
  }

  async function handleRunNow(flow: Flow) {
    try { await triggerFlow(flow.flow_id); alert(`Flow "${flow.name}" triggered. Check the execution history for results.`); }
    catch (e) { console.error("Failed to trigger flow:", e); }
  }

  function toggleLabel(label: string) { setSelectedLabel((prev) => (prev === label ? null : label)); }

  // Apply status and label filters
  let filtered = filter === "all" ? flows : flows.filter((f) => f.status === filter);
  if (selectedLabel) filtered = filtered.filter((f) => (f.labels || []).includes(selectedLabel));

  // Apply trigger type filter (client-side)
  if (triggerTypeFilter !== "all") {
    filtered = filtered.filter((f) => {
      const flowTriggerType = f.trigger_type || "scheduled";
      return flowTriggerType === triggerTypeFilter;
    });
  }

  // Apply view mode filter (client-side)
  if (viewMode === "mine" && session?.user?.email) {
    const userEmail = session.user.email.toLowerCase();
    const userName = session.user.name?.toLowerCase() || "";
    filtered = filtered.filter((f) => {
      const createdByName = (f.created_by?.user_name || "").toLowerCase();
      const createdBySource = (f.created_by?.source || "").toLowerCase();
      return (
        createdByName === userEmail ||
        createdByName === userName ||
        createdBySource === userEmail
      );
    });
  }

  if (loading) {
    return (
      <div className="space-y-3 animate-fade-in-up">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-heading font-semibold text-foreground">Flows</h1>
            <p className="text-sm text-muted-foreground mt-1">Pre-defined steps that run on a schedule</p>
          </div>
        </div>
        <div className="space-y-3"><SkeletonCard /><SkeletonCard /><SkeletonCard /></div>
      </div>
    );
  }

  return (
    <div className="space-y-3 md:space-y-4 animate-fade-in-up">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl md:text-2xl font-heading font-semibold text-foreground">Flows</h1>
          <p className="text-sm text-muted-foreground mt-1">Pre-defined steps that run on a schedule</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center bg-card border border-border rounded-lg p-0.5">
            <Button
              variant={viewMode === "all" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setViewMode("all")}
              className={cn(
                viewMode === "all" && "bg-accent-200 text-accent-on"
              )}
            >
              All
            </Button>
            <Button
              variant={viewMode === "mine" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setViewMode("mine")}
              className={cn(
                viewMode === "mine" && "bg-accent-200 text-accent-on"
              )}
            >
              My Flows
            </Button>
          </div>
          <Button asChild className="bg-accent-200 text-accent-on hover:bg-accent-300 press-scale">
            <Link href="/chat">
              <RiAddLine size={16} />
              New Flow
            </Link>
          </Button>
        </div>
      </div>

      {/* Tab bar -- All flows / Drafts / Disabled */}
      <div className="flex items-center gap-6 border-b border-border">
        <FlowTabButton
          label="All flows"
          count={flows.length}
          active={activeTab === "all"}
          onClick={() => setActiveTab("all")}
        />
        <FlowTabButton
          label="Drafts"
          count={0}
          active={activeTab === "drafts"}
          onClick={() => setActiveTab("drafts")}
        />
        <FlowTabButton
          label="Disabled"
          count={flows.filter((f) => f.status === "paused").length}
          active={activeTab === "disabled"}
          onClick={() => setActiveTab("disabled")}
        />
      </div>

      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

      {activeTab === "drafts" ? (
        <EmptyState icon={RiTimeLine} title="No drafts yet" description="Draft flows will appear here" />
      ) : (
        <>
      <div className="flex gap-2 flex-wrap">
        {STATUS_FILTERS.map((s) => (
          <Button key={s} onClick={() => setFilter(s)}
            variant={filter === s ? "secondary" : "outline"}
            size="sm"
            className={cn(
              "capitalize press-scale",
              filter === s && "bg-brand-100 text-brand-700"
            )}>
            {s}{s !== "all" && <span className="ml-1.5 text-xs opacity-60">{flows.filter((f) => f.status === s).length}</span>}
          </Button>
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        {TRIGGER_TYPE_FILTERS.map((t) => (
          <Button key={t} onClick={() => setTriggerTypeFilter(t)}
            variant={triggerTypeFilter === t ? "secondary" : "outline"}
            size="sm"
            className={cn(
              "capitalize press-scale",
              triggerTypeFilter === t && "bg-brand-100 text-brand-700"
            )}>
            {t}{t !== "all" && <span className="ml-1.5 text-xs opacity-60">{flows.filter((f) => (f.trigger_type || "scheduled") === t).length}</span>}
          </Button>
        ))}
      </div>

      {allLabels.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {allLabels.map((label) => {
            const isSelected = selectedLabel === label;
            const color = getLabelColor(label);
            return (
              <Button key={label} onClick={() => toggleLabel(label)}
                variant="outline"
                size="sm"
                className={cn(
                  "rounded-full press-scale",
                  isSelected
                    ? `${color.bg} ${color.text} ${color.border} ring-2 ring-offset-1`
                    : "text-muted-foreground"
                )}>
                {isSelected && <RiCheckLine size={12} className="mr-1" />}
                {label}
              </Button>
            );
          })}
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState
          icon={RiTimeLine}
          title={filter === "all" && !selectedLabel && viewMode === "all" ? "No flows yet" : viewMode === "mine" ? "No flows created by you" : selectedLabel ? `No flows match the label "${selectedLabel}"` : `No ${filter} flows`}
          description={filter === "all" && !selectedLabel && viewMode === "all" ? "Create one via the chat" : undefined}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 stagger-children">
          {filtered.map((flow) => (
            <Card key={flow.flow_id} className="p-3 hover-lift">
              <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3 md:gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 md:gap-2.5 mb-1 flex-wrap">
                    <Link href={`/flows/${flow.flow_id}`} className="text-sm md:text-base font-semibold text-foreground hover:text-brand-700 transition-colors truncate">
                      {flow.name}
                    </Link>
                    {statusBadge(flow.status)}
                    {flow.visibility === "private" && (
                      <Badge variant="outline" className="rounded-md bg-gray-100 text-gray-600 border-gray-200">
                        <RiLockLine size={12} />
                        Private
                      </Badge>
                    )}
                    {(flow.labels || []).map((label) => <LabelPill key={label} label={label} small />)}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 md:gap-x-4 gap-y-1 text-xs md:text-sm text-muted-foreground mt-1">
                    {flow.trigger_type === "webhook" ? (
                      <Badge variant="outline" className="rounded-md bg-violet-50 text-violet-700 border-violet-200">
                        <RiLinksLine size={14} />
                        Webhook{flow.webhook_config?.auth_method ? ` · ${flow.webhook_config.auth_method}` : ""}
                      </Badge>
                    ) : flow.trigger_type === "slack" ? (
                      <Badge variant="outline" className="rounded-md bg-emerald-50 text-emerald-700 border-emerald-200">
                        <RiHashtag size={14} />
                        Slack{flow.channel_name ? ` · ${flow.channel_name}` : (flow.channel_id ? ` · ${flow.channel_id}` : "")}
                      </Badge>
                    ) : (
                      <>
                        <span className="flex items-center gap-1">
                          <RiTimeLine size={14} />
                          {flow.frequency || flow.cron || "One-time"}
                        </span>
                      </>
                    )}
                    {flow.run_count > 0 && <span>Ran {flow.run_count} time{flow.run_count !== 1 ? "s" : ""}</span>}
                    <span>{flowModelLabel(flow.model)}</span>
                    {flow.last_run_at && <span>Last: <ClientTimestamp iso={flow.last_run_at} variant="short" /></span>}
                    {flow.next_run_at && flow.status === "active" && flow.trigger_type !== "webhook" && <span className="text-brand-600">Next: <ClientTimestamp iso={flow.next_run_at} variant="short" /></span>}
                  </div>
                  {flow.last_error && <div className="mt-2 text-xs text-red-600 bg-red-50 rounded px-2 py-1 inline-block">{flow.last_error}</div>}
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0 self-end md:self-auto">
                  {flow.status !== "completed" && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handlePauseResume(flow)}
                          className="text-muted-foreground hover:text-foreground press-scale"
                        >
                          {flow.status === "active" ? (
                            <RiPauseLine size={16} />
                          ) : (
                            <RiPlayLine size={16} />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{flow.status === "active" ? "Pause" : "Resume"}</TooltipContent>
                    </Tooltip>
                  )}
                  {flow.status === "active" && (flow.trigger_type || "scheduled") === "scheduled" && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleRunNow(flow)}
                          className="text-muted-foreground hover:text-brand-700 hover:bg-brand-50 press-scale"
                        >
                          <RiPlayCircleLine size={16} />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Run now</TooltipContent>
                    </Tooltip>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        asChild
                        className="text-muted-foreground hover:text-blue-700 hover:bg-blue-50 press-scale"
                      >
                        <Link href={`/chat?flow=${flow.flow_id}`}>
                          <RiPencilLine size={16} />
                        </Link>
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Edit in chat</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => handleDelete(flow)}
                        className="text-muted-foreground hover:text-red-600 hover:bg-red-50 press-scale"
                      >
                        <RiDeleteBinLine size={16} />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Delete</TooltipContent>
                  </Tooltip>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
        </>
      )}
    </div>
  );
}

function FlowTabButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className="relative pb-3 pt-1 flex items-center gap-1.5 transition-colors h-auto px-1 rounded-none"
      style={{
        color: active ? "#1F1D1A" : "#8C857D",
        borderBottom: active ? "2px solid #1F1D1A" : "2px solid transparent",
        marginBottom: "-1px",
        fontFamily: "var(--font-sans), system-ui, sans-serif",
        fontSize: 14,
        fontWeight: active ? 600 : 500,
      }}
    >
      <span>{label}</span>
      <span
        className="inline-flex items-center justify-center rounded-full px-1.5 py-0.5 min-w-[18px]"
        style={{
          background: "#F0EDE6",
          border: "none",
          fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
          fontSize: 10,
          color: "#5C5650",
          fontWeight: 600,
        }}
      >
        {count}
      </span>
    </Button>
  );
}
