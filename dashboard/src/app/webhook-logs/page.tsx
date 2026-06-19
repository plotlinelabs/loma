"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { fetchWebhookLogs, fetchFlows } from "../../lib/api";
import type { WebhookLog, Flow } from "../../lib/api";
import { basePath } from "../../lib/api";
import ClientTimestamp from "../../components/ClientTimestamp";

const AUTH_FILTERS = ["all", "success", "failed", "skipped"] as const;
const STATUS_FILTERS = ["all", "completed", "error", "running", "skipped", "pending"] as const;

function authBadge(result: string) {
  const styles: Record<string, string> = {
    success: "bg-green-50 text-green-700 border-green-200",
    failed: "bg-red-50 text-red-700 border-red-200",
    skipped: "bg-gray-50 text-gray-500 border-gray-200",
    pending: "bg-yellow-50 text-yellow-700 border-yellow-200",
  };
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium border ${styles[result] || styles.pending}`}
    >
      {result}
    </span>
  );
}

function statusBadge(status: string) {
  const styles: Record<string, string> = {
    completed: "bg-green-50 text-green-700 border-green-200",
    error: "bg-red-50 text-red-700 border-red-200",
    running: "bg-blue-50 text-blue-700 border-blue-200",
    skipped: "bg-gray-50 text-gray-500 border-gray-200",
    pending: "bg-yellow-50 text-yellow-700 border-yellow-200",
  };
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium border ${styles[status] || styles.pending}`}
    >
      {status}
    </span>
  );
}

function payloadPreview(body: unknown): string {
  if (!body) return "";
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return text.length > 120 ? text.slice(0, 120) + "..." : text;
}

