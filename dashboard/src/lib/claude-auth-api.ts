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

export async function getClaudeLoginTerminalToken(): Promise<{ token: string; autoCommand: string }> {
  const res = await fetch(`${API_BASE}/api/claude-auth/terminal-token`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to get terminal token: ${res.status}`);
  }
  return res.json();
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
