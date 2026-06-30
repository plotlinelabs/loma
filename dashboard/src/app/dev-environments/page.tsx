"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteDevEnvironment,
  fetchDevEnvironments,
  saveDevEnvironment,
  type DevEnvironment,
  type DevEnvFile,
} from "../../lib/dev-environments-api";
import { useUser } from "../../lib/UserContext";

const EMPTY_ENV: DevEnvironment = {
  environment_id: "app-dev",
  name: "App Dev",
  repo: "owner/repo",
  default_branch: "main",
  worktree_base_path: "/var/lib/loma/worktrees/app",
  service_commands: [
    "cd apps/api-go && make dev",
    "cd apps/dashboard && npm run dev",
  ],
  health_urls: ["http://127.0.0.1:3000", "http://127.0.0.1:4002/health"],
  env_files: [
    { path: "apps/dashboard/.env.local" },
    { path: "apps/api-go/.env" },
    { path: "apps/server/.env" },
    { path: "apps/mcp/.env" },
  ],
  browser_auth: {
    login_url: "http://127.0.0.1:3000/login",
    success_url_contains: "/dashboard",
    allowed_domains: ["127.0.0.1:3000", "localhost:3000"],
  },
};

function lines(values: string[]): string {
  return values.join("\n");
}

function parseLines(value: string): string[] {
  return value.split("\n").map((v) => v.trim()).filter(Boolean);
}

function formatDate(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString();
}