function ExpandedLogRow({ log }: { log: WebhookLog }) {
  return (
    <tr>
      <td colSpan={7} className="px-4 py-3 bg-gray-50">
        <div className="space-y-3 text-sm">
          {/* Headers */}
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Headers
            </h4>
            <pre className="text-xs text-gray-600 bg-surface rounded-lg border border-gray-200 p-3 max-h-40 overflow-y-auto whitespace-pre-wrap break-all">
              {log.headers
                ? JSON.stringify(log.headers, null, 2)
                : "No headers recorded"}
            </pre>
          </div>

          {/* Payload */}
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Payload
            </h4>
            <pre className="text-xs text-gray-600 bg-surface rounded-lg border border-gray-200 p-3 max-h-60 overflow-y-auto whitespace-pre-wrap break-all">
              {log.body
                ? typeof log.body === "string"
                  ? log.body
                  : JSON.stringify(log.body, null, 2)
                : "No body"}
            </pre>
          </div>

          {/* Error */}
          {log.error && (
            <div>
              <h4 className="text-xs font-semibold text-red-500 uppercase tracking-wider mb-1">
                Error
              </h4>
              <div className="text-xs text-red-600 bg-red-50 rounded-lg border border-red-200 p-3">
                {log.error}
              </div>
            </div>
          )}

          {/* Links */}
          <div className="flex items-center gap-4">
            {log.conversation_id && (
              <a
                href={`${basePath}/conversations/${log.conversation_id}`}
                className="text-xs text-brand-600 hover:text-brand-700 underline"
              >
                View Conversation
              </a>
            )}
            {log.flow_id && (
              <a
                href={`${basePath}/flows/${log.flow_id}`}
                className="text-xs text-brand-600 hover:text-brand-700 underline"
              >
                View Flow
              </a>
            )}
            {log.duration_ms != null && (
              <span className="text-xs text-gray-500">
                Duration: {(log.duration_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        </div>
      </td>
    </tr>
  );
}

function SkeletonRow() {
  return (
    <tr>
      <td className="px-4 py-3"><div className="skeleton h-3 w-32" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-24" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-14 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-40" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-12" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-10" /></td>
    </tr>
  );
}

function WebhookLogsContent() {
  const searchParams = useSearchParams();
  const initialFlowId = searchParams.get("flowId") || "";

  const [logs, setLogs] = useState<WebhookLog[]>([]);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [flowFilter, setFlowFilter] = useState(initialFlowId);
  const [authFilter, setAuthFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const [logsData, flowsData] = await Promise.all([
        fetchWebhookLogs(initialFlowId || undefined, 100),
        fetchFlows(undefined, "webhook"),
      ]);
      setLogs(logsData.logs);
      setFlows(flowsData.flows);
    } catch (e) {
      console.error("Failed to load webhook logs:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleFlowFilterChange(newFlowId: string) {
    setFlowFilter(newFlowId);
    setLoading(true);
    try {
      const data = await fetchWebhookLogs(newFlowId || undefined, 100);
      setLogs(data.logs);
    } catch (e) {
      console.error("Failed to load webhook logs:", e);
    } finally {
      setLoading(false);
    }
  }

  // Client-side filters for auth result and execution status
  let filtered = logs;
  if (authFilter !== "all") {
    filtered = filtered.filter((l) => l.auth_result === authFilter);
  }
  if (statusFilter !== "all") {
    filtered = filtered.filter((l) => l.execution_status === statusFilter);
  }

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      {/* Header */}
      <div>
        <h1 className="text-xl md:text-2xl font-semibold text-gray-900">
          Webhook Logs
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Incoming webhook requests across all flows
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Flow selector */}
        <select
          value={flowFilter}
          onChange={(e) => handleFlowFilterChange(e.target.value)}
          className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
        >
          <option value="">All flows</option>
          {flows.map((f) => (
            <option key={f.flow_id} value={f.flow_id}>
              {f.name}
            </option>
          ))}
        </select>

        {/* Auth result filter */}
        <div className="flex gap-1.5">
          {AUTH_FILTERS.map((a) => (
            <button
              key={a}
              onClick={() => setAuthFilter(a)}
              className={`px-2.5 py-1 text-xs rounded-lg font-medium capitalize transition-colors press-scale ${
                authFilter === a
                  ? "bg-brand-100 text-brand-700"
                  : "bg-surface border border-gray-200 text-gray-600 hover:bg-gray-50"
              }`}
            >
              {a === "all" ? "Auth: All" : a}
            </button>
          ))}
        </div>

        {/* Execution status filter */}
        <div className="flex gap-1.5">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-2.5 py-1 text-xs rounded-lg font-medium capitalize transition-colors press-scale ${
                statusFilter === s
                  ? "bg-brand-100 text-brand-700"
                  : "bg-surface border border-gray-200 text-gray-600 hover:bg-gray-50"
              }`}
            >
              {s === "all" ? "Status: All" : s}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-100 bg-gray-50/50">
                <th className="px-4 py-3 font-medium">Received</th>
                <th className="px-4 py-3 font-medium">Flow</th>
                <th className="px-4 py-3 font-medium">Auth</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Payload</th>
                <th className="px-4 py-3 font-medium">Duration</th>
                <th className="px-4 py-3 font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </tbody>
          </table>
        </div>
      ) : filtered.length === 0 ? (
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <div className="text-gray-400 text-sm">
            {logs.length === 0
              ? "No webhook requests received yet."
              : "No logs match the current filters."}
          </div>
        </div>
      ) : (
        <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-100 bg-gray-50/50">
                  <th className="px-4 py-3 font-medium">Received</th>
                  <th className="px-4 py-3 font-medium">Flow</th>
                  <th className="px-4 py-3 font-medium">Auth</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Payload</th>
                  <th className="px-4 py-3 font-medium">Duration</th>
                  <th className="px-4 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((log) => (
                  <>
                    <tr
                      key={log.log_id}
                      className={`hover:bg-gray-50 cursor-pointer transition-colors ${
                        expandedLogId === log.log_id ? "bg-gray-50" : ""
                      }`}
                      onClick={() =>
                        setExpandedLogId(
                          expandedLogId === log.log_id ? null : log.log_id
                        )
                      }
                    >
                      <td className="px-4 py-3 whitespace-nowrap">
                        <ClientTimestamp
                          iso={log.received_at}
                          variant="full"
                          className="text-gray-700"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <a
                          href={`${basePath}/flows/${log.flow_id}`}
                          className="text-brand-600 hover:text-brand-700 font-medium truncate block max-w-[160px]"
                          onClick={(e) => e.stopPropagation()}
                          title={log.flow_name}
                        >
                          {log.flow_name || log.flow_id}
                        </a>
                      </td>
                      <td className="px-4 py-3">{authBadge(log.auth_result)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          {statusBadge(log.execution_status)}
                          {log.error && (
                            <span
                              className="text-red-500 text-xs truncate max-w-[100px]"
                              title={log.error}
                            >
                              {log.error.slice(0, 30)}...
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs text-gray-500 font-mono truncate block max-w-[200px]">
                          {payloadPreview(log.body)}
                        </span>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-gray-500">
                        {log.duration_ms != null
                          ? `${(log.duration_ms / 1000).toFixed(1)}s`
                          : "\u2014"}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          {log.conversation_id && (
                            <a
                              href={`${basePath}/conversations/${log.conversation_id}`}
                              className="text-xs text-brand-600 hover:text-brand-700 underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              View
                            </a>
                          )}
                          <svg
                            className={`w-4 h-4 text-gray-400 transition-transform ${
                              expandedLogId === log.log_id ? "rotate-180" : ""
                            }`}
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={1.5}
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m19.5 8.25-7.5 7.5-7.5-7.5"
                            />
                          </svg>
                        </div>
                      </td>
                    </tr>
                    {expandedLogId === log.log_id && (
                      <ExpandedLogRow key={`${log.log_id}-expanded`} log={log} />
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Footer count */}
      {!loading && (
        <div className="text-xs text-gray-400 text-right">
          Showing {filtered.length} of {logs.length} logs
        </div>
      )}
    </div>
  );
}

export default function WebhookLogsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-4 md:space-y-6">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Webhook Logs</h1>
            <p className="text-sm text-gray-500 mt-1">Loading...</p>
          </div>
        </div>
      }
    >
      <WebhookLogsContent />
    </Suspense>
  );
}
