/**
 * Billing mapping API client — org-level MonetizeNow account linking.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export type MappingStatus = "linked" | "auto_matched" | "not_found";

export interface MnAccount {
  id: string;
  name: string;
  custom_id?: string | null;
}

export interface OrgMapping {
  org_id: string;
  org_name: string;
  is_blocked: boolean;
  products_count: number;
  has_billing_id: boolean;
  status: MappingStatus;
  account: MnAccount | null;
  linked_by?: string | null;
}

export interface BillingMappingResponse {
  organisations: OrgMapping[];
  accounts_error: string | null;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function fetchBillingMapping(): Promise<BillingMappingResponse> {
  const res = await fetch(`${API_BASE}/api/billing/mapping`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to fetch billing mapping: ${res.status}`);
  }
  return res.json();
}

export async function searchMnAccounts(query: string): Promise<MnAccount[]> {
  const res = await fetch(`${API_BASE}/api/billing/accounts?q=${encodeURIComponent(query)}`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to search accounts: ${res.status}`);
  }
  const data = await res.json();
  return data.accounts;
}

export async function linkOrg(
  orgId: string,
  orgName: string,
  account: MnAccount,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/billing/mapping/${encodeURIComponent(orgId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: account.id, account_name: account.name, org_name: orgName }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to link org: ${res.status}`);
  }
}

export async function unlinkOrg(orgId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/billing/mapping/${encodeURIComponent(orgId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to unlink org: ${res.status}`);
  }
}
