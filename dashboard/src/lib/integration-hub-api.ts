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
  status: "active" | "inactive";
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
}

export type IntegrationWorkItemType = "milestone" | "task" | "risk" | "blocker";
export type IntegrationWorkItemStatus = "not_started" | "in_progress" | "blocked" | "completed";
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
}
export type IntegrationWorkItemInput = Omit<
  IntegrationWorkItem, "item_id" | "created_at" | "updated_at"
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
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

export async function fetchIntegrationAccounts(filters: {
  search?: string; stage?: string; health?: string;
} = {}): Promise<{ accounts: IntegrationAccount[] }> {
  const query = new URLSearchParams();
  if (filters.search) query.set("search", filters.search);
  if (filters.stage) query.set("stage", filters.stage);
  if (filters.health) query.set("health", filters.health);
  const suffix = query.size ? `?${query}` : "";
  return request(`/api/integration-hub/accounts${suffix}`);
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

export function createIntegrationAccount(input: IntegrationAccountInput) {
  return request<{ account: IntegrationAccount }>("/api/integration-hub/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateIntegrationAccount(accountId: string, input: IntegrationAccountInput) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
}

export function createIntegrationWorkItem(accountId: string, input: IntegrationWorkItemInput) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  );
}

export function updateIntegrationWorkItem(
  accountId: string, itemId: string, input: Partial<IntegrationWorkItemInput>,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items/${itemId}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  );
}

export function deleteIntegrationWorkItem(accountId: string, itemId: string) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/work-items/${itemId}`,
    { method: "DELETE" },
  );
}

export function createIntegrationActivity(
  accountId: string, input: Pick<IntegrationActivity, "type" | "message">,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/activities`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  );
}

export function createIntegrationSourceLink(
  accountId: string,
  input: Pick<IntegrationSourceLink, "type" | "title" | "url" | "notes">,
) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/source-links`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  );
}

export function deleteIntegrationSourceLink(accountId: string, linkId: string) {
  return request<{ account: IntegrationAccount }>(
    `/api/integration-hub/accounts/${accountId}/source-links/${linkId}`,
    { method: "DELETE" },
  );
}

export function formatIntegrationLabel(value: string) {
  return value.split("_").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}
