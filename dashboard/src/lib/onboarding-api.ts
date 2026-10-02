/**
 * Onboarding tracker API client.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type FieldType = "text" | "longtext" | "number" | "date" | "select" | "multiselect" | "link" | "person";

export interface OnboardingField {
  key: string;
  label: string;
  type: FieldType;
  options?: string[];
}

export interface OnboardingStage {
  key: string;
  label: string;
  terminal?: boolean;
}

export interface OnboardingConfig {
  stages: OnboardingStage[];
  fields: OnboardingField[];
  edit_min_role: string;
  can_edit: boolean;
}

export type FieldValue = string | number | string[] | null | undefined;

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

async function json<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
  return data as T;
}

export async function fetchOnboardingConfig(): Promise<OnboardingConfig> {
  return json(await fetch(`${API_BASE}/api/onboarding/config`));
}

export async function fetchOnboardingRecords(): Promise<OnboardingRecord[]> {
  const data = await json<{ records: OnboardingRecord[] }>(await fetch(`${API_BASE}/api/onboarding/records`));
  return data.records ?? [];
}

export async function fetchOnboardingRecord(id: string): Promise<{
  record: OnboardingRecord;
  events: OnboardingEvent[];
  siblings: { record_id: string; name: string; stage: string }[];
}> {
  return json(await fetch(`${API_BASE}/api/onboarding/records/${encodeURIComponent(id)}`));
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
};

export const FLAG_COLORS: Record<string, string> = {
  overdue: "bg-red-50 text-red-700 border-red-200",
  late: "bg-red-50 text-red-700 border-red-200",
  blocked: "bg-orange-50 text-orange-700 border-orange-200",
  stale: "bg-amber-50 text-amber-700 border-amber-200",
  pilot_ending: "bg-blue-50 text-blue-700 border-blue-200",
};

export function formatValue(value: FieldValue): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}
