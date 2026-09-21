/**
 * Asset Library API client — typed fetch for the owner-scoped list.
 *
 * Accepts a query object so a later search filter can slot in without a
 * rewrite. v1 sends none and does not filter.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type AssetListQuery = {
  q?: string;
};

export interface Asset {
  file_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
  conversation_id: string | null;
  source: string;
  status?: string;
  reason?: string | null;
}

export interface AssetListResponse {
  assets: Asset[];
  query: AssetListQuery;
}

export async function fetchAssets(
  query: AssetListQuery = {},
): Promise<AssetListResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/assets${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Failed to fetch assets: ${res.status}`);
  return res.json();
}
