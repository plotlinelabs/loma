"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { fetchWebhookLogs, fetchFlows } from "../../lib/api";
import type { WebhookLog, Flow } from "../../lib/api";
import { basePath } from "../../lib/api";
import ClientTimestamp from "../../components/ClientTimestamp";
import { cn } from "@/lib/utils";
import { statusColors } from "@/lib/status-colors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RiArrowDownSLine, RiDownloadLine, RiLoader4Line } from "@remixicon/react";
import { EmptyState } from "@/components/EmptyState";

const AUTH_FILTERS = ["all", "success", "failed", "skipped"] as const;
const STATUS_FILTERS = ["all", "completed", "error", "running", "skipped", "pending"] as const;

function authBadge(result: string) {
  return (
    <Badge variant="outline" className={cn("rounded", statusColors[result] || statusColors.pending)}>
      {result}
    </Badge>
  );
}

function statusBadge(status: string) {
  return (
    <Badge variant="outline" className={cn("rounded", statusColors[status] || statusColors.pending)}>
      {status}
    </Badge>
  );
}

function payloadPreview(body: unknown): string {
  if (!body) return "";
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return text.length > 120 ? text.slice(0, 120) + "..." : text;
}

function ExpandedLogRow({ log }: { log: WebhookLog }) {
  return (
    <TableRow>
      <TableCell colSpan={7} className="bg-muted/50">
        <div className="space-y-2 text-sm">
          {/* Headers */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
              Headers
            </h4>
            <pre className="text-xs text-muted-foreground bg-card rounded-lg border border-border p-3 max-h-40 overflow-y-auto whitespace-pre-wrap break-all">
              {log.headers
                ? JSON.stringify(log.headers, null, 2)
                : "No headers recorded"}
            </pre>
          </div>

          {/* Payload */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
              Payload
            </h4>
            <pre className="text-xs text-muted-foreground bg-card rounded-lg border border-border p-3 max-h-60 overflow-y-auto whitespace-pre-wrap break-all">
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
              <Alert variant="destructive" className="text-xs">
                <AlertDescription>{log.error}</AlertDescription>
              </Alert>
            </div>
          )}

          {/* Links */}
          <div className="flex items-center gap-3">
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
              <span className="text-xs text-muted-foreground">
                Duration: {(log.duration_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        </div>
      </TableCell>
    </TableRow>
  );
}

function SkeletonRow() {
  return (
    <TableRow>
      <TableCell><Skeleton className="h-3 w-32" /></TableCell>
      <TableCell><Skeleton className="h-3 w-24" /></TableCell>
      <TableCell><Skeleton className="h-5 w-14 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded" /></TableCell>
      <TableCell><Skeleton className="h-3 w-40" /></TableCell>
      <TableCell><Skeleton className="h-3 w-12" /></TableCell>
      <TableCell><Skeleton className="h-3 w-10" /></TableCell>
    </TableRow>
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [logsData, flowsData] = await Promise.all([
        fetchWebhookLogs(initialFlowId || undefined, 100),
        fetchFlows(undefined, "webhook"),
      ]);
      setLogs(logsData.logs);
      setFlows(flowsData.flows);
    } catch (e) {
      console.error("Failed to load webhook logs:", e);
      setError("Failed to load webhook logs");
    } finally {
      setLoading(false);
    }
  }

  async function handleFlowFilterChange(newFlowId: string) {
    setFlowFilter(newFlowId);
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWebhookLogs(newFlowId || undefined, 100);
      setLogs(data.logs);
    } catch (e) {
      console.error("Failed to load webhook logs:", e);
      setError("Failed to load webhook logs");
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
    <div className="space-y-2 animate-fade-in-up">
      {/* Header */}
      <div>
        <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">
          Webhook Logs
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Incoming webhook requests across all flows
        </p>
      </div>

      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Flow selector */}
        <Select
          value={flowFilter}
          onValueChange={(v) => handleFlowFilterChange(v)}
        >
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="All flows" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All flows</SelectItem>
            {flows.map((f) => (
              <SelectItem key={f.flow_id} value={f.flow_id}>
                {f.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Auth result filter */}
        <div className="flex gap-1.5">
          {AUTH_FILTERS.map((a) => (
            <Button
              key={a}
              onClick={() => setAuthFilter(a)}
              variant={authFilter === a ? "secondary" : "outline"}
              size="xs"
              className={cn(
                "capitalize press-scale",
                authFilter === a && "bg-brand-100 text-brand-700"
              )}
            >
              {a === "all" ? "Auth: All" : a}
            </Button>
          ))}
        </div>

        {/* Execution status filter */}
        <div className="flex gap-1.5">
          {STATUS_FILTERS.map((s) => (
            <Button
              key={s}
              onClick={() => setStatusFilter(s)}
              variant={statusFilter === s ? "secondary" : "outline"}
              size="xs"
              className={cn(
                "capitalize press-scale",
                statusFilter === s && "bg-brand-100 text-brand-700"
              )}
            >
              {s === "all" ? "Status: All" : s}
            </Button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="text-left text-muted-foreground border-b border-border bg-muted/50">
                <TableHead className="font-medium">Received</TableHead>
                <TableHead className="font-medium">Flow</TableHead>
                <TableHead className="font-medium">Auth</TableHead>
                <TableHead className="font-medium">Status</TableHead>
                <TableHead className="font-medium">Payload</TableHead>
                <TableHead className="font-medium">Duration</TableHead>
                <TableHead className="font-medium"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </TableBody>
          </Table>
        </Card>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={RiDownloadLine}
          title={logs.length === 0 ? "No webhook requests received yet" : "No logs match the current filters"}
          description={logs.length === 0 ? "Webhook events will appear here when triggered" : undefined}
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="text-left text-muted-foreground border-b border-border bg-muted/50">
                  <TableHead className="font-medium">Received</TableHead>
                  <TableHead className="font-medium">Flow</TableHead>
                  <TableHead className="font-medium">Auth</TableHead>
                  <TableHead className="font-medium">Status</TableHead>
                  <TableHead className="font-medium">Payload</TableHead>
                  <TableHead className="font-medium">Duration</TableHead>
                  <TableHead className="font-medium"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((log) => (
                  <>
                    <TableRow
                      key={log.log_id}
                      className={cn(
                        "cursor-pointer",
                        expandedLogId === log.log_id && "bg-muted/50"
                      )}
                      onClick={() =>
                        setExpandedLogId(
                          expandedLogId === log.log_id ? null : log.log_id
                        )
                      }
                    >
                      <TableCell className="whitespace-nowrap">
                        <ClientTimestamp
                          iso={log.received_at}
                          variant="full"
                          className="text-foreground"
                        />
                      </TableCell>
                      <TableCell>
                        <a
                          href={`${basePath}/flows/${log.flow_id}`}
                          className="text-brand-600 hover:text-brand-700 font-medium truncate block max-w-[160px]"
                          onClick={(e) => e.stopPropagation()}
                          title={log.flow_name}
                        >
                          {log.flow_name || log.flow_id}
                        </a>
                      </TableCell>
                      <TableCell>{authBadge(log.auth_result)}</TableCell>
                      <TableCell>
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
                      </TableCell>
                      <TableCell>
                        <span className="text-xs text-muted-foreground font-mono truncate block max-w-[200px]">
                          {payloadPreview(log.body)}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {log.duration_ms != null
                          ? `${(log.duration_ms / 1000).toFixed(1)}s`
                          : "—"}
                      </TableCell>
                      <TableCell>
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
                          <RiArrowDownSLine
                            size={16}
                            className={cn(
                              "text-muted-foreground transition-transform",
                              expandedLogId === log.log_id && "rotate-180"
                            )}
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                    {expandedLogId === log.log_id && (
                      <ExpandedLogRow key={`${log.log_id}-expanded`} log={log} />
                    )}
                  </>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}

      {/* Footer count */}
      {!loading && (
        <div className="text-xs text-muted-foreground text-right">
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
        <div className="space-y-2">
          <div>
            <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Webhook Logs</h1>
            <p className="text-sm text-muted-foreground mt-1">Incoming webhook requests across all flows</p>
          </div>
          <div className="flex items-center justify-center py-16">
            <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
          </div>
        </div>
      }
    >
      <WebhookLogsContent />
    </Suspense>
  );
}
