/**
 * API Keys client — personal bearer keys for external MCP/API access
 * (e.g. connecting an external agent to the loma-tasks MCP server).
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface ApiKeyRecord {
  key_id: string;
  name: string;
  key_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
}

// ── Fetchers ──────────────────────────────────────────────────────────────

export async function fetchApiKeys(): Promise<ApiKeyRecord[]> {
  const res = await fetch(`${API_BASE}/api/api-keys`);
  if (!res.ok) throw new Error(`Failed to fetch API keys: ${res.status}`);
  const data = await res.json();
  return data.keys ?? [];
}

export async function createApiKey(
  name: string,
): Promise<{ key: string; record: ApiKeyRecord }> {
  const res = await fetch(`${API_BASE}/api/api-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Failed to create API key: ${res.status}`);
  return data;
}

export async function revokeApiKey(keyId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/api-keys/${keyId}`, { method: "DELETE" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to revoke API key: ${res.status}`);
  }
}
