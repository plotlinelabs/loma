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
  health_reason: string | null;
  target_go_live_at: string | null;
  current_blocker: string | null;
  next_action: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export type IntegrationAccountInput = Pick<IntegrationAccount, "name"> &
  Partial<Pick<IntegrationAccount, "owner_email" | "stage" | "health" |
    "health_reason" | "target_go_live_at" | "current_blocker" | "next_action">>;

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

export function formatIntegrationLabel(value: string) {
  return value.split("_").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}
