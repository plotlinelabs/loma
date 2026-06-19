"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { fetchFlows, fetchLabels, pauseFlow, resumeFlow, deleteFlow, triggerFlow } from "../../lib/api";
import type { Flow } from "../../lib/api";
import { basePath } from "../../lib/api";
import ClientTimestamp from "../../components/ClientTimestamp";

type FlowTab = "all" | "drafts" | "disabled";

const STATUS_FILTERS = ["all", "active", "paused", "completed"] as const;
const TRIGGER_TYPE_FILTERS = ["all", "scheduled", "webhook"] as const;

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
      className={`inline-flex items-center rounded-full font-medium border ${
        small ? "px-1.5 py-0 text-[10px]" : "px-2 py-0.5 text-xs"
      } ${color.bg} ${color.text} ${color.border}`}
    >
      {label}
    </span>
  );
}

function statusBadge(status: string) {
  const styles: Record<string, string> = {
    active: "bg-green-50 text-green-700 border-green-200",
    paused: "bg-yellow-50 text-yellow-700 border-yellow-200",
    completed: "bg-gray-50 text-gray-500 border-gray-200",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${styles[status] || styles.completed}`}>
      {status}
    </span>
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
    <div className="bg-surface rounded-xl border border-gray-200 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5 mb-2">
            <div className="skeleton h-4 w-48" />
            <div className="skeleton h-5 w-14 rounded-md" />
          </div>
          <div className="flex gap-4 mt-2">
            <div className="skeleton h-3 w-24" />
            <div className="skeleton h-3 w-20" />
            <div className="skeleton h-3 w-28" />
          </div>
        </div>
        <div className="flex gap-1.5">
          <div className="skeleton h-7 w-7 rounded-lg" />
          <div className="skeleton h-7 w-7 rounded-lg" />
          <div className="skeleton h-7 w-7 rounded-lg" />
        </div>
      </div>
    </div>
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

  useEffect(() => { loadFlows(); loadLabels(); }, []);

  async function loadFlows() {
    setLoading(true);
    try { const data = await fetchFlows(); setFlows(data.flows); }
    catch (e) { console.error("Failed to load flows:", e); }
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
    try { await triggerFlow(flow.flow_id); alert(`Flow "${flow.name}" triggered. Check the target channel for results.`); }
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
      <div className="space-y-6 animate-fade-in-up">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Flows</h1>
            <p className="text-sm text-gray-500 mt-1">Pre-defined steps that run on a schedule</p>
          </div>
        </div>
        <div className="space-y-3"><SkeletonCard /><SkeletonCard /><SkeletonCard /></div>
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Flows</h1>
          <p className="text-sm text-gray-500 mt-1">Pre-defined steps that run on a schedule</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center bg-surface border border-gray-200 rounded-lg p-0.5">
            <button
              onClick={() => setViewMode("all")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                viewMode === "all"
                  ? "bg-accent-200 text-accent-on"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setViewMode("mine")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                viewMode === "mine"
                  ? "bg-accent-200 text-accent-on"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              My Flows
            </button>
          </div>
          <Link href="/chat" className="inline-flex items-center gap-2 px-4 py-2 bg-accent-200 text-accent-on text-sm font-medium rounded-lg hover:bg-accent-300 transition-colors press-scale">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            New Flow
          </Link>
        </div>
      </div>

      {/* Tab bar — All flows / Drafts / Disabled */}
      <div className="flex items-center gap-6 border-b border-gray-200">
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

      {activeTab === "drafts" ? (
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <div className="text-gray-400 text-sm">No drafts yet.</div>
        </div>
      ) : (
        <>
      <div className="flex gap-2 flex-wrap">
        {STATUS_FILTERS.map((s) => (
          <button key={s} onClick={() => setFilter(s)}
            className={`px-3 py-1.5 text-sm rounded-lg font-medium capitalize transition-colors press-scale ${
              filter === s ? "bg-brand-100 text-brand-700" : "bg-surface border border-gray-200 text-gray-600 hover:bg-gray-50"
            }`}>
            {s}{s !== "all" && <span className="ml-1.5 text-xs opacity-60">{flows.filter((f) => f.status === s).length}</span>}
          </button>
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        {TRIGGER_TYPE_FILTERS.map((t) => (
          <button key={t} onClick={() => setTriggerTypeFilter(t)}
            className={`px-3 py-1.5 text-sm rounded-lg font-medium capitalize transition-colors press-scale ${
              triggerTypeFilter === t ? "bg-brand-100 text-brand-700" : "bg-surface border border-gray-200 text-gray-600 hover:bg-gray-50"
            }`}>
            {t}{t !== "all" && <span className="ml-1.5 text-xs opacity-60">{flows.filter((f) => (f.trigger_type || "scheduled") === t).length}</span>}
          </button>
        ))}
      </div>

      {allLabels.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {allLabels.map((label) => {
            const isSelected = selectedLabel === label;
            const color = getLabelColor(label);
            return (
              <button key={label} onClick={() => toggleLabel(label)}
                className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border transition-colors press-scale ${
                  isSelected ? `${color.bg} ${color.text} ${color.border} ring-2 ring-offset-1 ring-${color.text.replace("text-", "").replace("-700", "-300")}` : "bg-surface border-gray-200 text-gray-500 hover:bg-gray-50"
                }`}>
                {isSelected && <svg className="w-3 h-3 mr-1" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>}
                {label}
              </button>
            );
          })}
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <div className="text-gray-400 text-sm">
            {filter === "all" && !selectedLabel && viewMode === "all" ? "No flows yet. Create one via the chat." : viewMode === "mine" ? "No flows created by you." : selectedLabel ? `No flows match the label \u201c${selectedLabel}\u201d.` : `No ${filter} flows.`}
          </div>
        </div>
      ) : (
        <div className="space-y-3 stagger-children">
          {filtered.map((flow) => (
            <div key={flow.flow_id} className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 hover-lift">
              <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3 md:gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 md:gap-2.5 mb-1 flex-wrap">
                    <Link href={`/flows/${flow.flow_id}`} className="text-sm md:text-base font-semibold text-gray-900 hover:text-brand-700 transition-colors truncate">
                      {flow.name}
                    </Link>
                    {statusBadge(flow.status)}
                    {flow.visibility === "private" && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-gray-100 text-gray-600 border border-gray-200">
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                        </svg>
                        Private
                      </span>
                    )}
                    {(flow.labels || []).map((label) => <LabelPill key={label} label={label} small />)}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 md:gap-x-4 gap-y-1 text-xs md:text-sm text-gray-500 mt-1">
                    {flow.trigger_type === "webhook" ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-violet-50 text-violet-700 border border-violet-200">
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" /></svg>
                        Webhook{flow.webhook_config?.auth_method ? ` \u00b7 ${flow.webhook_config.auth_method}` : ""}
                      </span>
                    ) : (
                      <>
                        <span className="flex items-center gap-1">
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>
                          {flow.frequency || flow.cron || "One-time"}
                        </span>
                        <span className="flex items-center gap-1">
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M5.25 8.25h15m-16.5 7.5h15m-1.8-13.5-3.9 19.5m-2.1-19.5-3.9 19.5" /></svg>
                          {flow.channel_name || flow.channel_id}
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
                    <button onClick={() => handlePauseResume(flow)} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors press-scale" title={flow.status === "active" ? "Pause" : "Resume"}>
                      {flow.status === "active" ? (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25v13.5m-7.5-13.5v13.5" /></svg>
                      ) : (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z" /></svg>
                      )}
                    </button>
                  )}
                  {flow.status === "active" && flow.trigger_type !== "webhook" && (
                    <button onClick={() => handleRunNow(flow)} className="p-1.5 rounded-lg text-gray-400 hover:text-brand-700 hover:bg-brand-50 transition-colors press-scale" title="Run now">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 8.689c0-.864.933-1.406 1.683-.977l7.108 4.061a1.125 1.125 0 0 1 0 1.954l-7.108 4.061A1.125 1.125 0 0 1 3 16.811V8.69ZM12.75 8.689c0-.864.933-1.406 1.683-.977l7.108 4.061a1.125 1.125 0 0 1 0 1.954l-7.108 4.061a1.125 1.125 0 0 1-1.683-.977V8.69Z" /></svg>
                    </button>
                  )}
                  <Link href={`/chat?flow=${flow.flow_id}`} className="p-1.5 rounded-lg text-gray-400 hover:text-blue-700 hover:bg-blue-50 transition-colors press-scale" title="Edit in chat">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" /></svg>
                  </Link>
                  <button onClick={() => handleDelete(flow)} className="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors press-scale" title="Delete">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" /></svg>
                  </button>
                </div>
              </div>
            </div>
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
    <button
      onClick={onClick}
      className="relative pb-3 pt-1 flex items-center gap-1.5 transition-colors"
      style={{
        color: active ? "#1F1D1A" : "#8C857D",
        borderBottom: active ? "2px solid #1F1D1A" : "2px solid transparent",
        marginBottom: "-1px",
        fontFamily: "var(--font-figtree), system-ui, sans-serif",
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
    </button>
  );
}
