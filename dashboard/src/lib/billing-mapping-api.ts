const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type BillingStatus =
  | "correctly_linked"
  | "contract_missing"
  | "invalid_contract"
  | "inactive_contract"
  | "account_mismatch"
  | "unknown";

export interface BillingContract {
  id: string;
  name: string;
  status: string;
  accountId: string;
  accountName?: string | null;
  legalEntityId?: string;
  startDate?: string;
  url?: string | null;
}

export interface BillingProduct {
  id: string;
  name: string;
  billingId?: string | null;
  status: BillingStatus;
  contract?: BillingContract | null;
  /** Other organizations pointing at the same contract. */
  sharedWithOrganizations: string[];
}

export interface BillingOrganization {
  id: string;
  name: string;
  dashboardDisabled: boolean;
  accountId?: string | null;
  accountName?: string | null;
  /** Where accountId came from: an operator override, the org's own contracts, or nothing. */
  accountSource: "override" | "derived" | "none";
  danglingProductRefs: number;
  products: BillingProduct[];
  summary: Record<BillingStatus, number>;
}

export interface BillingPagination {
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
}

export interface BillingMappingsResponse {
  organizations: BillingOrganization[];
  pagination: BillingPagination;
  totals: Record<BillingStatus, number>;
  generatedAt: string;
  upstreamFailures: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error || `Request failed: ${response.status}`);
  }
  return data;
}

export async function fetchBillingMappings(
  page = 1,
  pageSize = 25,
  status = "all",
  search = "",
  refresh = false,
): Promise<BillingMappingsResponse> {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (status !== "all") query.set("status", status);
  if (search) query.set("q", search);
  if (refresh) query.set("refresh", "1");
  return request<BillingMappingsResponse>(`/api/billing-mappings?${query}`);
}

export async function setBillingAccount(organizationId: string, accountId: string) {
  return request(`/api/billing-mappings/organizations/${organizationId}/account`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountId }),
  });
}

export async function fetchActiveContracts(organizationId: string): Promise<BillingContract[]> {
  const result = await request<{ contracts: BillingContract[] }>(
    `/api/billing-mappings/organizations/${organizationId}/contracts`,
  );
  return result.contracts;
}

export async function setProductContract(productId: string, contractId: string) {
  return request(`/api/billing-mappings/products/${productId}/contract`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contractId }),
  });
}
