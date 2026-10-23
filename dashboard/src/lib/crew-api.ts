/**
 * Crew API client: personal crew settings, controls and the fleet tree.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type CrewRole = "cos" | "pm" | "worker" | "reviewer";
export type CrewSource = "gmail" | "calendar" | "board" | "slack" | "linear";

export interface CrewSettings {
  crew_id: string | null;
  exists: boolean;
  state: "active" | "paused" | "stopped";
  timezone: string;
  morning_time: string;
  evening_time: string;
  focus: string;
  sources: Record<CrewSource, boolean>;
  reports: { inbox: boolean; slack_dm: boolean };
  max_concurrent: number;
  daily_usd: number;
  max_depth: number;
  max_fanout: number;
  max_rework: number;
  max_tasks_per_day: number;
}

export interface CrewStatus {
  enabled: boolean;
  dispatcher_running: boolean;
  crew: CrewSettings;
  spend_today_usd: number;
  tasks_today: number;
  running: number;
  needs_you: number;
  started?: string;
}

export interface CrewNode {
  conversation_id: string;
  title: string | null;
  role: CrewRole;
  kind: "plan" | "report" | "task" | "review";
  state: string;
  rework_round: number;
  error: string | null;
  result: string;
  cost_usd: number;
  pull_requests: { url: string; number: number; repo: string }[];
  created_at: string;
  children: CrewNode[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error || `Request failed (${res.status})`);
  return body as T;
}

export const fetchCrew = () => call<CrewStatus>("/api/crew");

export const saveCrew = (settings: Partial<CrewSettings>) =>
  call<CrewStatus>("/api/crew", { method: "PUT", body: JSON.stringify(settings) });

export type CrewAction = "activate" | "pause" | "stop" | "run-plan" | "run-report";

export const crewAction = (action: CrewAction) =>
  call<CrewStatus>(`/api/crew/actions/${action}`, { method: "POST" });

export const fetchCrewTree = (days = 3) =>
  call<{ roots: CrewNode[] }>(`/api/crew/tree?days=${days}`);
