"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import {
  RiArrowDownSLine,
  RiCheckLine,
  RiPencilLine,
  RiLockLine,
  RiRefreshLine,
  RiFileCopyLine,
  RiLoader4Line,
  RiPriceTag3Line,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { statusColors } from "@/lib/status-colors";
import {
  fetchFlow,
  fetchFlowRuns,
  fetchLabels,
  updateFlowLabels,
  pauseFlow,
  resumeFlow,
  deleteFlow,
  triggerFlow,
  fetchWebhookLogs,
  updateFlow,
  fetchAgentModels,
} from "../../../lib/api";
import type { Flow, Conversation, WebhookLog, AgentModel } from "../../../lib/api";
import { basePath } from "../../../lib/api";
import ClientTimestamp from "../../../components/ClientTimestamp";

// Fixed palette of 10 colors for deterministic label coloring
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

const FAVORITE_MODEL_IDS = [
  "anthropic/claude-opus-4-8",
  "anthropic/claude-opus-4-7",
  "anthropic/claude-opus-4-6",
  "opencode-go/deepseek-v4-flash",
  "openai/gpt-5.5",
] as const;

function favoriteModelRank(model: AgentModel): number | null {
  const index = FAVORITE_MODEL_IDS.indexOf(model.id as typeof FAVORITE_MODEL_IDS[number]);
  return index === -1 ? null : index;
}

function isFavoriteModel(model: AgentModel): boolean {
  return favoriteModelRank(model) !== null;
}

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

function statusBadge(status: string) {
  return (
    <Badge variant="outline" className={statusColors[status] || statusColors.completed}>
      {status}
    </Badge>
  );
}

function modelShortLabel(model: AgentModel | undefined, modelId: string | null | undefined) {
  if (model) return model.label.split("·").pop()?.trim() || model.model_id;
  if (!modelId) return "Claude default";
  return modelId.split("/").pop() || modelId;
}

function runtimeLabel(modelId: string | null | undefined) {
  if (!modelId) return "Claude Agent SDK";
  const provider = modelId.split("/", 1)[0];
  if (provider === "anthropic") return "Claude Agent SDK";
  if (provider === "openai") return "OpenCode · OpenAI";
  if (provider === "opencode-go") return "OpenCode Go";
  return "OpenCode";
}

function FlowModelSelector({
  flow,
  models,
  loading,
  error,
  saving,
  onChange,
}: {
  flow: Flow;
  models: AgentModel[];
  loading: boolean;
  error: string | null;
  saving: boolean;
  onChange: (modelId: string) => void;
}) {
  const fallbackModel = flow.model || "";
  const effectiveModel = fallbackModel || models.find((model) => model.provider_id === "anthropic")?.id || "";
  const selected = models.find((model) => model.id === effectiveModel);
  const hasUnknownStoredModel = Boolean(fallbackModel && !selected);
  const recommendedModels = models
    .filter(isFavoriteModel)
    .sort((a, b) => (favoriteModelRank(a) ?? 99) - (favoriteModelRank(b) ?? 99));
  const remainingModels = models.filter((model) => !isFavoriteModel(model));

  // Build a flat list of items for the Select — radix select doesn't support optgroups natively
  // but we can use SelectGroup + SelectLabel
  return (
    <div className="bg-card rounded-xl border border-border p-3 space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-[13px] font-heading font-semibold text-foreground">Agent Model</h2>
        </div>
        <Badge variant="outline" className="gap-1.5">
          <span className={cn("h-1.5 w-1.5 rounded-full", effectiveModel.startsWith("anthropic/") ? "bg-violet-500" : "bg-emerald-500")} />
          {runtimeLabel(effectiveModel)}
        </Badge>
      </div>

      <Select
        value={effectiveModel}
        disabled={loading || saving || models.length === 0}
        onValueChange={onChange}
      >
        <SelectTrigger className="w-full">
          <SelectValue placeholder={loading ? "Loading models..." : "Select a model"} />
        </SelectTrigger>
        <SelectContent>
          {hasUnknownStoredModel && (
            <SelectItem value={fallbackModel}>{fallbackModel}</SelectItem>
          )}
          {!loading && models.length === 0 && !fallbackModel && (
            <SelectItem value="">Claude default</SelectItem>
          )}
          {recommendedModels.length > 0 && (
            <SelectGroup>
              <SelectLabel>Favorites</SelectLabel>
              {recommendedModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.label} ({runtimeLabel(model.id)})
                </SelectItem>
              ))}
            </SelectGroup>
          )}
          {remainingModels.length > 0 && (
            <SelectGroup>
              <SelectLabel>All models</SelectLabel>
              {remainingModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.label} ({runtimeLabel(model.id)})
                </SelectItem>
              ))}
            </SelectGroup>
          )}
        </SelectContent>
      </Select>

      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">
          Using <span className="font-medium text-foreground">{modelShortLabel(selected, effectiveModel)}</span>
        </span>
        {saving && <span className="text-brand-600">Saving...</span>}
      </div>
      {error && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-3 py-2 text-xs text-yellow-800">
          {error}
        </div>
      )}
    </div>
  );
}

