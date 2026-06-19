/**
 * Claude MAX usage monitoring API client — typed fetch wrappers for usage stats, auth, and logout.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface UsageBucket {
  label: string;
  utilization: number; // 0.0 to 1.0
  reset: number; // Unix timestamp (seconds)
  status: "allowed" | "rate_limited" | string;
}

export interface UsageStats {
  session: UsageBucket;
  weekly: UsageBucket;
  weekly_sonnet: UsageBucket;
  overage: UsageBucket;
  representative_claim: string;
  fallback_percentage: number;
  overall_status: string;
  overall_reset: number;
  fetched_at: string;
}

export interface AuthInfo {
  loggedIn: boolean;
  authMethod?: string;
  apiProvider?: string;
  email?: string;
  orgId?: string;
  orgName?: string | null;
  subscriptionType?: string | null;
  tokenExpiresAt?: number;
  error?: string;
}

export interface HealthStatus {
  auth: AuthInfo;
  token_valid: boolean;
  token_expires_at: number | null;
  usage: UsageStats | null;
  usage_ok: boolean;
  healthy: boolean;
}

// ── Fetchers ──────────────────────────────────────────────────────────────

export async function fetchUsageStats(): Promise<UsageStats> {
  const res = await fetch(`${API_BASE}/api/usage/stats`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch usage stats: ${res.status}`);
  }
  return res.json();
}

export async function fetchAuthInfo(): Promise<AuthInfo> {
  const res = await fetch(`${API_BASE}/api/usage/auth-info`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch auth info: ${res.status}`);
  }
  return res.json();
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_BASE}/api/usage/health`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch health: ${res.status}`);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/usage/logout`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Logout failed: ${res.status}`);
  }
}
