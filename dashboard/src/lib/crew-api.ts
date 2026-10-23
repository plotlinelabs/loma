/**
 * Crew API client: a personal crew of user-defined agents (members), the links
 * between them, their permissions and schedules, plus controls and the fleet tree.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** What a member may do with sends and merges (email, Slack, calendar, PR merge). */
export type WritePolicy = "ask" | "deny" | "allow";

export interface CrewMember {
  id: string;
  name: string;
  description: string;
  instructions: string;
  /** Optional Loma agent identity: its skills and tools then limit this member. */
  agent_id: string | null;
  model: string;
  /** Members this one can hand work to. */
  delegates_to: string[];
  /** Member that reviews this one's results before they count as done. */
  reviewed_by: string | null;
  writes: WritePolicy;
  can_escalate: boolean;
}

export interface CrewSchedule {
  id: string;
  name: string;
  member_id: string;
  time: string;
  /** 0 = Monday … 6 = Sunday. */
  days: number[];
  prompt: string;
  include_activity: boolean;
  /** updates: also send replies from turns that handed work out, not only the finished result. */
  deliver: { inbox: boolean; slack_dm: boolean; updates: boolean };
  enabled: boolean;
}

export interface CrewSettings {
  crew_id: string | null;
  exists: boolean;
  state: "active" | "paused" | "stopped";
  timezone: string;
  notes: string;
  members: CrewMember[];
  schedules: CrewSchedule[];
  max_concurrent: number;
  daily_usd: number;
  max_depth: number;
  max_fanout: number;
  max_rework: number;
  max_tasks_per_day: number;
  /** Server allows members with writes = "allow". */
  unattended_writes_allowed: boolean;
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
  member_id: string;
  member_name: string | null;
  kind: "task" | "review";
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

export type CrewAction = "activate" | "pause" | "stop";

export const crewAction = (action: CrewAction) =>
  call<CrewStatus>(`/api/crew/actions/${action}`, { method: "POST" });

export const runSchedule = (scheduleId: string) =>
  call<CrewStatus>("/api/crew/actions/run-schedule", {
    method: "POST",
    body: JSON.stringify({ schedule_id: scheduleId }),
  });

export const giveTask = (task: { member_id: string; title: string; brief: string; acceptance?: string }) =>
  call<CrewStatus>("/api/crew/tasks", { method: "POST", body: JSON.stringify(task) });

export const fetchCrewTree = (days = 3) =>
  call<{ roots: CrewNode[] }>(`/api/crew/tree?days=${days}`);