export default function DevEnvironmentsPage() {
  const { hasRole } = useUser();
  const canManage = hasRole("maintainer");
  const [environments, setEnvironments] = useState<DevEnvironment[]>([]);
  const [form, setForm] = useState<DevEnvironment>(EMPTY_ENV);
  const [serviceCommands, setServiceCommands] = useState(lines(EMPTY_ENV.service_commands));
  const [healthUrls, setHealthUrls] = useState(lines(EMPTY_ENV.health_urls));
  const [allowedDomains, setAllowedDomains] = useState(lines(EMPTY_ENV.browser_auth.allowed_domains));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setEnvironments(await fetchDevEnvironments());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dev environments");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (canManage) load();
  }, [canManage, load]);

  const selected = useMemo(
    () => environments.find((env) => env.environment_id === form.environment_id),
    [environments, form.environment_id],
  );

  const updateEnvFile = (index: number, patch: Partial<DevEnvFile>) => {
    setForm((prev) => {
      const next = [...prev.env_files];
      next[index] = { ...next[index], ...patch };
      return { ...prev, env_files: next };
    });
  };

  const editExisting = (env: DevEnvironment) => {
    setForm({
      ...env,
      env_files: env.env_files.map((item) => ({ path: item.path, configured: item.configured, updated_at: item.updated_at })),
      browser_auth: { ...env.browser_auth, username: "", password: "" },
    });
    setServiceCommands(lines(env.service_commands));
    setHealthUrls(lines(env.health_urls));
    setAllowedDomains(lines(env.browser_auth.allowed_domains));
  };

  const resetForm = () => {
    setForm(EMPTY_ENV);
    setServiceCommands(lines(EMPTY_ENV.service_commands));
    setHealthUrls(lines(EMPTY_ENV.health_urls));
    setAllowedDomains(lines(EMPTY_ENV.browser_auth.allowed_domains));
  };

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload: DevEnvironment = {
        ...form,
        service_commands: parseLines(serviceCommands),
        health_urls: parseLines(healthUrls),
        env_files: form.env_files
          .filter((item) => item.path.trim())
          .map((item) => {
            const next: DevEnvFile = { path: item.path.trim() };
            if (typeof item.content === "string" && item.content.length > 0) next.content = item.content;
            return next;
          }),
        browser_auth: {
          ...form.browser_auth,
          allowed_domains: parseLines(allowedDomains),
        },
      };
      if (!payload.browser_auth.username) delete payload.browser_auth.username;
      if (!payload.browser_auth.password) delete payload.browser_auth.password;
      const saved = await saveDevEnvironment(payload);
      setEnvironments((prev) => [saved, ...prev.filter((env) => env.environment_id !== saved.environment_id)]);
      editExisting(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save dev environment");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (environmentId: string) => {
    if (!confirm(`Delete ${environmentId}? Stored env files and browser credentials will be removed.`)) return;
    await deleteDevEnvironment(environmentId);
    setEnvironments((prev) => prev.filter((env) => env.environment_id !== environmentId));
    if (form.environment_id === environmentId) resetForm();
  };

  if (!canManage) {
    return <div className="p-8 text-sm text-gray-500">Maintainer access required.</div>;
  }

  return (
    <div className="min-h-screen bg-background text-gray-900">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Dev Environments</h1>
          <p className="text-sm text-gray-500 mt-1">
            Store encrypted env bundles and browser login profiles for closed-loop coding tasks.
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-6">
          <div className="rounded-xl border border-gray-200 bg-surface p-4 h-fit">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Saved environments</h2>
              <button onClick={resetForm} className="text-xs text-accent-600 hover:text-accent-700">
                New
              </button>
            </div>
            {loading ? (
              <div className="text-sm text-gray-400">Loading...</div>
            ) : environments.length === 0 ? (
              <div className="text-sm text-gray-400">No dev environments yet.</div>
            ) : (
              <div className="space-y-2">
                {environments.map((env) => (
                  <button
                    key={env.environment_id}
                    onClick={() => editExisting(env)}
                    className={`w-full text-left rounded-lg border p-3 transition-colors ${
                      form.environment_id === env.environment_id ? "border-accent-300 bg-accent-50" : "border-gray-200 hover:bg-gray-50"
                    }`}
                  >
                    <div className="font-medium text-sm">{env.name}</div>
                    <div className="text-xs text-gray-500 mt-1">{env.repo}</div>
                    <div className="text-[11px] text-gray-400 mt-2">
                      {env.env_files.filter((f) => f.configured).length} env files · browser auth{" "}
                      {env.browser_auth.password_configured ? "configured" : "missing"}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-gray-200 bg-surface p-5 space-y-6">
            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Repository</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.environment_id} onChange={(e) => setForm({ ...form, environment_id: e.target.value })} placeholder="environment id" />
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Name" />
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} placeholder="owner/repo" />
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.default_branch} onChange={(e) => setForm({ ...form, default_branch: e.target.value })} placeholder="main" />
                <input className="md:col-span-2 border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.worktree_base_path} onChange={(e) => setForm({ ...form, worktree_base_path: e.target.value })} placeholder="/var/lib/loma/worktrees/repo" />
              </div>
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Env files</h2>
              <div className="space-y-3">
                {form.env_files.map((item, index) => (
                  <div key={index} className="rounded-lg border border-gray-200 p-3 space-y-2">
                    <div className="flex gap-2">
                      <input className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono" value={item.path} onChange={(e) => updateEnvFile(index, { path: e.target.value })} placeholder="apps/dashboard/.env.local" />
                      <button onClick={() => setForm((prev) => ({ ...prev, env_files: prev.env_files.filter((_, i) => i !== index) }))} className="text-xs px-3 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50">
                        Remove
                      </button>
                    </div>
                    <textarea className="w-full min-h-24 border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono" value={item.content || ""} onChange={(e) => updateEnvFile(index, { content: e.target.value })} placeholder={item.configured ? "Configured. Paste a replacement to rotate." : "Paste env file contents"} />
                    <div className="text-[11px] text-gray-400">
                      {item.configured ? `Configured${item.updated_at ? ` · ${formatDate(item.updated_at)}` : ""}` : "Not configured"}
                    </div>
                  </div>
                ))}
              </div>
              <button onClick={() => setForm((prev) => ({ ...prev, env_files: [...prev.env_files, { path: "" }] }))} className="text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">
                Add env file
              </button>
            </section>

            <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <h2 className="text-sm font-semibold mb-2">Service commands</h2>
                <textarea className="w-full min-h-28 border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono" value={serviceCommands} onChange={(e) => setServiceCommands(e.target.value)} />
              </div>
              <div>
                <h2 className="text-sm font-semibold mb-2">Health URLs</h2>
                <textarea className="w-full min-h-28 border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono" value={healthUrls} onChange={(e) => setHealthUrls(e.target.value)} />
              </div>
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Browser login profile</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.browser_auth.login_url} onChange={(e) => setForm({ ...form, browser_auth: { ...form.browser_auth, login_url: e.target.value } })} placeholder="http://127.0.0.1:3000/login" />
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.browser_auth.success_url_contains} onChange={(e) => setForm({ ...form, browser_auth: { ...form.browser_auth, success_url_contains: e.target.value } })} placeholder="/dashboard" />
                <input className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.browser_auth.username || ""} onChange={(e) => setForm({ ...form, browser_auth: { ...form.browser_auth, username: e.target.value } })} placeholder={form.browser_auth.username_configured ? "Username configured. Enter replacement." : "Username"} />
                <input type="password" className="border border-gray-200 rounded-lg px-3 py-2 text-sm" value={form.browser_auth.password || ""} onChange={(e) => setForm({ ...form, browser_auth: { ...form.browser_auth, password: e.target.value } })} placeholder={form.browser_auth.password_configured ? "Password configured. Enter replacement." : "Password"} />
                <textarea className="md:col-span-2 min-h-20 border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono" value={allowedDomains} onChange={(e) => setAllowedDomains(e.target.value)} placeholder="Allowed domains, one per line" />
              </div>
            </section>

            <div className="flex items-center gap-3 pt-2">
              <button onClick={submit} disabled={saving} className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50">
                {saving ? "Saving..." : "Save environment"}
              </button>
              {selected && (
                <button onClick={() => remove(selected.environment_id)} className="px-4 py-2 rounded-lg border border-red-200 text-red-600 text-sm hover:bg-red-50">
                  Delete
                </button>
              )}
              <div className="text-xs text-gray-400">
                Secrets are encrypted and never shown after save.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