function LabelSelector({
  flowLabels,
  allLabels,
  onUpdate,
}: {
  flowLabels: string[];
  allLabels: string[];
  onUpdate: (labels: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  function toggleLabel(label: string) {
    const current = new Set(flowLabels);
    if (current.has(label)) {
      current.delete(label);
    } else {
      current.add(label);
    }
    onUpdate(Array.from(current));
  }

  function handleCreateLabel(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = newLabel.trim();
    if (!trimmed) return;
    const current = new Set(flowLabels);
    current.add(trimmed);
    onUpdate(Array.from(current));
    setNewLabel("");
  }

  // Merge existing allLabels with flowLabels to get the complete set for display
  const combinedLabels = Array.from(new Set([...allLabels, ...flowLabels])).sort();

  return (
    <div className="relative" ref={dropdownRef}>
      <Button
        variant="outline"
        onClick={() => setOpen(!open)}
        className="gap-1.5"
      >
        <RiPriceTag3Line size={16} />
        Labels
        {flowLabels.length > 0 && (
          <Badge variant="secondary" className="text-xs ml-0.5">
            {flowLabels.length}
          </Badge>
        )}
        <RiArrowDownSLine size={14} className={cn("transition-transform", open && "rotate-180")} />
      </Button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-64 bg-card border border-border rounded-xl shadow-lg z-20 overflow-hidden">
          {/* Create new label */}
          <form onSubmit={handleCreateLabel} className="p-2 border-b border-border/50">
            <Input
              type="text"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="Create new label..."
              autoFocus
            />
          </form>

          {/* Existing labels */}
          <div className="max-h-48 overflow-y-auto p-1">
            {combinedLabels.length === 0 ? (
              <div className="px-3 py-2 text-xs text-muted-foreground text-center">
                No labels yet. Type above to create one.
              </div>
            ) : (
              combinedLabels.map((label) => {
                const isSelected = flowLabels.includes(label);
                const color = getLabelColor(label);
                return (
                  <Button
                    key={label}
                    variant="ghost"
                    onClick={() => toggleLabel(label)}
                    className="w-full flex items-center gap-2 px-2.5 py-1.5 h-auto justify-start"
                  >
                    <span
                      className={cn(
                        "w-4 h-4 rounded border flex items-center justify-center flex-shrink-0",
                        isSelected
                          ? `${color.bg} ${color.border}`
                          : "border-muted-foreground/30"
                      )}
                    >
                      {isSelected && (
                        <RiCheckLine size={12} className={color.text} />
                      )}
                    </span>
                    <Badge variant="outline" className={`${color.bg} ${color.text} ${color.border}`}>
                      {label}
                    </Badge>
                  </Button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function FlowDetailPage() {
  const params = useParams();
  const flowId = params.id as string;
  const [flow, setFlow] = useState<Flow | null>(null);
  const [runs, setRuns] = useState<Conversation[]>([]);
  const [webhookLogs, setWebhookLogs] = useState<WebhookLog[]>([]);
  const [allLabels, setAllLabels] = useState<string[]>([]);
  const [agentModels, setAgentModels] = useState<AgentModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelLoadError, setModelLoadError] = useState<string | null>(null);
  const [modelSaving, setModelSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshingLogs, setRefreshingLogs] = useState(false);
  const [refreshingRuns, setRefreshingRuns] = useState(false);
  const [webhookUrlCopied, setWebhookUrlCopied] = useState(false);

  useEffect(() => {
    loadData();
    loadAgentModels();
  }, [flowId]);

  async function loadAgentModels() {
    setModelsLoading(true);
    try {
      const catalog = await fetchAgentModels();
      setAgentModels(catalog.models || []);
      setModelLoadError(null);
    } catch (e) {
      console.warn("Failed to load agent models:", e);
      setAgentModels([]);
      setModelLoadError("Model list is unavailable. Existing flows will keep using their saved model or Claude default.");
    } finally {
      setModelsLoading(false);
    }
  }

  async function loadData() {
    setLoading(true);
    try {
      const [flowData, runsData] = await Promise.all([
        fetchFlow(flowId),
        fetchFlowRuns(flowId),
      ]);
      setFlow(flowData.flow);
      setRuns(runsData.runs);

      // Fetch labels separately so a failure doesn't break the page
      try {
        const labelsData = await fetchLabels();
        setAllLabels(labelsData.labels);
      } catch (labelErr) {
        console.warn("Failed to fetch labels (non-fatal):", labelErr);
        setAllLabels([]);
      }

      // Fetch webhook logs for webhook flows
      if (flowData.flow.trigger_type === "webhook") {
        try {
          const logsData = await fetchWebhookLogs(flowData.flow.flow_id);
          setWebhookLogs(logsData.logs);
        } catch (logErr) {
          console.warn("Failed to fetch webhook logs (non-fatal):", logErr);
          setWebhookLogs([]);
        }
      }
    } catch (e) {
      console.error("Failed to load flow:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleUpdateLabels(labels: string[]) {
    if (!flow) return;
    try {
      const result = await updateFlowLabels(flow.flow_id, labels);
      setFlow(result.flow);
      // Refresh the global labels list in case a new label was created
      try {
        const labelsData = await fetchLabels();
        setAllLabels(labelsData.labels);
      } catch {
        // Non-fatal — the label was still saved on the flow
      }
    } catch (e) {
      console.error("Failed to update labels:", e);
    }
  }

  async function handleModelChange(modelId: string) {
    if (!flow || modelSaving || modelId === flow.model) return;
    setModelSaving(true);
    const previous = flow;
    setFlow({ ...flow, model: modelId });
    try {
      const result = await updateFlow(flow.flow_id, { model: modelId });
      setFlow(result.flow);
      setModelLoadError(null);
    } catch (e) {
      console.error("Failed to update flow model:", e);
      setFlow(previous);
      setModelLoadError("Could not save the selected model. Please try again.");
    } finally {
      setModelSaving(false);
    }
  }

  async function handlePauseResume() {
    if (!flow) return;
    try {
      if (flow.status === "active") {
        await pauseFlow(flow.flow_id);
      } else {
        await resumeFlow(flow.flow_id);
      }
      await loadData();
    } catch (e) {
      console.error("Failed to update flow:", e);
    }
  }

  async function handleDelete() {
    if (!flow) return;
    if (!confirm(`Delete flow "${flow.name}"? This cannot be undone.`)) return;
    try {
      await deleteFlow(flow.flow_id);
      window.location.href = `${basePath}/flows`;
    } catch (e) {
      console.error("Failed to delete flow:", e);
    }
  }

  async function handleRunNow() {
    if (!flow) return;
    try {
      await triggerFlow(flow.flow_id);
      alert(`Flow "${flow.name}" triggered. Check the execution history for results.`);
    } catch (e) {
      console.error("Failed to trigger flow:", e);
    }
  }

  async function handleRefreshLogs() {
    if (!flow || refreshingLogs) return;
    setRefreshingLogs(true);
    try {
      const logsData = await fetchWebhookLogs(flow.flow_id);
      setWebhookLogs(logsData.logs);
    } catch (e) {
      console.warn("Failed to refresh webhook logs:", e);
    } finally {
      setRefreshingLogs(false);
    }
  }

  async function handleRefreshRuns() {
    if (refreshingRuns) return;
    setRefreshingRuns(true);
    try {
      const runsData = await fetchFlowRuns(flowId);
      setRuns(runsData.runs);
    } catch (e) {
      console.warn("Failed to refresh runs:", e);
    } finally {
      setRefreshingRuns(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex items-center gap-2 text-muted-foreground">
          <RiLoader4Line size={16} className="animate-spin text-brand-600" />
          Loading flow...
        </div>
      </div>
    );
  }

  if (!flow) {
    return (
      <div className="text-muted-foreground text-center py-20">Flow not found.</div>
    );
  }

  const isWebhook = flow.trigger_type === "webhook";
  const isSlack = flow.trigger_type === "slack";
  const isScheduled = (flow.trigger_type || "scheduled") === "scheduled";
  const webhookUrl = `${window.location.origin}/webhook?flowId=${flow.flow_id}`;

  return (
    <div className="space-y-2">
      {/* Breadcrumb */}
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href={`${basePath}/flows`}>Flows</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{flow.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-2xl font-heading font-semibold text-foreground">
              {flow.name}
            </h1>
            {statusBadge(flow.status)}
            {flow.visibility === "private" && (
              <Badge variant="outline" className="gap-1">
                <RiLockLine size={14} />
                Private
              </Badge>
            )}
          </div>
          {flow.description && (
            <p className="text-[13px] text-muted-foreground mt-1">{flow.description}</p>
          )}
          {/* Label pills */}
          {(flow.labels || []).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {flow.labels.map((label) => {
                const color = getLabelColor(label);
                return (
                  <Badge
                    key={label}
                    variant="outline"
                    className={`${color.bg} ${color.text} ${color.border}`}
                  >
                    {label}
                  </Badge>
                );
              })}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <LabelSelector
            flowLabels={flow.labels || []}
            allLabels={allLabels}
            onUpdate={handleUpdateLabels}
          />
          <Button
            variant="outline"
            onClick={async () => {
              const newVis = flow.visibility === "private" ? "shared" : "private";
              try {
                const result = await updateFlow(flow.flow_id, { visibility: newVis });
                setFlow(result.flow);
              } catch (e) {
                console.error("Failed to update visibility:", e);
              }
            }}
            title={flow.visibility === "private" ? "Make shared (visible to all)" : "Make private (only you and admins)"}
          >
            <RiLockLine size={16} />
            {flow.visibility === "private" ? "Private" : "Shared"}
          </Button>
          <Button asChild className="bg-accent-200 hover:bg-accent-300 text-accent-on">
            <a href={`${basePath}/chat?flow=${flow.flow_id}`}>
              <RiPencilLine size={16} />
              Edit in Chat
            </a>
          </Button>
          {flow.status !== "completed" && (
            <Button
              variant="outline"
              onClick={handlePauseResume}
            >
              {flow.status === "active" ? "Pause" : "Resume"}
            </Button>
          )}
          {flow.status === "active" && isScheduled && (
            <Button
              variant="outline"
              onClick={handleRunNow}
            >
              Run Now
            </Button>
          )}
          <Button
            variant="outline"
            onClick={handleDelete}
            className="border-red-200 text-red-600 hover:bg-red-50"
          >
            Delete
          </Button>
        </div>
      </div>

      {/* Webhook URL */}
      {isWebhook && (
        <div className="bg-card rounded-xl border border-border p-3 space-y-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">Webhook URL</h2>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-[13px] bg-muted/50 rounded-lg px-3 py-2 text-foreground/80 break-all">
              {webhookUrl}
            </code>
            <Button
              variant="outline"
              onClick={() => {
                navigator.clipboard.writeText(webhookUrl);
                setWebhookUrlCopied(true);
                setTimeout(() => setWebhookUrlCopied(false), 2000);
              }}
            >
              {webhookUrlCopied ? (
                <>
                  <RiCheckLine size={16} className="text-green-600" />
                  Copied
                </>
              ) : (
                <>
                  <RiFileCopyLine size={16} />
                  Copy
                </>
              )}
            </Button>
          </div>
          <div className="text-[13px] text-muted-foreground">
            Auth: <span className="font-medium text-foreground">{flow.webhook_config?.auth_method || "none"}</span>
          </div>
        </div>
      )}

      <FlowModelSelector
        flow={flow}
        models={agentModels}
        loading={modelsLoading}
        error={modelLoadError}
        saving={modelSaving}
        onChange={handleModelChange}
      />

      {/* Flow info grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        {/* Schedule & Channel */}
        <div className="bg-card rounded-xl border border-border p-3 space-y-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">Details</h2>
          <div className="space-y-2 text-[13px]">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Trigger</span>
              <span className="text-foreground font-medium">
                {isWebhook ? "Webhook" : isSlack ? "Slack" : "Scheduled"}
              </span>
            </div>
            {isWebhook ? (
              <>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Auth method</span>
                  <span className="text-foreground">{flow.webhook_config?.auth_method || "none"}</span>
                </div>
                {flow.webhook_config?.auth_method === "hmac_sha256" && flow.webhook_config?.signature_header && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Signature header</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded text-foreground/80">
                      {flow.webhook_config.signature_header}
                    </code>
                  </div>
                )}
              </>
            ) : isSlack ? (
              <>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Channel</span>
                  <span className="text-foreground">{flow.channel_name || flow.channel_id}</span>
                </div>
                {flow.slack_config?.allow_bot_messages && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Bot messages</span>
                    <span className="text-foreground">Enabled</span>
                  </div>
                )}
                <p className="text-xs text-muted-foreground pt-1">
                  Responds to new top-level messages in this channel and replies in-thread.
                  @mention the bot to reply inside a thread.
                </p>
              </>
            ) : (
              <>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Schedule</span>
                  <span className="text-foreground font-medium">
                    {flow.frequency || flow.cron || "One-time"}
                  </span>
                </div>
                {flow.cron && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Cron</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded text-foreground/80">
                      {flow.cron}
                    </code>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Timezone</span>
                  <span className="text-foreground">{flow.timezone}</span>
                </div>
              </>
            )}
            <div className="flex justify-between">
              <span className="text-muted-foreground">Created</span>
              <ClientTimestamp iso={flow.created_at} variant="full" className="text-foreground" />
            </div>
            {flow.created_by?.user_name && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Created by</span>
                <span className="text-foreground">{flow.created_by.user_name}</span>
              </div>
            )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Visibility</span>
                <span className="text-foreground capitalize">{flow.visibility || "shared"}</span>
              </div>
          </div>
        </div>

        {/* Run stats */}
        <div className="bg-card rounded-xl border border-border p-3 space-y-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">Run Stats</h2>
          <div className="space-y-2 text-[13px]">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total runs</span>
              <span className="text-foreground font-medium">{flow.run_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Last run</span>
              <ClientTimestamp iso={flow.last_run_at} variant="full" className="text-foreground" />
            </div>
            {isScheduled && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Next run</span>
                {flow.status === "active" ? (
                  <ClientTimestamp iso={flow.next_run_at} variant="full" className="text-brand-600 font-medium" />
                ) : (
                  <span className="text-foreground">{"—"}</span>
                )}
              </div>
            )}
            {flow.last_error && (
              <div>
                <span className="text-muted-foreground block mb-1">Last error</span>
                <div className="text-xs text-red-600 bg-red-50 rounded px-2 py-1">
                  {flow.last_error}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Prompt */}
      <div className="bg-card rounded-xl border border-border p-3">
        <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">
          {isWebhook ? "Prompt Template" : "Agent Prompt"}
        </h2>
        <pre className="text-[13px] text-foreground/80 whitespace-pre-wrap bg-muted/50 rounded-lg p-3 max-h-96 overflow-y-auto">
          {isWebhook ? (flow.prompt_template || flow.prompt) : flow.prompt}
        </pre>
      </div>

      {/* Webhook Logs (webhook flows only) */}
      {isWebhook && (
        <div className="bg-card rounded-xl border border-border p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[13px] font-heading font-semibold text-foreground">Webhook Logs</h2>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={handleRefreshLogs}
                disabled={refreshingLogs}
                title="Refresh logs"
              >
                <RiRefreshLine size={14} className={cn(refreshingLogs && "animate-spin")} />
              </Button>
              <a
                href={`${basePath}/webhook-logs?flowId=${flow.flow_id}`}
                className="text-xs text-brand-600 hover:text-brand-700 transition-colors"
              >
                View all
              </a>
            </div>
          </div>
          {webhookLogs.length === 0 ? (
            <div className="text-[13px] text-muted-foreground py-4 text-center">
              No webhook requests received yet.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Received</TableHead>
                  <TableHead>Auth</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Conversation</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {webhookLogs.slice(0, 20).map((log) => (
                  <TableRow key={log.log_id}>
                    <TableCell>
                      <ClientTimestamp iso={log.received_at} variant="full" className="text-foreground/80" />
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="secondary"
                        className={cn(
                          "text-xs",
                          log.auth_result === "success"
                            ? "bg-green-50 text-green-700"
                            : log.auth_result === "failed"
                              ? "bg-red-50 text-red-700"
                              : "bg-muted text-muted-foreground"
                        )}
                      >
                        {log.auth_result}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="secondary"
                        className={cn(
                          "text-xs",
                          log.execution_status === "completed"
                            ? "bg-green-50 text-green-700"
                            : log.execution_status === "error"
                              ? "bg-red-50 text-red-700"
                              : log.execution_status === "running"
                                ? "bg-blue-50 text-blue-700"
                                : "bg-muted text-muted-foreground"
                        )}
                      >
                        {log.execution_status}
                      </Badge>
                      {log.error && (
                        <span className="ml-1 text-xs text-red-500" title={log.error}>
                          {log.error.slice(0, 40)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      {log.conversation_id ? (
                        <a
                          href={`${basePath}/conversations/${log.conversation_id}`}
                          className="text-brand-600 hover:text-brand-700 text-xs underline"
                        >
                          View
                        </a>
                      ) : (
                        <span className="text-muted-foreground text-xs">{"—"}</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {/* Execution history */}
      <div className="bg-card rounded-xl border border-border p-3">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">
            Execution History
          </h2>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={handleRefreshRuns}
            disabled={refreshingRuns}
            title="Refresh runs"
          >
            <RiRefreshLine size={14} className={cn(refreshingRuns && "animate-spin")} />
          </Button>
        </div>
        {runs.length === 0 ? (
          <div className="text-[13px] text-muted-foreground py-4 text-center">
            No executions yet.
          </div>
        ) : (
          <div className="space-y-2">
            {runs.map((run) => (
              <a
                key={run.conversation_id}
                href={`${basePath}/conversations/${run.conversation_id}`}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-muted/50 transition-colors border border-border/50"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "w-2 h-2 rounded-full flex-shrink-0",
                      run.status === "completed"
                        ? "bg-green-400"
                        : run.status === "error"
                          ? "bg-red-400"
                          : "bg-blue-400"
                    )}
                  />
                  <ClientTimestamp iso={run.started_at} variant="full" className="text-[13px] text-foreground/80" />
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{run.status}</span>
                  {run.duration_ms && (
                    <span>{(run.duration_ms / 1000).toFixed(1)}s</span>
                  )}
                  {run.cost?.total_cost_usd != null && (
                    <span>${run.cost.total_cost_usd.toFixed(4)}</span>
                  )}
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
