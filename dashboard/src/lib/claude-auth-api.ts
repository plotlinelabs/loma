/**
 * API client for per-user Claude Code authentication.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export interface ClaudeAuthStatus {
  connected: boolean;
  email?: string;
  authMethod?: string;
  has_warm_client?: boolean;
}

export async function fetchClaudeAuthStatus(): Promise<ClaudeAuthStatus> {
  const res = await fetch(`${API_BASE}/api/claude-auth/status`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch claude auth status: ${res.status}`);
  }
  return res.json();
}

export interface ClaudeLoginSession {
  id: string;
  state: "starting" | "waiting" | "connected" | "failed" | "cancelled";
  url: string | null;
  error: string | null;
  expires_at: number;
}

export async function claudeLoginRequest(path = "", method = "GET", code?: string): Promise<ClaudeLoginSession> {
  const res = await fetch(`${API_BASE}/api/claude-auth/login${path}`, {
    method,
    headers: code === undefined ? undefined : { "Content-Type": "application/json" },
    body: code === undefined ? undefined : JSON.stringify({ code }),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Login session ended. Please start again.");
  return data;
}

export async function disconnectClaude(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/claude-auth/disconnect`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Disconnect failed: ${res.status}`);
  }
}

export interface ClaudeTestResult {
  ok: boolean;
  email: string;
  response?: string;
  error?: string;
  auth_error?: boolean;
  duration_ms?: number;
}

/** Admin-only: run a tiny test chat against a specific user's Claude account. */
export async function testClaudeAccount(email: string): Promise<ClaudeTestResult> {
  const res = await fetch(`${API_BASE}/api/claude-auth/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const data = await res.json().catch(() => null);
  if (!data) {
    throw new Error(`Claude test failed: ${res.status}`);
  }
  return data as ClaudeTestResult;
}
