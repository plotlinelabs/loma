/**
 * Environment variables API client — typed fetch wrappers for .env management.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface EnvVariable {
  key: string;
  value: string;
  is_sensitive: boolean;
  is_readonly: boolean;
  masked: boolean;
}

export interface EnvAuditChange {
  key: string;
  type: "added" | "modified" | "deleted";
  old_preview: string | null;
  new_preview: string | null;
}

export interface EnvAuditEntry {
  action: "update" | "reveal";
  user_email: string;
  timestamp: string;
  changes?: EnvAuditChange[];
  revealed_key?: string;
}

export interface EnvUpdateVariable {
  key: string;
  value?: string;
  action: "set" | "delete";
}

export interface EnvUpdateResponse {
  success: boolean;
  changes_applied: number;
  changes: EnvAuditChange[];
  restart_recommended: boolean;
}

// ── Fetchers ──────────────────────────────────────────────────────────────

export async function fetchEnvVariables(): Promise<EnvVariable[]> {
  const res = await fetch(`${API_BASE}/api/env`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch env vars: ${res.status}`);
  }
  const data = await res.json();
  return data.variables;
}

export async function revealEnvValue(key: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/env/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to reveal value: ${res.status}`);
  }
  const data = await res.json();
  return data.value;
}

export async function updateEnvVariables(
  variables: EnvUpdateVariable[],
): Promise<EnvUpdateResponse> {
  const res = await fetch(`${API_BASE}/api/env`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ variables }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to update env vars: ${res.status}`);
  }
  return res.json();
}

export async function fetchEnvAuditLog(limit = 50): Promise<EnvAuditEntry[]> {
  const res = await fetch(`${API_BASE}/api/env/audit?limit=${limit}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch audit log: ${res.status}`);
  }
  const data = await res.json();
  return data.logs;
}

export async function toggleSensitive(
  key: string,
  sensitive: boolean,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/env/sensitive`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, sensitive }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to toggle sensitive: ${res.status}`);
  }
}

export async function restartService(): Promise<void> {
  await fetch(`${API_BASE}/api/env/restart`, { method: "POST" });
  // The server will restart, so the response may not arrive — that's expected.
}
