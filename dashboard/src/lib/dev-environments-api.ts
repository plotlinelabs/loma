const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export interface DevEnvFile {
  path: string;
  content?: string;
  configured?: boolean;
  updated_at?: string | null;
}

export interface BrowserAuthProfile {
  login_url: string;
  username?: string;
  password?: string;
  username_configured?: boolean;
  password_configured?: boolean;
  success_url_contains: string;
  allowed_domains: string[];
  updated_at?: string | null;
}

export interface DevEnvironment {
  environment_id: string;
  name: string;
  repo: string;
  default_branch: string;
  worktree_base_path: string;
  service_commands: string[];
  health_urls: string[];
  env_files: DevEnvFile[];
  browser_auth: BrowserAuthProfile;
  created_by?: string | null;
  created_at?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

async function parseError(res: Response, fallback: string): Promise<Error> {
  const data = await res.json().catch(() => ({}));
  return new Error(data.error || `${fallback}: ${res.status}`);
}

export async function fetchDevEnvironments(): Promise<DevEnvironment[]> {
  const res = await fetch(`${API_BASE}/api/dev-environments`);
  if (!res.ok) throw await parseError(res, "Failed to fetch dev environments");
  const data = await res.json();
  return data.environments;
}

export async function saveDevEnvironment(env: DevEnvironment): Promise<DevEnvironment> {
  const res = await fetch(`${API_BASE}/api/dev-environments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(env),
  });
  if (!res.ok) throw await parseError(res, "Failed to save dev environment");
  const data = await res.json();
  return data.environment;
}

export async function deleteDevEnvironment(environmentId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/dev-environments/${encodeURIComponent(environmentId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await parseError(res, "Failed to delete dev environment");
}
