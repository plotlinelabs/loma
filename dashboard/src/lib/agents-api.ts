/**
 * Agent identities API client — user-created, shareable agent personas.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type AgentVisibility = "private" | "workspace";
export type AgentMotif = "round" | "square" | "halo" | "antenna";

export interface AgentAvatarSpec {
  seed: number;
  motif: AgentMotif;
}

export interface AgentIdentity {
  agent_id: string;
  name: string;
  description: string;
  identity_prompt: string;
  skills: string[];
  tools: string[];
  auth_mode: "requester";
  visibility: AgentVisibility;
  default_model: string | null;
  avatar: AgentAvatarSpec;
  status: "active" | "disabled";
  created_by: string;
  created_at?: string;
  updated_at?: string;
  conversation_count?: number;
}

export interface AgentIdentityInput {
  name?: string;
  description?: string;
  identity_prompt?: string;
  skills?: string[];
  tools?: string[];
  visibility?: AgentVisibility;
  default_model?: string | null;
  avatar?: AgentAvatarSpec;
  status?: "active" | "disabled";
}

async function parseError(res: Response, fallback: string): Promise<never> {
  const err = await res.json().catch(() => ({}));
  throw new Error(err.error || `${fallback}: ${res.status}`);
}

export async function fetchAgentIdentities(): Promise<{ agents: AgentIdentity[] }> {
  const res = await fetch(`${API_BASE}/api/agent-identities`);
  if (!res.ok) return parseError(res, "Failed to fetch agents");
  return res.json();
}

export async function createAgentIdentity(input: AgentIdentityInput): Promise<AgentIdentity> {
  const res = await fetch(`${API_BASE}/api/agent-identities`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return parseError(res, "Failed to create agent");
  const data = await res.json();
  return data.agent;
}

export async function updateAgentIdentity(
  agentId: string,
  input: AgentIdentityInput,
): Promise<AgentIdentity> {
  const res = await fetch(`${API_BASE}/api/agent-identities/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return parseError(res, "Failed to update agent");
  const data = await res.json();
  return data.agent;
}

export async function deleteAgentIdentity(agentId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/agent-identities/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
  if (!res.ok) return parseError(res, "Failed to delete agent");
}
