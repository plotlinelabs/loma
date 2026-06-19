/**
 * Governance API client — typed fetch wrappers for users, teams, tool configs.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export type SystemRole = "admin" | "maintainer" | "operator" | "analyst" | "chatter";

export interface User {
  email: string;
  name: string;
  avatar: string;
  system_role: SystemRole;
  tool_assignments: Record<string, ToolAssignment>;
  theme_preference?: "light" | "dark" | "system";
  pinned_conversations?: Array<{ conversation_id: string; pinned_at: string }>;
  claude_connected?: boolean;
  claude_email?: string;
  claude_pool_enabled?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface ToolAssignment {
  role?: string | null;
  oauth_status?: "connected" | "expired" | "not_connected" | null;
  last_used?: string | null;
}

export interface Team {
  team_id: string;
  name: string;
  color: string;
  bg_color: string;
  members: string[];
  tool_defaults: Record<string, TeamToolDefault>;
  created_at?: string;
  updated_at?: string;
}

export interface TeamToolDefault {
  role?: string | null;
  oauth_required?: boolean | null;
}

export interface ToolConfig {
  tool_key: string;
  auth_mode: "loma-managed" | "tool-managed";
  roles: { name: string; description: string }[];
  oauth?: {
    client_id: string;
    redirect_uri: string;
    scopes: string[];
    configured: boolean;
  } | null;
  updated_at?: string;
}

export interface EffectiveRole {
  role: string | null;
  source: string;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function fetchCurrentUser(): Promise<User> {
  const res = await fetch(`${API_BASE}/api/governance/me`);
  if (!res.ok) throw new Error(`Failed to fetch current user: ${res.status}`);
  return res.json();
}

export async function fetchUsers(): Promise<User[]> {
  const res = await fetch(`${API_BASE}/api/governance/users`);
  if (!res.ok) throw new Error(`Failed to fetch users: ${res.status}`);
  const data = await res.json();
  return data.users;
}

export async function fetchUser(email: string): Promise<{ user: User; teams: Team[] }> {
  const res = await fetch(`${API_BASE}/api/governance/users/${encodeURIComponent(email)}`);
  if (!res.ok) throw new Error(`Failed to fetch user: ${res.status}`);
  return res.json();
}

export async function updateUser(
  email: string,
  updates: Partial<Pick<User, "system_role" | "tool_assignments" | "name" | "claude_pool_enabled">>,
): Promise<User> {
  const res = await fetch(`${API_BASE}/api/governance/users/${encodeURIComponent(email)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update user: ${res.status}`);
  const data = await res.json();
  return data.user;
}

export async function fetchTeams(): Promise<Team[]> {
  const res = await fetch(`${API_BASE}/api/governance/teams`);
  if (!res.ok) throw new Error(`Failed to fetch teams: ${res.status}`);
  const data = await res.json();
  return data.teams;
}

export async function fetchTeam(teamId: string): Promise<{ team: Team; members: User[] }> {
  const res = await fetch(`${API_BASE}/api/governance/teams/${encodeURIComponent(teamId)}`);
  if (!res.ok) throw new Error(`Failed to fetch team: ${res.status}`);
  return res.json();
}

export async function createTeam(team: {
  team_id: string;
  name: string;
  color?: string;
  bg_color?: string;
  members?: string[];
  tool_defaults?: Record<string, TeamToolDefault>;
}): Promise<Team> {
  const res = await fetch(`${API_BASE}/api/governance/teams`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(team),
  });
  if (!res.ok) throw new Error(`Failed to create team: ${res.status}`);
  const data = await res.json();
  return data.team;
}

export async function updateTeam(
  teamId: string,
  updates: Partial<Pick<Team, "name" | "color" | "bg_color" | "members" | "tool_defaults">>,
): Promise<Team> {
  const res = await fetch(`${API_BASE}/api/governance/teams/${encodeURIComponent(teamId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update team: ${res.status}`);
  const data = await res.json();
  return data.team;
}

export async function deleteTeam(teamId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/governance/teams/${encodeURIComponent(teamId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete team: ${res.status}`);
}

export async function fetchToolConfigs(): Promise<ToolConfig[]> {
  const res = await fetch(`${API_BASE}/api/governance/tools`);
  if (!res.ok) throw new Error(`Failed to fetch tool configs: ${res.status}`);
  const data = await res.json();
  return data.tools;
}

export async function updateToolConfig(
  toolKey: string,
  updates: Partial<Pick<ToolConfig, "auth_mode" | "roles" | "oauth">>,
): Promise<ToolConfig> {
  const res = await fetch(`${API_BASE}/api/governance/tools/${encodeURIComponent(toolKey)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update tool config: ${res.status}`);
  const data = await res.json();
  return data.tool;
}

export async function resolveEffectiveRole(email: string, tool: string): Promise<EffectiveRole> {
  const res = await fetch(
    `${API_BASE}/api/governance/resolve/${encodeURIComponent(email)}/${encodeURIComponent(tool)}`,
  );
  if (!res.ok) throw new Error(`Failed to resolve role: ${res.status}`);
  return res.json();
}

// ── Client-side role resolution (for admin pages with preloaded data) ─────

const TOOL_ROLE_PRIORITY: Record<string, number> = {
  Admin: 3,
  Analyst: 2,
  "Read-only": 1,
  Support: 1,
};

export function getEffectiveRole(
  user: User,
  teams: Team[],
  toolKey: string,
): EffectiveRole {
  // 1. Direct user-level assignment
  const direct = user.tool_assignments?.[toolKey];
  if (direct?.role) return { role: direct.role, source: "direct" };

  // 2. Team defaults — highest-privilege team wins
  let best: EffectiveRole | null = null;
  for (const team of teams) {
    if (!team.members.includes(user.email)) continue;
    const td = team.tool_defaults[toolKey];
    if (!td?.role) continue;
    const priority = TOOL_ROLE_PRIORITY[td.role] ?? 0;
    if (!best || priority > (TOOL_ROLE_PRIORITY[best.role ?? ""] ?? 0)) {
      best = { role: td.role, source: team.name };
    }
  }
  if (best) return best;

  // 3. No access
  return { role: null, source: "none" };
}

export function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
