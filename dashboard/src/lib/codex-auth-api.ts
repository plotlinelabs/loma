/**
 * API client for per-user Codex (ChatGPT subscription) authentication.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export interface CodexAuthStatus {
  connected: boolean;
  email?: string;
  authMethod?: string;
  plan?: string;
  pool_enabled?: boolean;
}

export async function fetchCodexAuthStatus(): Promise<CodexAuthStatus> {
  const res = await fetch(`${API_BASE}/api/codex-auth/status`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch codex auth status: ${res.status}`);
  }
  return res.json();
}

export async function getCodexLoginTerminalToken(): Promise<{ token: string }> {
  const res = await fetch(`${API_BASE}/api/codex-auth/terminal-token`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to get terminal token: ${res.status}`);
  }
  return res.json();
}

export async function disconnectCodex(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/codex-auth/disconnect`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Disconnect failed: ${res.status}`);
  }
}
