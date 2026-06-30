/**
 * OAuth API client — typed fetch wrappers for personal OAuth integrations.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

async function apiError(res: Response, fallback: string): Promise<Error> {
  try {
    const data = await res.json();
    if (typeof data?.error === "string" && data.error.trim()) {
      return new Error(data.error);
    }
  } catch {
    // Fall through to the generic status message.
  }
  return new Error(`${fallback}: ${res.status}`);
}

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
  if (!res.ok) throw await apiError(res, "Failed to fetch OAuth connections");
  const data = await res.json();
  return data.connections;
}

export async function getGoogleAuthorizeUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/oauth/google/authorize`);
  if (!res.ok) throw await apiError(res, "Failed to get Google authorize URL");
  const data = await res.json();
  return data.authorize_url;
}

export async function disconnectGoogle(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/oauth/connections/google`, {
    method: "DELETE",
  });
  if (!res.ok) throw await apiError(res, "Failed to disconnect Google");
}

// ── Slack ──────────────────────────────────────────────────────────────────

export async function getSlackAuthorizeUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/oauth/slack/authorize`);
  if (!res.ok) throw await apiError(res, "Failed to get Slack authorize URL");
  const data = await res.json();
  return data.authorize_url;
}

export async function disconnectSlack(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/oauth/connections/slack`, {
    method: "DELETE",
  });
  if (!res.ok) throw await apiError(res, "Failed to disconnect Slack");
}

// ── Custom MCP OAuth ─────────────────────────────────────────────────────

export async function getCustomMcpAuthorizeUrl(provider: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/oauth/custom-mcp/${provider}/authorize`);
  if (!res.ok) throw await apiError(res, "Failed to get authorize URL");
  const data = await res.json();
  return data.authorize_url;
}

export async function disconnectCustomMcp(provider: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/oauth/connections/custom-mcp/${provider}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await apiError(res, "Failed to disconnect");
}
