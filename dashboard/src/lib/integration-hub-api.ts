import { basePath } from "./api";

export const INTEGRATION_STAGES = [
  "kickoff", "sdk_installation", "identification", "events_attributes",
  "pages_elements", "test_validation", "production_deployment",
  "first_campaign", "handover",
] as const;
export const INTEGRATION_HEALTH = [
  "on_track", "needs_attention", "blocked", "silent", "at_risk", "escalated",
] as const;

export type IntegrationStage = typeof INTEGRATION_STAGES[number];
export type IntegrationHealth = typeof INTEGRATION_HEALTH[number];

export interface IntegrationAccount {
  account_id: string;
  name: string;
  status: "active" | "inactive" | "archived";
  owner_email: string | null;
  stage: IntegrationStage;
  health: IntegrationHealth;
  health_override_enabled: boolean;
  calculated_health: IntegrationHealth;
  effective_health: IntegrationHealth;
  calculated_health_reasons: string[];
  overdue_count: number;
  upcoming_count: number;
  open_blocker_count: number;
  health_reason: string | null;
  target_go_live_at: string | null;
  current_blocker: string | null;
  next_action: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  platforms: string[];
  environments: string[];
  stakeholders: string[];
  go_live_criteria: string | null;
  completion_percentage: number;
  work_items: IntegrationWorkItem[];
  activities: IntegrationActivity[];
  source_links: IntegrationSourceLink[];
  projects: IntegrationProject[];
  interactions: IntegrationInteraction[];
  sync_sources: IntegrationSyncSource[];
  conversations: IntegrationConversation[];
  findings: IntegrationFinding[];
}

export interface IntegrationConversation {
  conversation_id: string;
  source: string;
  issue_title?: string | null;
  issue_status?: string | null;
  assignee?: unknown;
  state: IntegrationInteraction["conversation_state"];
  requires_response: boolean;
  summary: string;
  source_url?: string | null;
  last_interaction_at: string;
}

export interface IntegrationFinding {
  finding_id: string;
  classification: string;
  summary: string;
  requires_response: boolean;
  conversation_state: IntegrationInteraction["conversation_state"];
  confidence: number;
  review_status: "unreviewed" | "confirmed" | "corrected";
}

export interface IntegrationInteraction {
  interaction_id: string;
  source: IntegrationSourceLink["type"];
  source_url: string | null;
  occurred_at: string;
  direction: "customer_to_plotline" | "plotline_to_customer" | "internal";
  classification: string | null;
  requires_response: boolean;
  meaningful_contact: boolean;
  conversation_state: "waiting_on_plotline" | "waiting_on_customer" | "internally_blocked" | "resolved" | "monitoring" | "no_action_required";
  summary: string;
  confidence: number;
  human_status: "unreviewed" | "confirmed" | "corrected";
}

export interface IntegrationProject {
  project_id: string;
  name: string;
  description: string | null;
  status: "active" | "paused" | "completed" | "cancelled";
  version: number;
  owner_email: string | null;
  target_at: string | null;
  playbook: "mobile_sdk" | "web_sdk" | null;
  created_at: string;
}

export interface IntegrationActivity {
  activity_id: string;
  type: "note" | "decision" | "update";
  message: string;
  created_at: string;
  created_by: string;
}

export interface IntegrationSourceLink {
  link_id: string;
  type: "grain" | "slack" | "linear" | "pylon" | "hubspot" | "document" | "other";
  title: string;
  url: string;
  notes: string | null;
  created_at: string;
  created_by: string;
  updated_at: string;
  version: number;
}

export type IntegrationWorkItemType = "milestone" | "task" | "risk" | "blocker";
export type IntegrationWorkItemStatus = "todo" | "pending" | "open" | "in_progress" | "blocked" | "mitigating" | "completed" | "achieved" | "missed" | "accepted" | "resolved" | "cancelled";
export interface IntegrationWorkItem {
  item_id: string;
  type: IntegrationWorkItemType;
  title: string;
  description: string | null;
  status: IntegrationWorkItemStatus;
  owner_email: string | null;
  due_at: string | null;
  severity: "low" | "medium" | "high" | "critical" | null;
  dependency: string | null;
  resolution: string | null;
  escalated: boolean;
  created_at: string;
  updated_at: string;
  version: number;
}
export type IntegrationWorkItemInput = Omit<
  IntegrationWorkItem, "item_id" | "created_at" | "updated_at" | "version"
>;

export type IntegrationAccountInput = Pick<IntegrationAccount, "name"> &
  Partial<Pick<IntegrationAccount, "owner_email" | "stage" | "health" |
    "health_reason" | "target_go_live_at" | "current_blocker" | "next_action" |
    "platforms" | "environments" | "stakeholders" | "go_live_criteria" |
    "completion_percentage" | "health_override_enabled">>;

export interface IntegrationAction extends IntegrationWorkItem {
  account_id: string;
  account_name: string;
  is_overdue: boolean;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${basePath}${url}`, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || data.error || `Request failed: ${response.status}`);
  return data;
}

export async function fetchIntegrationAccounts(filters: {
  search?: string; stage?: string; health?: string; owner?: string; status?: string;
  cursor?: string; limit?: number;
} = {}): Promise<{ accounts: IntegrationAccount[]; pagination: { next_cursor: string | null; limit: number } }> {
  const query = new URLSearchParams();
  if (filters.search) query.set("search", filters.search);
  if (filters.stage) query.set("stage", filters.stage);
  if (filters.health) query.set("health", filters.health);
  if (filters.owner) query.set("owner", filters.owner);
  if (filters.status) query.set("status", filters.status);
  if (filters.cursor) query.set("cursor", filters.cursor);
  query.set("limit", String(filters.limit || 25));
  return request(`/api/integration-hub/accounts?${query}`);
}

export function fetchIntegrationAccount(accountId: string) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}`,
  );
}

