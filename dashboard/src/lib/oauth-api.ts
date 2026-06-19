/**
 * OAuth API client — typed fetch wrappers for personal OAuth integrations.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface OAuthConnection {
  provider: string;
  status: "connected" | "expired" | "not_connected";
  scopes: string[];
  connected_at?: string | null;
  updated_at?: string | null;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function fetchOAuthConnections(): Promise<OAuthConnection[]> {
  const res = await fetch(`${API_BASE}/api/oauth/connections`);
  if (!res.ok) throw new Error(`Failed to fetch OAuth connections: ${res.status}`);
  const data = await res.json();
  return data.connections;
}

export async function getGoogleAuthorizeUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/oauth/google/authorize`);
  if (!res.ok) throw new Error(`Failed to get Google authorize URL: ${res.status}`);
  const data = await res.json();
  return data.authorize_url;
}

export async function disconnectGoogle(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/oauth/connections/google`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to disconnect Google: ${res.status}`);
}

// ── Slack ──────────────────────────────────────────────────────────────────

export async function getSlackAuthorizeUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/oauth/slack/authorize`);
  if (!res.ok) throw new Error(`Failed to get Slack authorize URL: ${res.status}`);
  const data = await res.json();
  return data.authorize_url;
}

export async function disconnectSlack(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/oauth/connections/slack`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to disconnect Slack: ${res.status}`);
}
