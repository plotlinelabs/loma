const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type BillingStatus = "correctly_linked" | "contract_missing" | "invalid_contract" | "inactive_contract" | "account_mismatch" | "account_not_linked";
export interface BillingContract { id: string; name: string; status: string; accountId: string; legalEntityId?: string; startDate?: string; url?: string; }
export interface BillingProduct { id: string; name: string; billingId?: string; status: BillingStatus; contract?: BillingContract; }
export interface BillingOrganization { id: string; name: string; isBlocked: boolean; monetizeNowAccountId?: string; products: BillingProduct[]; summary: Record<BillingStatus, number>; }
export interface BillingPagination { page: number; pageSize: number; total: number; hasNext: boolean; }
export interface BillingMappingsResponse { organizations: BillingOrganization[]; pagination: BillingPagination; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}
export async function fetchBillingMappings(page = 1, pageSize = 25): Promise<BillingMappingsResponse> { return request<BillingMappingsResponse>(`/api/billing-mappings?page=${page}&pageSize=${pageSize}`); }
export async function setBillingAccount(organizationId: string, accountId: string) { return request(`/api/billing-mappings/organizations/${organizationId}/account`, { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify({accountId}) }); }
export async function fetchActiveContracts(organizationId: string): Promise<BillingContract[]> { return (await request<{contracts: BillingContract[]}>(`/api/billing-mappings/organizations/${organizationId}/contracts`)).contracts; }
export async function setProductContract(productId: string, contractId: string) { return request(`/api/billing-mappings/products/${productId}/contract`, { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify({contractId}) }); }
