/**
 * Onboarding tracker API client.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type FieldType =
  | "text"
  | "longtext"
  | "number"
  | "date"
  | "select"
  | "multiselect"
  | "link"
  | "person";

export const FIELD_TYPES: FieldType[] = [
  "text",
  "longtext",
  "number",
  "date",
  "select",
  "multiselect",
  "link",
  "person",
];

export type FieldSource =
  | "human"
  | "contract"
  | "product"
  | "hubspot"
  | "billing"
  | "grain";

export const FIELD_SOURCES: { key: FieldSource; label: string }[] = [
  { key: "human", label: "Person" },
  { key: "contract", label: "Contract" },
  { key: "product", label: "Product data" },
  { key: "hubspot", label: "HubSpot" },
  { key: "billing", label: "Billing" },
  { key: "grain", label: "Calls (Grain)" },
];

export const MILESTONES: { key: string; label: string }[] = [
  { key: "sdk_live", label: "SDK live" },
  { key: "first_campaign", label: "First campaign" },
  { key: "adopting", label: "Adopting" },
];

/** The four module layers. Their options come from the module catalogue. */
export const MODULE_LAYERS: { key: string; label: string; hint: string }[] = [
  { key: "modules_paid", label: "Paid", hint: "In the contract. Ticked at kickoff" },
  { key: "modules_enabled", label: "Enabled", hint: "Switched on in the dashboard" },
  { key: "modules_integrated", label: "Integrated", hint: "Working in the client's app" },
  { key: "modules_in_use", label: "In use", hint: "Live campaigns, last 30-90 days" },
];

export interface OnboardingField {
  key: string;
  label: string;
  type: FieldType;
  options?: string[];
  options_from?: "modules";
  section?: string;
  source?: FieldSource;
  on_card?: boolean;
  required_from?: string | null;
}

export interface OnboardingStage {
  key: string;
  label: string;
  description?: string;
  terminal?: boolean;
  milestone?: string | null;
}

export interface OnboardingModule {
  key: string;
  label: string;
  enabled_signal?: string;
  integrated_signal?: string;
  usage_signal?: string;
}

export interface OnboardingRules {
  first_campaign_min_users: number;
  adopting_min_campaigns: number;
  idle_days: number;
  stale_days: number;
  pilot_ending_days: number;
}

export interface OnboardingTemplate {
  stages: OnboardingStage[];
  fields: OnboardingField[];
  modules: OnboardingModule[];
  rules: OnboardingRules;
  edit_min_role: string;
  template_min_role: string;
}

export interface OnboardingConfig extends OnboardingTemplate {
  can_edit: boolean;
  can_edit_template: boolean;
  version?: number | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

export type FieldValue = string | number | string[] | null | undefined;

export interface ModuleGaps {
  paid_not_integrated: string[];
  enabled_not_paid: string[];
  integrated_not_used: string[];
  upsell: string[];
}

export interface OnboardingRecord {
  record_id: string;
  name: string;
  account: string;
  stage: string;
  fields: Record<string, FieldValue>;
  meta: Record<string, { source: string; by: string; at: string }>;
  created_at: string;
  updated_at: string;
  updated_by?: string;
  derived: {
    days_to_live_gross: number | null;
    days_to_live_net: number | null;
    pilot_days_left: number | null;
    first_campaign_qualified: boolean;
    days_live_to_first_campaign: number | null;
    days_live_without_campaign: number | null;
    mtu_usage_pct: number | null;
    module_gaps: ModuleGaps;
    missing_required: string[];
    suggested_stage: string | null;
    flags: string[];
  };
}

export interface OnboardingEvent {
  event_id: string;
  record_id: string;
  at: string;
  actor: string;
  source: string;
  changes: { field: string; old: FieldValue; new: FieldValue }[];
  note?: string | null;
}

export interface TemplateHistoryEntry {
  version: number;
  at: string;
  by: string;
  changes: string[];
}

async function json<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
  return data as T;
}

export async function fetchOnboardingConfig(): Promise<OnboardingConfig> {
  return json(await fetch(`${API_BASE}/api/onboarding/config`));
}

export async function saveOnboardingTemplate(
  template: OnboardingTemplate,
): Promise<OnboardingConfig & { changes: string[] }> {
  return json(
    await fetch(`${API_BASE}/api/onboarding/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(template),
    }),
  );
}

export async function fetchTemplateHistory(): Promise<TemplateHistoryEntry[]> {
  const data = await json<{ history: TemplateHistoryEntry[] }>(
    await fetch(`${API_BASE}/api/onboarding/config/history`),
  );
  return data.history ?? [];
}

export async function fetchOnboardingRecords(): Promise<OnboardingRecord[]> {
  const data = await json<{ records: OnboardingRecord[] }>(
    await fetch(`${API_BASE}/api/onboarding/records`),
  );
  return data.records ?? [];
}

export async function fetchOnboardingRecord(id: string): Promise<{
  record: OnboardingRecord;
  events: OnboardingEvent[];
  siblings: { record_id: string; name: string; stage: string }[];
}> {
  return json(
    await fetch(`${API_BASE}/api/onboarding/records/${encodeURIComponent(id)}`),
  );
}

export async function createOnboardingRecord(body: {
  name: string;
  account?: string;
  stage?: string;
  fields?: Record<string, FieldValue>;
}): Promise<OnboardingRecord> {
  const data = await json<{ record: OnboardingRecord }>(
    await fetch(`${API_BASE}/api/onboarding/records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  return data.record;
}

export async function updateOnboardingRecord(
  id: string,
  changes: Record<string, FieldValue>,
  note?: string,
): Promise<OnboardingRecord> {
  const data = await json<{ record: OnboardingRecord }>(
    await fetch(`${API_BASE}/api/onboarding/records/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes, note }),
    }),
  );
  return data.record;
}

export const FLAG_LABELS: Record<string, string> = {
  overdue: "Overdue",
  stale: "Stale",
  blocked: "Blocked",
  late: "Past target",
  pilot_ending: "Pilot ending",
  idle: "Live, no campaign",
  paid_gap: "Paid, not integrated",
  unpaid_enabled: "Enabled, not paid",
};

export const FLAG_COLORS: Record<string, string> = {
  overdue: "bg-red-50 text-red-700 border-red-200",
  late: "bg-red-50 text-red-700 border-red-200",
  idle: "bg-red-50 text-red-700 border-red-200",
  blocked: "bg-orange-50 text-orange-700 border-orange-200",
  paid_gap: "bg-orange-50 text-orange-700 border-orange-200",
  stale: "bg-amber-50 text-amber-700 border-amber-200",
  unpaid_enabled: "bg-violet-50 text-violet-700 border-violet-200",
  pilot_ending: "bg-blue-50 text-blue-700 border-blue-200",
};

export const FLAG_BORDER: Record<string, string> = {
  overdue: "border-l-red-500",
  late: "border-l-red-500",
  idle: "border-l-red-500",
  blocked: "border-l-orange-500",
  paid_gap: "border-l-orange-500",
  stale: "border-l-amber-400",
  unpaid_enabled: "border-l-violet-500",
  pilot_ending: "border-l-blue-500",
};

export function formatValue(value: FieldValue): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

/** Index of the stage carrying a milestone, or -1. */
export function milestoneIndex(stages: OnboardingStage[], milestone: string) {
  return stages.findIndex((s) => s.milestone === milestone);
}