export function fetchIntegrationActions() {
  return request<{ actions: IntegrationAction[]; attention_accounts: IntegrationAccount[] }>(
    "/api/integration-hub/actions",
  );
}

export function createIntegrationAccount(input: IntegrationAccountInput, idempotencyKey: string) {
  return request<{ account: IntegrationAccount }>("/api/integration-hub/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function updateIntegrationAccount(accountId: string, input: IntegrationAccountInput, version?: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...(version === undefined ? {} : { "If-Match": `"${version}"` }) },
      body: JSON.stringify(input),
    },
  );
}

export function archiveIntegrationAccount(accountId: string, version: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/archive`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify({ reason: "Archived from dashboard" }) },
  );
}

export function restoreIntegrationAccount(accountId: string, version: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/restore`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify({}) },
  );
}

export function createIntegrationProject(accountId: string, version: number, input: {
  name: string; description?: string; owner_email?: string; target_at?: string; playbook?: string;
}) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/projects`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify(input) },
  );
}

export function archiveIntegrationProject(accountId: string, projectId: string, version: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/projects/${projectId}/archive`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify({ reason: "Deleted from dashboard" }) },
  );
}

export function createIntegrationWorkItem(accountId: string, version: number, input: IntegrationWorkItemInput) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify(input) },
  );
}

export function updateIntegrationWorkItem(
  accountId: string, itemId: string, version: number, input: Partial<IntegrationWorkItemInput>,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items/${itemId}`,
    { method: "PATCH", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify(input) },
  );
}

export function archiveIntegrationWorkItem(accountId: string, itemId: string, version: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items/${itemId}/archive`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify({ reason: "Archived from dashboard" }) },
  );
}

export function createIntegrationActivity(
  accountId: string, version: number, input: Pick<IntegrationActivity, "type" | "message">,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/activities`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify(input) },
  );
}

export function createIntegrationSourceLink(
  accountId: string, version: number,
  input: Pick<IntegrationSourceLink, "type" | "title" | "url" | "notes">,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/source-links`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify(input) },
  );
}

export function archiveIntegrationSourceLink(accountId: string, sourceId: string, version: number) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/source-links/${sourceId}/archive`,
    { method: "POST", headers: { "Content-Type": "application/json", "If-Match": `"${version}"` }, body: JSON.stringify({ reason: "Deleted from dashboard" }) },
  );
}

export function formatIntegrationLabel(value: string) {
  return value.split("_").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}

export interface IntegrationSyncSource {
  mapping_id: string;
  source: "slack" | "grain" | "pylon";
  tenant_id: string;
  external_id: string;
  label: string | null;
  status: "active" | "paused";
  sync_status: "never_synced" | "succeeded" | "failed";
  last_error: string | null;
  last_synced_at: string | null;
  config: { thread_ts?: string; source_url?: string; limit?: number; customer_name?: string; issue_ids?: string[] };
}

export interface PylonCustomerMatch {
  customer_id: string;
  name: string;
  domains?: string[];
  issue_count: number | null;
  preview_issues: Array<{ id: string; title: string; state: string; updated_at: string }>;
}

export function searchPylonCustomers(query: string) {
  return request<{ customers: PylonCustomerMatch[] }>(
    `/api/integration-hub/pylon/customers?query=${encodeURIComponent(query)}`,
  );
}

export interface PylonIssueSummary {
  id: string;
  title: string;
  state: string;
  assignee?: { id?: string; name?: string; email?: string } | string | null;
  created_at?: string | null;
  updated_at?: string | null;
  url?: string | null;
}

export interface PylonIssueDetail {
  issue: PylonIssueSummary & Record<string, unknown>;
  messages: Array<Record<string, unknown>>;
}

export function fetchPylonIssues(
  accountId: string,
  params: { cursor?: string; status?: string; query?: string; limit?: number } = {},
) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 25));
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.status) query.set("status", params.status);
  if (params.query) query.set("query", params.query);
  return request<{
    issues: PylonIssueSummary[];
    pagination: { next_cursor: string | null; has_next_page: boolean };
  }>(`/api/integration-hub/accounts/${accountId}/pylon/issues?${query.toString()}`);
}

export function fetchPylonIssue(accountId: string, issueId: string) {
  return request<PylonIssueDetail>(
    `/api/integration-hub/accounts/${accountId}/pylon/issues/${encodeURIComponent(issueId)}`,
  );
}

export function createIntegrationSyncSource(accountId: string, input: {
  source: IntegrationSyncSource["source"];
  tenant_id: string;
  external_id: string;
  label?: string;
  config?: IntegrationSyncSource["config"];
}) {
  return request<{ source: IntegrationSyncSource }>(
    `/api/integration-hub/accounts/${accountId}/sync-sources`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  );
}

export function syncIntegrationSource(accountId: string, mappingId: string) {
  return request<{ source: IntegrationSyncSource; created: number; seen: number }>(
    `/api/integration-hub/accounts/${accountId}/sync-sources/${mappingId}/sync`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
}
