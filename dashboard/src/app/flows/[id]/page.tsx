"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
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
  const styles: Record<string, string> = {
    active: "bg-green-50 text-green-700 border-green-200",
    paused: "bg-yellow-50 text-yellow-700 border-yellow-200",
    completed: "bg-gray-50 text-gray-500 border-gray-200",
  };
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium border ${styles[status] || styles.completed}`}
    >
      {status}
    </span>
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

  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-5 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Agent Model</h2>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-600">
          <span className={`h-1.5 w-1.5 rounded-full ${effectiveModel.startsWith("anthropic/") ? "bg-violet-500" : "bg-emerald-500"}`} />
          {runtimeLabel(effectiveModel)}
        </span>
      </div>

      <label className="block">
        <span className="sr-only">Flow model</span>
        <select
          value={effectiveModel}
          disabled={loading || saving || models.length === 0}
          onChange={(event) => onChange(event.target.value)}
          className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-900 shadow-sm transition-colors focus:border-accent-200 focus:outline-none focus:ring-2 focus:ring-accent-100 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400"
        >
          {loading && <option value={effectiveModel}>Loading models...</option>}
          {!loading && models.length === 0 && !fallbackModel && (
            <option value="">Claude default</option>
          )}
          {hasUnknownStoredModel && (
            <option value={fallbackModel}>{fallbackModel}</option>
          )}
          {recommendedModels.length > 0 && (
            <optgroup label="Favorites">
              {recommendedModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} ({runtimeLabel(model.id)})
                </option>
              ))}
            </optgroup>
          )}
          {remainingModels.length > 0 && (
            <optgroup label="All models">
              {remainingModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} ({runtimeLabel(model.id)})
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>

      <div className="flex items-center justify-between gap-3 text-xs text-gray-500">
        <span className="truncate">
          Using <span className="font-medium text-gray-700">{modelShortLabel(selected, effectiveModel)}</span>
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
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <svg
          className="w-4 h-4"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M9.568 3H5.25A2.25 2.25 0 0 0 3 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 0 0 5.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 0 0 9.568 3Z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M6 6h.008v.008H6V6Z"
          />
        </svg>
        Labels
        {flowLabels.length > 0 && (
          <span className="bg-gray-100 text-gray-600 text-xs px-1.5 py-0.5 rounded-full">
            {flowLabels.length}
          </span>
        )}
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-64 bg-surface border border-gray-200 rounded-xl shadow-lg z-20 overflow-hidden">
          {/* Create new label */}
          <form onSubmit={handleCreateLabel} className="p-2 border-b border-gray-100">
            <input
              type="text"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="Create new label..."
              className="w-full px-2.5 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
              autoFocus
            />
          </form>

          {/* Existing labels */}
          <div className="max-h-48 overflow-y-auto p-1">
            {combinedLabels.length === 0 ? (
              <div className="px-3 py-2 text-xs text-gray-400 text-center">
                No labels yet. Type above to create one.
              </div>
            ) : (
              combinedLabels.map((label) => {
                const isSelected = flowLabels.includes(label);
                const color = getLabelColor(label);
                return (
                  <button
                    key={label}
                    onClick={() => toggleLabel(label)}
                    className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm rounded-lg hover:bg-gray-50 transition-colors"
                  >
                    <span
                      className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                        isSelected
                          ? `${color.bg} ${color.border}`
                          : "border-gray-300"
                      }`}
                    >
                      {isSelected && (
                        <svg
                          className={`w-3 h-3 ${color.text}`}
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={3}
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="m4.5 12.75 6 6 9-13.5"
                          />
                        </svg>
                      )}
                    </span>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${color.bg} ${color.text} ${color.border}`}
                    >
                      {label}
                    </span>
                  </button>
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
        <div className="flex items-center gap-2 text-gray-400">
          <svg
            className="animate-spin w-4 h-4 text-brand-600"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          Loading flow...
        </div>
      </div>
    );
  }

  if (!flow) {
    return (
      <div className="text-gray-400 text-center py-20">Flow not found.</div>
    );
  }

  const isWebhook = flow.trigger_type === "webhook";
  const isSlack = flow.trigger_type === "slack";
  const isScheduled = (flow.trigger_type || "scheduled") === "scheduled";
  const webhookUrl = `${window.location.origin}/webhook?flowId=${flow.flow_id}`;

  return (
    <div className="space-y-6">
      {/* Back link */}
      <a
        href={`${basePath}/flows`}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
      >
        <svg
          className="w-4 h-4"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15.75 19.5 8.25 12l7.5-7.5"
          />
        </svg>
        Back to Flows
      </a>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-2xl font-semibold text-gray-900">
              {flow.name}
            </h1>
            {statusBadge(flow.status)}
            {flow.visibility === "private" && (
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-gray-100 text-gray-600 border border-gray-200">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                </svg>
                Private
              </span>
            )}
          </div>
          {flow.description && (
            <p className="text-sm text-gray-500 mt-1">{flow.description}</p>
          )}
          {/* Label pills */}
          {(flow.labels || []).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {flow.labels.map((label) => {
                const color = getLabelColor(label);
                return (
                  <span
                    key={label}
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${color.bg} ${color.text} ${color.border}`}
                  >
                    {label}
                  </span>
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
          <button
            onClick={async () => {
              const newVis = flow.visibility === "private" ? "shared" : "private";
              try {
                const result = await updateFlow(flow.flow_id, { visibility: newVis });
                setFlow(result.flow);
              } catch (e) {
                console.error("Failed to update visibility:", e);
              }
            }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors"
            title={flow.visibility === "private" ? "Make shared (visible to all)" : "Make private (only you and admins)"}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
            </svg>
            {flow.visibility === "private" ? "Private" : "Shared"}
          </button>
          <a
            href={`${basePath}/chat?flow=${flow.flow_id}`}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-accent-200 text-accent-on text-sm font-medium rounded-lg hover:bg-accent-300 transition-colors"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
              />
            </svg>
            Edit in Chat
          </a>
          {flow.status !== "completed" && (
            <button
              onClick={handlePauseResume}
              className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors"
            >
              {flow.status === "active" ? "Pause" : "Resume"}
            </button>
          )}
          {flow.status === "active" && isScheduled && (
            <button
              onClick={handleRunNow}
              className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Run Now
            </button>
          )}
          <button
            onClick={handleDelete}
            className="px-3 py-1.5 text-sm font-medium rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Webhook URL */}
      {isWebhook && (
        <div className="bg-surface rounded-xl border border-gray-200 p-5 space-y-3">
          <h2 className="text-sm font-semibold text-gray-900">Webhook URL</h2>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-sm bg-gray-50 rounded-lg px-3 py-2 text-gray-700 break-all">
              {webhookUrl}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(webhookUrl);
                setWebhookUrlCopied(true);
                setTimeout(() => setWebhookUrlCopied(false), 2000);
              }}
              className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 transition-colors"
            >
              {webhookUrlCopied ? (
                <>
                  <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                  Copied
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 0 0-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 0 1-1.125-1.125v-9.25m12 6.625v-1.875a3.375 3.375 0 0 0-3.375-3.375h-1.5a1.125 1.125 0 0 1-1.125-1.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H9.75" />
                  </svg>
                  Copy
                </>
              )}
            </button>
          </div>
          <div className="text-sm text-gray-500">
            Auth: <span className="font-medium text-gray-700">{flow.webhook_config?.auth_method || "none"}</span>
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Schedule & Channel */}
        <div className="bg-surface rounded-xl border border-gray-200 p-5 space-y-4">
          <h2 className="text-sm font-semibold text-gray-900">Details</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Trigger</span>
              <span className="text-gray-900 font-medium">
                {isWebhook ? "Webhook" : isSlack ? "Slack" : "Scheduled"}
              </span>
            </div>
            {isWebhook ? (
              <>
                <div className="flex justify-between">
                  <span className="text-gray-500">Auth method</span>
                  <span className="text-gray-900">{flow.webhook_config?.auth_method || "none"}</span>
                </div>
                {flow.webhook_config?.auth_method === "hmac_sha256" && flow.webhook_config?.signature_header && (
                  <div className="flex justify-between">
                    <span className="text-gray-500">Signature header</span>
                    <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-700">
                      {flow.webhook_config.signature_header}
                    </code>
                  </div>
                )}
              </>
            ) : isSlack ? (
              <>
                <div className="flex justify-between">
                  <span className="text-gray-500">Channel</span>
                  <span className="text-gray-900">{flow.channel_name || flow.channel_id}</span>
                </div>
                {flow.slack_config?.allow_bot_messages && (
                  <div className="flex justify-between">
                    <span className="text-gray-500">Bot messages</span>
                    <span className="text-gray-900">Enabled</span>
                  </div>
                )}
                <p className="text-xs text-gray-400 pt-1">
                  Responds to new top-level messages in this channel and replies in-thread.
                  @mention the bot to reply inside a thread.
                </p>
              </>
            ) : (
              <>
                <div className="flex justify-between">
                  <span className="text-gray-500">Schedule</span>
                  <span className="text-gray-900 font-medium">
                    {flow.frequency || flow.cron || "One-time"}
                  </span>
                </div>
                {flow.cron && (
                  <div className="flex justify-between">
                    <span className="text-gray-500">Cron</span>
                    <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-700">
                      {flow.cron}
                    </code>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-gray-500">Timezone</span>
                  <span className="text-gray-900">{flow.timezone}</span>
                </div>
              </>
            )}
            <div className="flex justify-between">
              <span className="text-gray-500">Created</span>
              <ClientTimestamp iso={flow.created_at} variant="full" className="text-gray-900" />
            </div>
            {flow.created_by?.user_name && (
              <div className="flex justify-between">
                <span className="text-gray-500">Created by</span>
                <span className="text-gray-900">{flow.created_by.user_name}</span>
              </div>
            )}
              <div className="flex justify-between">
                <span className="text-gray-500">Visibility</span>
                <span className="text-gray-900 capitalize">{flow.visibility || "shared"}</span>
              </div>
          </div>
        </div>

        {/* Run stats */}
        <div className="bg-surface rounded-xl border border-gray-200 p-5 space-y-4">
          <h2 className="text-sm font-semibold text-gray-900">Run Stats</h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Total runs</span>
              <span className="text-gray-900 font-medium">{flow.run_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Last run</span>
              <ClientTimestamp iso={flow.last_run_at} variant="full" className="text-gray-900" />
            </div>
            {isScheduled && (
              <div className="flex justify-between">
                <span className="text-gray-500">Next run</span>
                {flow.status === "active" ? (
                  <ClientTimestamp iso={flow.next_run_at} variant="full" className="text-brand-600 font-medium" />
                ) : (
                  <span className="text-gray-900">{"\u2014"}</span>
                )}
              </div>
            )}
            {flow.last_error && (
              <div>
                <span className="text-gray-500 block mb-1">Last error</span>
                <div className="text-xs text-red-600 bg-red-50 rounded px-2 py-1">
                  {flow.last_error}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Prompt */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-900 mb-3">
          {isWebhook ? "Prompt Template" : "Agent Prompt"}
        </h2>
        <pre className="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-lg p-4 max-h-96 overflow-y-auto">
          {isWebhook ? (flow.prompt_template || flow.prompt) : flow.prompt}
        </pre>
      </div>

      {/* Webhook Logs (webhook flows only) */}
      {isWebhook && (
        <div className="bg-surface rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-900">Webhook Logs</h2>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRefreshLogs}
                disabled={refreshingLogs}
                className="p-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors disabled:opacity-50"
                title="Refresh logs"
              >
                <svg className={`w-3.5 h-3.5 ${refreshingLogs ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M21.015 4.356v4.992" />
                </svg>
              </button>
              <a
                href={`${basePath}/webhook-logs?flowId=${flow.flow_id}`}
                className="text-xs text-brand-600 hover:text-brand-700 transition-colors"
              >
                View all
              </a>
            </div>
          </div>
          {webhookLogs.length === 0 ? (
            <div className="text-sm text-gray-400 py-4 text-center">
              No webhook requests received yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-100">
                    <th className="pb-2 font-medium">Received</th>
                    <th className="pb-2 font-medium">Auth</th>
                    <th className="pb-2 font-medium">Status</th>
                    <th className="pb-2 font-medium">Conversation</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {webhookLogs.slice(0, 20).map((log) => (
                    <tr key={log.log_id} className="hover:bg-gray-50">
                      <td className="py-2 pr-3">
                        <ClientTimestamp iso={log.received_at} variant="full" className="text-gray-700" />
                      </td>
                      <td className="py-2 pr-3">
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${
                            log.auth_result === "success"
                              ? "bg-green-50 text-green-700"
                              : log.auth_result === "failed"
                                ? "bg-red-50 text-red-700"
                                : "bg-gray-50 text-gray-500"
                          }`}
                        >
                          {log.auth_result}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${
                            log.execution_status === "completed"
                              ? "bg-green-50 text-green-700"
                              : log.execution_status === "error"
                                ? "bg-red-50 text-red-700"
                                : log.execution_status === "running"
                                  ? "bg-blue-50 text-blue-700"
                                  : "bg-gray-50 text-gray-500"
                          }`}
                        >
                          {log.execution_status}
                        </span>
                        {log.error && (
                          <span className="ml-1 text-xs text-red-500" title={log.error}>
                            {log.error.slice(0, 40)}
                          </span>
                        )}
                      </td>
                      <td className="py-2">
                        {log.conversation_id ? (
                          <a
                            href={`${basePath}/conversations/${log.conversation_id}`}
                            className="text-brand-600 hover:text-brand-700 text-xs underline"
                          >
                            View
                          </a>
                        ) : (
                          <span className="text-gray-400 text-xs">{"\u2014"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Execution history */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-900">
            Execution History
          </h2>
          <button
            onClick={handleRefreshRuns}
            disabled={refreshingRuns}
            className="p-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors disabled:opacity-50"
            title="Refresh runs"
          >
            <svg className={`w-3.5 h-3.5 ${refreshingRuns ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M21.015 4.356v4.992" />
            </svg>
          </button>
        </div>
        {runs.length === 0 ? (
          <div className="text-sm text-gray-400 py-4 text-center">
            No executions yet.
          </div>
        ) : (
          <div className="space-y-2">
            {runs.map((run) => (
              <a
                key={run.conversation_id}
                href={`${basePath}/conversations/${run.conversation_id}`}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 transition-colors border border-gray-100"
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      run.status === "completed"
                        ? "bg-green-400"
                        : run.status === "error"
                          ? "bg-red-400"
                          : "bg-blue-400"
                    }`}
                  />
                  <ClientTimestamp iso={run.started_at} variant="full" className="text-sm text-gray-700" />
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500">
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
