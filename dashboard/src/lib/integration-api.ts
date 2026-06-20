/**
 * Integration API client — typed fetch wrappers for org-level integrations.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface ExtraFieldDef {
  key: string;
  label: string;
  placeholder?: string;
  required?: boolean;
}

export interface Integration {
  provider: string;
  display_name: string;
  description: string;
  auth_type: string;
  auth_label: string;
  auth_help_url: string;
  has_webhook: boolean;
  webhook_secret_label?: string | null;
  extra_fields?: ExtraFieldDef[];
  status: "connected" | "not_connected" | "system_managed";
  connected_by?: string | null;
  connected_at?: string | null;
  has_webhook_secret?: boolean;
  // Custom (admin-added) remote MCP connectors
  is_custom?: boolean;
  url?: string;
  has_token?: boolean;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function fetchIntegrations(): Promise<Integration[]> {
  const res = await fetch(`${API_BASE}/api/integrations`);
  if (!res.ok) throw new Error(`Failed to fetch integrations: ${res.status}`);
  return res.json();
}

export async function connectIntegration(
  provider: string,
  apiKey: string,
  webhookSecret?: string,
  extraFields?: Record<string, string>,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/integrations/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider,
      api_key: apiKey,
      webhook_secret: webhookSecret || "",
      extra_fields: extraFields || {},
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to connect ${provider}: ${res.status}`);
  }
}

export async function disconnectIntegration(provider: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/integrations/${provider}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to disconnect ${provider}: ${res.status}`);
  }
}

export async function addCustomConnector(input: {
  name: string;
  url: string;
  token?: string;
  authHeader?: string;
}): Promise<void> {
  const res = await fetch(`${API_BASE}/api/integrations/custom`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      url: input.url,
      token: input.token || "",
      auth_header: input.authHeader || "",
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to add connector: ${res.status}`);
  }
}

export async function removeCustomConnector(provider: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/integrations/custom/${provider}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to remove connector: ${res.status}`);
  }
}

export async function getWebhookUrl(provider: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/integrations/${provider}/webhook-url`);
  if (!res.ok) throw new Error(`Failed to get webhook URL: ${res.status}`);
  const data = await res.json();
  return data.webhook_url;
}
