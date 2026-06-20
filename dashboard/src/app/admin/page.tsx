"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getToolMeta, CATEGORIES } from "../mcp/tool-meta";
import { TOOL_LOGOS } from "../mcp/tool-logos";
import {
  fetchUsers,
  fetchTeams,
  fetchToolConfigs,
  updateUser,
  getEffectiveRole,
  type User,
  type Team,
  type ToolConfig,
  type SystemRole,
} from "../../lib/governance-api";
import {
  fetchEnvVariables,
  revealEnvValue,
  updateEnvVariables,
  fetchEnvAuditLog,
  toggleSensitive,
  restartService,
  type EnvVariable,
  type EnvAuditEntry,
  type EnvUpdateVariable,
  type EnvAuditChange,
} from "../../lib/env-api";
import {
  fetchPromptSettings,
  updatePromptSetting,
  type PromptSetting,
  type PromptSettingKey,
} from "../../lib/prompt-settings-api";
import UsagePanel from "../usage/UsagePanel";
import dynamic from "next/dynamic";
import { useUser } from "../../lib/UserContext";

const WebTerminal = dynamic(() => import("../../components/WebTerminal"), { ssr: false });

/* ── Helpers ──────────────────────────────────────────────────────── */

const ALL_TOOLS = CATEGORIES.flatMap((c) => c.keys);

const ROLE_PERMISSIONS: { permission: string; admin: boolean; maintainer: boolean; operator: boolean; analyst: boolean; chatter: boolean }[] = [
  { permission: "Chat with Loma",                  admin: true,  maintainer: true,  operator: true,  analyst: true,  chatter: true },
  { permission: "View own conversations",           admin: true,  maintainer: true,  operator: true,  analyst: true,  chatter: true },
  { permission: "View all conversations",           admin: true,  maintainer: false, operator: false, analyst: false, chatter: false },
  { permission: "View flow-spawned conversations",  admin: true,  maintainer: true,  operator: true,  analyst: true,  chatter: false },
  { permission: "Create & edit flows / tasks",      admin: true,  maintainer: true,  operator: true,  analyst: false, chatter: false },
  { permission: "View flow definitions",            admin: true,  maintainer: true,  operator: true,  analyst: true,  chatter: false },
  { permission: "View analytics, memory & signals", admin: true,  maintainer: true,  operator: true,  analyst: true,  chatter: false },
  { permission: "Manage users & roles",             admin: true,  maintainer: false, operator: false, analyst: false, chatter: false },
  { permission: "Manage tool configurations",       admin: true,  maintainer: true,  operator: false, analyst: false, chatter: false },
  { permission: "Manage environment variables",     admin: true,  maintainer: true,  operator: false, analyst: false, chatter: false },
  { permission: "Manage usage & login",             admin: true,  maintainer: true,  operator: false, analyst: false, chatter: false },
];

const ROLE_META: Record<string, { label: string; color: string; bg: string; description: string }> = {
  admin:      { label: "Admin",      color: "text-red-700",    bg: "bg-red-50",    description: "Full access to everything" },
  maintainer: { label: "Maintainer", color: "text-purple-700", bg: "bg-purple-50", description: "Admin access except user management & all conversations" },
  operator:   { label: "Operator",   color: "text-blue-700",   bg: "bg-blue-50",   description: "Chat + create flows & tasks" },
  analyst:    { label: "Analyst",    color: "text-amber-700",  bg: "bg-amber-50",  description: "Chat + read-only dashboards" },
  chatter:    { label: "Chatter",    color: "text-gray-600",   bg: "bg-gray-50",   description: "Chat only" },
};

const AI_PROVIDER_CARDS = [
  {
    key: "OPENCODE_API_KEY",
    name: "OpenCode Go",
    description: "Enables OpenCode Go models in Dashboard Chat.",
    models: "OpenCode Go models",
  },
  {
    key: "OPENAI_API_KEY",
    name: "OpenAI",
    description: "Enables GPT models through the OpenCode runtime.",
    models: "GPT series",
  },
] as const;

const ALLOWED_EMAIL_DOMAINS_KEY = "ALLOWED_EMAIL_DOMAINS";

function StatusCell({ role, source, oauthStatus, authMode }: {
  role: string | null;
  source?: string;
  oauthStatus: "connected" | "expired" | "not_connected" | null;
  authMode: "loma-managed" | "tool-managed";
}) {
  if (authMode === "tool-managed") {
    if (oauthStatus === "connected")
      return <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Connected</span>;
    if (oauthStatus === "expired")
      return <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Expired</span>;
    return <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Not connected</span>;
  }

  // Loma-managed — show provenance
  if (role) {
    const isTeam = source && source !== "direct" && source !== "none";
    return (
      <span className="inline-flex flex-col items-center gap-0.5">
        <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">{role}</span>
        {isTeam && (
          <span className="text-[8px] text-gray-400">via {source}</span>
        )}
      </span>
    );
  }
  return <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">No access</span>;
}

/* ── Page ─────────────────────────────────────────────────────────── */

export default function AdminPage() {
  const { isAdmin, hasRole, loading: userLoading } = useUser();
  const isMaintainerOrAbove = hasRole("maintainer");
  const router = useRouter();
  const [tab, setTab] = useState<"users" | "teams" | "environment" | "settings" | "usage">("teams");
  const [showRoles, setShowRoles] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [toolConfigMap, setToolConfigMap] = useState<Record<string, ToolConfig>>({});
  const [loading, setLoading] = useState(true);

  // Environment tab state
  const [envTab, setEnvTab] = useState<"variables" | "audit">("variables");
  const [envVars, setEnvVars] = useState<EnvVariable[]>([]);
  const [editedValues, setEditedValues] = useState<Record<string, string>>({});
  const [newVars, setNewVars] = useState<Array<{ key: string; value: string; sensitive: boolean }>>([]);
  const [deletedKeys, setDeletedKeys] = useState<Set<string>>(new Set());
  const [revealedKeys, setRevealedKeys] = useState<Record<string, string>>({});
  const [envAuditLog, setEnvAuditLog] = useState<EnvAuditEntry[]>([]);
  const [envLoading, setEnvLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [envError, setEnvError] = useState<string | null>(null);
  const [envSuccess, setEnvSuccess] = useState<string | null>(null);
  const [restartWarning, setRestartWarning] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [expandedAudit, setExpandedAudit] = useState<Set<number>>(new Set());
  const [providerInputs, setProviderInputs] = useState<Record<string, string>>({});
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [settingsValues, setSettingsValues] = useState<Record<string, string>>({});
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [promptSettings, setPromptSettings] = useState<PromptSetting[]>([]);
  const [promptDrafts, setPromptDrafts] = useState<Record<string, string>>({});
  const [promptLoading, setPromptLoading] = useState(false);
  const [savingPromptKey, setSavingPromptKey] = useState<PromptSettingKey | null>(null);

  useEffect(() => {
    if (!userLoading && !isMaintainerOrAbove) {
      router.replace("/");
      return;
    }
    if (!userLoading && isMaintainerOrAbove) {
      const fetches: Promise<unknown>[] = [fetchTeams(), fetchToolConfigs()];
      // Only fetch users if admin (maintainers can't see user management)
      if (isAdmin) fetches.unshift(fetchUsers());
      else fetches.unshift(Promise.resolve([]));

      Promise.all(fetches)
        .then(([u, t, tc]) => {
          setUsers(u as User[]);
          setTeams(t as Team[]);
          const map: Record<string, ToolConfig> = {};
          for (const c of tc as ToolConfig[]) map[c.tool_key] = c;
          setToolConfigMap(map);
        })
        .catch((e) => console.error("Failed to load admin data:", e))
        .finally(() => setLoading(false));
    }
  }, [userLoading, isAdmin, isMaintainerOrAbove, router]);

  // Load env data when switching to environment/settings tabs
  const loadEnvVars = async () => {
    setEnvLoading(true);
    setEnvError(null);
    try {
      const vars = await fetchEnvVariables();
      setEnvVars(vars);
      setEditedValues({});
      setNewVars([]);
      setDeletedKeys(new Set());
      setRevealedKeys({});
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : "Failed to load env vars");
    } finally {
      setEnvLoading(false);
    }
  };

  useEffect(() => {
    if ((tab === "environment" || tab === "settings") && envVars.length === 0 && !envLoading) {
      loadEnvVars();
    }
    if (tab === "settings" && promptSettings.length === 0 && !promptLoading) {
      loadPromptSettings();
    }
    if (tab === "environment" && envTab === "audit" && envAuditLog.length === 0) {
      fetchEnvAuditLog().then(setEnvAuditLog).catch(console.error);
    }
  }, [tab, envTab]);

  const loadPromptSettings = async () => {
    setPromptLoading(true);
    setEnvError(null);
    try {
      const settings = await fetchPromptSettings();
      setPromptSettings(settings);
      setPromptDrafts({});
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : "Failed to load prompt settings");
    } finally {
      setPromptLoading(false);
    }
  };

  // Env helpers
  const hasEnvChanges = Object.keys(editedValues).length > 0 || newVars.some((v) => v.key.trim()) || deletedKeys.size > 0;

  const computeDiff = (): EnvAuditChange[] => {
    const changes: EnvAuditChange[] = [];
    for (const [key, newVal] of Object.entries(editedValues)) {
      const original = envVars.find((v) => v.key === key);
      if (original) {
        changes.push({ key, type: "modified", old_preview: original.value, new_preview: newVal });
      }
    }
    for (const nv of newVars) {
      if (nv.key.trim()) {
        changes.push({ key: nv.key, type: "added", old_preview: null, new_preview: nv.value });
      }
    }
    for (const key of deletedKeys) {
      const original = envVars.find((v) => v.key === key);
      changes.push({ key, type: "deleted", old_preview: original?.value ?? "", new_preview: null });
    }
    return changes;
  };

  const handleReveal = async (key: string) => {
    try {
      const value = await revealEnvValue(key);
      setRevealedKeys((prev) => ({ ...prev, [key]: value }));
    } catch (e) {
      console.error("Failed to reveal:", e);
    }
  };

  const handleToggleSensitive = async (key: string, currentlySensitive: boolean) => {
    const newSensitive = !currentlySensitive;
    // Optimistic update
    setEnvVars((prev) => prev.map((v) => v.key === key ? { ...v, is_sensitive: newSensitive, masked: newSensitive, value: newSensitive ? "\u2022\u2022\u2022" : v.value } : v));
    if (newSensitive) {
      // Hide revealed value when marking as sensitive
      setRevealedKeys((prev) => { const next = { ...prev }; delete next[key]; return next; });
    }
    try {
      await toggleSensitive(key, newSensitive);
      // Reload to get properly masked/unmasked values from server
      await loadEnvVars();
    } catch (e) {
      console.error("Failed to toggle sensitive:", e);
      // Revert optimistic update
      setEnvVars((prev) => prev.map((v) => v.key === key ? { ...v, is_sensitive: currentlySensitive, masked: currentlySensitive } : v));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setEnvError(null);
    setEnvSuccess(null);
    try {
      const variables: EnvUpdateVariable[] = [];
      for (const [key, value] of Object.entries(editedValues)) {
        variables.push({ key, value, action: "set" });
      }
      for (const nv of newVars) {
        if (nv.key.trim()) {
          variables.push({ key: nv.key, value: nv.value, action: "set" });
        }
      }
      for (const key of deletedKeys) {
        variables.push({ key, action: "delete" });
      }
      const result = await updateEnvVariables(variables);
      // Mark new vars as sensitive if flagged
      for (const nv of newVars) {
        if (nv.key.trim() && nv.sensitive) {
          await toggleSensitive(nv.key, true);
        }
      }
      setShowDiff(false);
      setEnvSuccess(`${result.changes_applied} change${result.changes_applied !== 1 ? "s" : ""} applied successfully.`);
      setRestartWarning(result.restart_recommended);
      // Refresh
      await loadEnvVars();
      // Refresh audit log if visible
      if (envTab === "audit") {
        fetchEnvAuditLog().then(setEnvAuditLog).catch(console.error);
      }
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : "Failed to save changes");
      setShowDiff(false);
    } finally {
      setSaving(false);
    }
  };

  const getEnvVar = (key: string) => envVars.find((v) => v.key === key);
  const isProviderConnected = (key: string) => Boolean(getEnvVar(key));
  const allowedEmailDomainsValue = settingsValues[ALLOWED_EMAIL_DOMAINS_KEY] ?? getEnvVar(ALLOWED_EMAIL_DOMAINS_KEY)?.value ?? "";
  const hasSettingsChanges = allowedEmailDomainsValue !== (getEnvVar(ALLOWED_EMAIL_DOMAINS_KEY)?.value ?? "");

  const handleAllowedEmailDomainsChange = (value: string) => {
    setSettingsValues((prev) => ({ ...prev, [ALLOWED_EMAIL_DOMAINS_KEY]: value }));
  };

  const handleSettingsSave = async () => {
    const current = getEnvVar(ALLOWED_EMAIL_DOMAINS_KEY)?.value ?? "";
    const nextValue = allowedEmailDomainsValue.trim();
    const variables: EnvUpdateVariable[] = nextValue !== current
      ? [{ key: ALLOWED_EMAIL_DOMAINS_KEY, value: nextValue, action: "set" }]
      : [];

    if (variables.length === 0) return;

    setSettingsSaving(true);
    setEnvError(null);
    setEnvSuccess(null);
    try {
      const result = await updateEnvVariables(variables);
      setEnvSuccess("Allowed email domain configuration saved.");
      setRestartWarning(result.restart_recommended);
      setSettingsValues({});
      await loadEnvVars();
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSettingsSaving(false);
    }
  };

  const getPromptDraft = (setting: PromptSetting) => promptDrafts[setting.setting_key] ?? setting.content ?? "";
  const isPromptChanged = (setting: PromptSetting) => getPromptDraft(setting) !== (setting.content ?? "");
  const canSetPromptDefault = (setting: PromptSetting) => Boolean(setting.default_content) && getPromptDraft(setting) !== setting.default_content;

  const handlePromptSave = async (setting: PromptSetting) => {
    setSavingPromptKey(setting.setting_key);
    setEnvError(null);
    setEnvSuccess(null);
    try {
      const updated = await updatePromptSetting(setting.setting_key, getPromptDraft(setting));
      setPromptSettings((prev) => prev.map((item) => item.setting_key === updated.setting_key ? updated : item));
      setPromptDrafts((prev) => {
        const next = { ...prev };
        delete next[setting.setting_key];
        return next;
      });
      setEnvSuccess(`${updated.title} saved.`);
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : "Failed to save prompt setting");
    } finally {
      setSavingPromptKey(null);
    }
  };

  const handleProviderConnect = async (key: string, label: string) => {
    const value = providerInputs[key]?.trim();
    if (!value) {
      setEnvError(`Paste a ${label} API key first.`);
      return;
    }

    setSavingProvider(key);
    setEnvError(null);
    setEnvSuccess(null);
    try {
      const result = await updateEnvVariables([{ key, value, action: "set" }]);
      await toggleSensitive(key, true);
      setProviderInputs((prev) => ({ ...prev, [key]: "" }));
      setEnvSuccess(`${label} API key connected.`);
      setRestartWarning(result.restart_recommended);
      await loadEnvVars();
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : `Failed to connect ${label}`);
    } finally {
      setSavingProvider(null);
    }
  };

  const handleProviderDisconnect = async (key: string, label: string) => {
    if (!confirm(`Disconnect ${label}?`)) return;
    setSavingProvider(key);
    setEnvError(null);
    setEnvSuccess(null);
    try {
      const result = await updateEnvVariables([{ key, action: "delete" }]);
      setEnvSuccess(`${label} API key disconnected.`);
      setRestartWarning(result.restart_recommended);
      await loadEnvVars();
    } catch (e) {
      setEnvError(e instanceof Error ? e.message : `Failed to disconnect ${label}`);
    } finally {
      setSavingProvider(null);
    }
  };

  // Connection vars that need restart
  const CONNECTION_VARS = new Set([
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "OBSERVABILITY_MONGODB_URI",
    "ANTHROPIC_API_KEY", "OPENCODE_API_KEY", "OPENAI_API_KEY",
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_ENCRYPTION_KEY",
  ]);

  if (userLoading || loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
      </div>
    );
  }

  return (
    <>
    <div className="space-y-5 animate-fade-in-up">
      {/* Header — changes based on active tab */}
      <div>
        <h1 className="text-lg md:text-xl font-semibold text-gray-900">
          {tab === "environment" ? "Environment Variables" : tab === "settings" ? "Settings" : tab === "usage" ? "Usage & Authentication" : "Users & Permissions"}
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {tab === "environment"
            ? "Manage .env configuration for the running service."
            : tab === "settings"
            ? "Configure who can sign in to Loma and edit its core prompt."
            : tab === "usage"
            ? "Claude MAX subscription usage and login management."
            : "Manage user access across all connected tools."}
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex max-w-full overflow-x-auto rounded-lg border border-gray-200 p-0.5 bg-gray-50">
        {isAdmin && (
          <button
            onClick={() => setTab("users")}
            className={`shrink-0 px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
              tab === "users"
                ? "bg-surface text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Users
          </button>
        )}
        <button
          onClick={() => setTab("teams")}
          className={`shrink-0 px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
            tab === "teams"
              ? "bg-surface text-gray-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Teams
        </button>
        <button
          onClick={() => setTab("environment")}
          className={`shrink-0 px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
            tab === "environment"
              ? "bg-surface text-gray-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Environment
        </button>
        <button
          onClick={() => setTab("settings")}
          className={`shrink-0 px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
            tab === "settings"
              ? "bg-surface text-gray-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Settings
        </button>
        <button
          onClick={() => setTab("usage")}
          className={`shrink-0 px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
            tab === "usage"
              ? "bg-surface text-gray-900 shadow-sm"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Usage
        </button>
      </div>

      {/* Role permissions reference — only for users/teams tabs */}
      {(tab === "users" || tab === "teams") && <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
        <button
          onClick={() => setShowRoles(!showRoles)}
          className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-gray-50/50 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
              <svg className="w-4 h-4 text-indigo-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
              </svg>
            </div>
            <div>
              <span className="text-sm font-medium text-gray-900">Role Permissions</span>
              <div className="flex items-center gap-1.5 mt-0.5">
                {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => {
                  const m = ROLE_META[role];
                  return (
                    <span key={role} className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${m.bg} ${m.color}`}>
                      {m.label}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
          <svg
            className={`w-5 h-5 text-gray-400 transition-transform duration-200 ${showRoles ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
          </svg>
        </button>

        {showRoles && (
          <div className="border-t border-gray-100 px-5 pb-4 pt-2">
            {/* Role descriptions */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4">
              {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => {
                const m = ROLE_META[role];
                return (
                  <div key={role} className={`rounded-lg px-3 py-2 ${m.bg}`}>
                    <div className={`text-xs font-semibold ${m.color}`}>{m.label}</div>
                    <div className="text-[11px] text-gray-500 mt-0.5">{m.description}</div>
                  </div>
                );
              })}
            </div>

            {/* Permissions table */}
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="py-2 pr-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Permission</th>
                    {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => (
                      <th key={role} className="py-2 px-3 text-center text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                        {ROLE_META[role].label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ROLE_PERMISSIONS.map((row) => (
                    <tr key={row.permission} className="border-b border-gray-50 last:border-0">
                      <td className="py-2 pr-4 text-[12px] text-gray-600">{row.permission}</td>
                      {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => (
                        <td key={role} className="py-2 px-3 text-center">
                          {row[role] ? (
                            <svg className="w-4 h-4 text-emerald-500 mx-auto" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                            </svg>
                          ) : (
                            <span className="text-gray-200">&mdash;</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>}

      {/* Summary cards — only for users/teams tabs */}
      {(tab === "users" || tab === "teams") && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 stagger-children">
          <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-blue-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
              </svg>
            </div>
            <div>
              <div className="text-xs text-gray-500 font-medium">Total Users</div>
              <div className="text-xl font-semibold text-gray-900 mt-0.5">{users.length}</div>
            </div>
          </div>
          <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-emerald-50 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-emerald-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 0 0 3.741-.479 3 3 0 0 0-4.682-2.72m.94 3.198.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0 1 12 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 0 1 6 18.719m12 0a5.971 5.971 0 0 0-.941-3.197m0 0A5.995 5.995 0 0 0 12 12.75a5.995 5.995 0 0 0-5.058 2.772m0 0a3 3 0 0 0-4.681 2.72 8.986 8.986 0 0 0 3.74.477m.94-3.197a5.971 5.971 0 0 0-.94 3.197M15 6.75a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm6 3a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Zm-13.5 0a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z" />
              </svg>
            </div>
            <div>
              <div className="text-xs text-gray-500 font-medium">Teams</div>
              <div className="text-xl font-semibold text-gray-900 mt-0.5">{teams.length}</div>
            </div>
          </div>
          <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-violet-50 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-violet-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
              </svg>
            </div>
            <div>
              <div className="text-xs text-gray-500 font-medium">OAuth-enabled</div>
              <div className="text-xl font-semibold text-gray-900 mt-0.5">
                {ALL_TOOLS.filter((t) => getToolMeta(t).supportsOAuth).length}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Users tab (admin only) ── */}
      {tab === "users" && isAdmin && (
        <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left min-w-[600px]">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50/60">
                  <th className="py-3 px-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider sticky left-0 bg-gray-50/60 z-10 min-w-[180px]">
                    User
                  </th>
                  <th className="py-3 px-2 text-center text-[11px] font-semibold text-gray-400 uppercase tracking-wider min-w-[100px]">
                    Role
                  </th>
                  <th className="py-3 px-2 text-center text-[11px] font-semibold text-gray-400 uppercase tracking-wider min-w-[160px]">
                    Status
                  </th>
                  <th className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-amber-50">
                        <img src="/claude.png" alt="Claude" className="w-4 h-4 rounded" />
                      </div>
                      <span className="text-[9px] text-gray-400 font-medium leading-tight">Claude</span>
                    </div>
                  </th>
                  <th className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-blue-50">
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
                          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
                          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
                          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
                        </svg>
                      </div>
                      <span className="text-[9px] text-gray-400 font-medium leading-tight">Google</span>
                    </div>
                  </th>
                  <th className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-purple-50">
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
                          <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" fill="#E01E5A"/>
                          <path d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" fill="#36C5F0"/>
                          <path d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" fill="#2EB67D"/>
                          <path d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#ECB22E"/>
                        </svg>
                      </div>
                      <span className="text-[9px] text-gray-400 font-medium leading-tight">Slack</span>
                    </div>
                  </th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.email} className="border-b border-gray-100 last:border-0 hover:bg-gray-50/50 transition-colors">
                    <td className="py-3 px-4 sticky left-0 bg-surface z-10">
                      <Link href={`/admin/${encodeURIComponent(user.email)}`} className="flex items-center gap-2.5 group">
                        <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                          <span className="text-sm font-medium text-brand-700">{user.avatar}</span>
                        </div>
                        <div>
                          <div className="text-sm font-medium text-gray-900 group-hover:text-brand-600 transition-colors">
                            {user.name}
                          </div>
                          <div className="text-xs text-gray-400">{user.email}</div>
                        </div>
                      </Link>
                    </td>
                    <td className="py-3 px-2 text-center">
                      <select
                        value={user.system_role}
                        onChange={async (e) => {
                          const newRole = e.target.value as SystemRole;
                          setUsers((prev) =>
                            prev.map((u) =>
                              u.email === user.email ? { ...u, system_role: newRole } : u
                            )
                          );
                          try {
                            await updateUser(user.email, { system_role: newRole });
                          } catch (err) {
                            console.error("Failed to update role:", err);
                            setUsers((prev) =>
                              prev.map((u) =>
                                u.email === user.email ? { ...u, system_role: user.system_role } : u
                              )
                            );
                          }
                        }}
                        className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 cursor-pointer"
                      >
                        <option value="admin">Admin</option>
                        <option value="maintainer">Maintainer</option>
                        <option value="operator">Operator</option>
                        <option value="analyst">Analyst</option>
                        <option value="chatter">Chatter</option>
                      </select>
                    </td>
                    {/* Approval status */}
                    <td className="py-3 px-2 text-center">
                      {(user.status ?? "active") === "pending" ? (
                        <div className="flex items-center justify-center gap-1.5">
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Pending</span>
                          <button
                            onClick={async () => {
                              const prev = user.status ?? "active";
                              setUsers((us) => us.map((u) => (u.email === user.email ? { ...u, status: "active" } : u)));
                              try {
                                await updateUser(user.email, { status: "active" });
                              } catch (err) {
                                console.error("Failed to approve user:", err);
                                setUsers((us) => us.map((u) => (u.email === user.email ? { ...u, status: prev } : u)));
                              }
                            }}
                            className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-600 text-white font-medium hover:bg-emerald-700 transition-colors"
                          >
                            Approve
                          </button>
                          <button
                            onClick={async () => {
                              const prev = user.status ?? "active";
                              setUsers((us) => us.map((u) => (u.email === user.email ? { ...u, status: "rejected" } : u)));
                              try {
                                await updateUser(user.email, { status: "rejected" });
                              } catch (err) {
                                console.error("Failed to reject user:", err);
                                setUsers((us) => us.map((u) => (u.email === user.email ? { ...u, status: prev } : u)));
                              }
                            }}
                            className="text-[10px] px-2 py-0.5 rounded-full bg-red-50 text-red-600 font-medium hover:bg-red-100 transition-colors"
                          >
                            Reject
                          </button>
                        </div>
                      ) : (user.status ?? "active") === "rejected" ? (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-50 text-red-500 font-medium">Rejected</span>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Active</span>
                      )}
                    </td>
                    {/* Claude connection + pool toggle */}
                    <td className="py-3 px-2 text-center">
                      {user.claude_connected ? (
                        <div className="flex items-center justify-center gap-2">
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Connected</span>
                          <label className="inline-flex items-center gap-1 cursor-pointer" title="Include in round-robin pool">
                            <input
                              type="checkbox"
                              checked={user.claude_pool_enabled !== false}
                              onChange={async (e) => {
                                const enabled = e.target.checked;
                                setUsers((prev) =>
                                  prev.map((u) =>
                                    u.email === user.email ? { ...u, claude_pool_enabled: enabled } : u
                                  )
                                );
                                try {
                                  await updateUser(user.email, { claude_pool_enabled: enabled });
                                } catch (err) {
                                  console.error("Failed to update pool toggle:", err);
                                  setUsers((prev) =>
                                    prev.map((u) =>
                                      u.email === user.email ? { ...u, claude_pool_enabled: !enabled } : u
                                    )
                                  );
                                }
                              }}
                              className="w-3.5 h-3.5 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                            />
                            <span className="text-[10px] text-gray-400">Pool</span>
                          </label>
                        </div>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Not connected</span>
                      )}
                    </td>
                    {/* Google connection */}
                    <td className="py-3 px-2 text-center">
                      <StatusCell
                        role={null}
                        oauthStatus={user.tool_assignments?.["google-personal"]?.oauth_status ?? "not_connected"}
                        authMode="tool-managed"
                      />
                    </td>
                    {/* Slack connection */}
                    <td className="py-3 px-2 text-center">
                      <StatusCell
                        role={null}
                        oauthStatus={user.tool_assignments?.["slack-personal"]?.oauth_status ?? "not_connected"}
                        authMode="tool-managed"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Teams tab ── */}
      {tab === "teams" && (
        <div className="space-y-3 stagger-children">
          {teams.map((team) => {
            const lomaTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "loma-managed" && team.tool_defaults[t]?.role);
            const oauthTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "tool-managed" && team.tool_defaults[t]?.oauth_required);

            return (
              <Link
                key={team.team_id}
                href={`/admin/teams/${team.team_id}`}
                className="block bg-surface rounded-xl border border-gray-200 p-5 hover-lift transition-all duration-200 group"
              >
                <div className="flex items-start gap-4">
                  {/* Team avatar */}
                  <div
                    className="w-11 h-11 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ backgroundColor: team.bg_color }}
                  >
                    <span className="text-lg font-bold" style={{ color: team.color }}>
                      {team.name.charAt(0)}
                    </span>
                  </div>

                  {/* Team info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-gray-900 group-hover:text-brand-600 transition-colors">
                        {team.name}
                      </h3>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">
                        {team.members.length} members
                      </span>
                    </div>

                    {/* Member avatars */}
                    <div className="flex items-center gap-1 mt-2">
                      {team.members.slice(0, 5).map((email) => {
                        const user = users.find((u) => u.email === email);
                        return (
                          <div
                            key={email}
                            className="w-6 h-6 rounded-full bg-brand-100 flex items-center justify-center border-2 border-white -ml-1 first:ml-0"
                            title={user?.name ?? email}
                          >
                            <span className="text-[9px] font-medium text-brand-700">{user?.avatar ?? "?"}</span>
                          </div>
                        );
                      })}
                      {team.members.length > 5 && (
                        <span className="text-[10px] text-gray-400 ml-1">+{team.members.length - 5}</span>
                      )}
                    </div>

                    {/* Tool defaults summary */}
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      {lomaTools.slice(0, 6).map((toolKey) => {
                        const meta = getToolMeta(toolKey);
                        const Logo = TOOL_LOGOS[toolKey];
                        const defaultRole = team.tool_defaults[toolKey]?.role;
                        return (
                          <span
                            key={toolKey}
                            className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-md bg-gray-50 text-gray-500"
                          >
                            <span
                              className="w-3.5 h-3.5 rounded flex items-center justify-center"
                              style={{ backgroundColor: meta.bgColor }}
                            >
                              {Logo ? (
                                <Logo className="w-2 h-2" />
                              ) : (
                                <span className="text-[7px] font-bold" style={{ color: meta.color }}>
                                  {meta.displayName.charAt(0)}
                                </span>
                              )}
                            </span>
                            {defaultRole}
                          </span>
                        );
                      })}
                      {oauthTools.slice(0, 3).map((toolKey) => {
                        const meta = getToolMeta(toolKey);
                        const Logo = TOOL_LOGOS[toolKey];
                        return (
                          <span
                            key={toolKey}
                            className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-md bg-blue-50 text-blue-500"
                          >
                            <span
                              className="w-3.5 h-3.5 rounded flex items-center justify-center"
                              style={{ backgroundColor: meta.bgColor }}
                            >
                              {Logo ? (
                                <Logo className="w-2 h-2" />
                              ) : (
                                <span className="text-[7px] font-bold" style={{ color: meta.color }}>
                                  {meta.displayName.charAt(0)}
                                </span>
                              )}
                            </span>
                            OAuth
                          </span>
                        );
                      })}
                      {(lomaTools.length + oauthTools.length) > 9 && (
                        <span className="text-[9px] text-gray-400 px-1.5 py-0.5">
                          +{lomaTools.length + oauthTools.length - 9} more
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Arrow */}
                  <svg className="w-5 h-5 text-gray-300 group-hover:text-gray-500 transition-colors flex-shrink-0 mt-1" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                  </svg>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {/* ── Environment tab ── */}
      {tab === "environment" && (
        <div className="space-y-4">
          {/* Alerts */}
          {envError && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
              </svg>
              {envError}
              <button onClick={() => setEnvError(null)} className="ml-auto text-red-400 hover:text-red-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}
          {envSuccess && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
              {envSuccess}
              <button onClick={() => setEnvSuccess(null)} className="ml-auto text-emerald-400 hover:text-emerald-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}
          {restartWarning && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              Some changes require a service restart to take effect (e.g., database connections, Slack tokens, API keys).
              <button onClick={() => setRestartWarning(false)} className="ml-auto text-amber-400 hover:text-amber-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}

          {/* AI provider keys */}
          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">AI Providers</h2>
                <p className="text-xs text-gray-500 mt-0.5">Manage model-provider keys used by Dashboard Chat.</p>
              </div>
              <span className="text-[11px] px-2 py-1 rounded-full bg-gray-100 text-gray-500">
                Claude uses Agent SDK login
              </span>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x divide-gray-100">
              {AI_PROVIDER_CARDS.map((provider) => {
                const connected = isProviderConnected(provider.key);
                const busy = savingProvider === provider.key;
                return (
                  <div key={provider.key} className="p-4 space-y-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-semibold text-gray-900">{provider.name}</h3>
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                            connected ? "bg-emerald-50 text-emerald-600" : "bg-gray-100 text-gray-400"
                          }`}>
                            {connected ? "Connected" : "Not connected"}
                          </span>
                        </div>
                        <p className="text-xs text-gray-500 mt-1">{provider.description}</p>
                      </div>
                    </div>
                    <div className="text-[11px] text-gray-500">
                      <span className="font-medium text-gray-700">Models:</span> {provider.models}
                    </div>
                    <input
                      type="password"
                      value={providerInputs[provider.key] || ""}
                      onChange={(e) => setProviderInputs((prev) => ({ ...prev, [provider.key]: e.target.value }))}
                      placeholder={connected ? "Paste a new key to rotate" : `Paste ${provider.name} API key`}
                      className="w-full text-[13px] font-mono border border-gray-200 rounded-lg px-3 py-2 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                    />
                    <div className="flex items-center gap-2">
                      <button
                        disabled={busy}
                        onClick={() => handleProviderConnect(provider.key, provider.name)}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent-200 text-accent-on hover:bg-accent-300 disabled:opacity-50 transition-colors"
                      >
                        {busy ? "Saving..." : connected ? "Rotate key" : "Connect"}
                      </button>
                      {connected && (
                        <button
                          disabled={busy}
                          onClick={() => handleProviderDisconnect(provider.key, provider.name)}
                          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                        >
                          Disconnect
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
              <div className="p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-900">Claude</h3>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">
                    Agent SDK
                  </span>
                </div>
                <p className="text-xs text-gray-500">Uses the existing Claude Agent SDK account pool and pre-warmed Claude model.</p>
                <div className="text-[11px] text-gray-500">
                  <span className="font-medium text-gray-700">Models:</span> Claude only
                </div>
                <Link
                  href="/integrations/manage"
                  className="inline-flex px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
                >
                  Manage Claude login
                </Link>
              </div>
            </div>
          </div>

          {/* Summary stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
                <svg className="w-5 h-5 text-blue-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12a7.5 7.5 0 0 0 15 0m-15 0a7.5 7.5 0 1 1 15 0m-15 0H3m16.5 0H21m-1.5 0H12m-8.457 3.077 1.41-.513m14.095-5.13 1.41-.513M5.106 17.785l1.15-.964m11.49-9.642 1.149-.964M7.501 19.795l.75-1.3m7.5-12.99.75-1.3m-6.063 16.658.26-1.477m2.605-14.772.26-1.477m-2.01 17.334-.364-1.44m2.833-14.235-.364-1.44" />
                </svg>
              </div>
              <div>
                <div className="text-xs text-gray-500 font-medium">Total Variables</div>
                <div className="text-xl font-semibold text-gray-900 mt-0.5">{envVars.length}</div>
              </div>
            </div>
            <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-amber-50 flex items-center justify-center flex-shrink-0">
                <svg className="w-5 h-5 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                </svg>
              </div>
              <div>
                <div className="text-xs text-gray-500 font-medium">Sensitive</div>
                <div className="text-xl font-semibold text-gray-900 mt-0.5">{envVars.filter((v) => v.is_sensitive).length}</div>
              </div>
            </div>
            <div className="bg-surface rounded-xl border border-gray-200 p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center flex-shrink-0">
                <svg className="w-5 h-5 text-gray-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
                </svg>
              </div>
              <div>
                <div className="text-xs text-gray-500 font-medium">Read-only</div>
                <div className="text-xl font-semibold text-gray-900 mt-0.5">{envVars.filter((v) => v.is_readonly).length}</div>
              </div>
            </div>
          </div>

          {/* Sub-tab bar + save button */}
          <div className="flex items-center justify-between">
            <div className="inline-flex rounded-lg border border-gray-200 p-0.5 bg-gray-50">
              <button
                onClick={() => setEnvTab("variables")}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
                  envTab === "variables" ? "bg-surface text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
                }`}
              >
                Variables
              </button>
              <button
                onClick={() => {
                  setEnvTab("audit");
                  if (envAuditLog.length === 0) fetchEnvAuditLog().then(setEnvAuditLog).catch(console.error);
                }}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
                  envTab === "audit" ? "bg-surface text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
                }`}
              >
                Audit Log
              </button>
            </div>
            {envTab === "variables" && (
              <div className="flex items-center gap-2">
                <button
                  disabled={restarting}
                  onClick={async () => {
                    if (!confirm("This will restart the backend service. Are you sure?")) return;
                    setRestarting(true);
                    setEnvError(null);
                    try {
                      await restartService();
                      setEnvSuccess("Service is restarting... The page will reload shortly.");
                      setRestartWarning(false);
                      // Poll until the server is back up, then reload
                      const poll = setInterval(async () => {
                        try {
                          const res = await fetch(`${window.location.origin}/api/env`);
                          if (res.ok) {
                            clearInterval(poll);
                            window.location.reload();
                          }
                        } catch { /* server still down */ }
                      }, 2000);
                    } catch {
                      setEnvError("Failed to restart service");
                      setRestarting(false);
                    }
                  }}
                  className="px-4 py-2 rounded-lg text-sm font-medium transition-colors press-scale border border-gray-200 text-gray-600 hover:bg-gray-100 disabled:opacity-50"
                >
                  {restarting ? "Restarting..." : "Restart Service"}
                </button>
                <button
                  disabled={!hasEnvChanges || saving}
                  onClick={() => setShowDiff(true)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors press-scale ${
                    hasEnvChanges
                      ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                      : "bg-gray-100 text-gray-400 cursor-not-allowed"
                  }`}
                >
                  Save Changes
                </button>
              </div>
            )}
          </div>

          {/* Variables sub-tab */}
          {envTab === "variables" && (
            envLoading ? (
              <div className="flex items-center justify-center h-40">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
              </div>
            ) : (
              <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50/60">
                        <th className="py-3 px-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider w-[280px]">Key</th>
                        <th className="py-3 px-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Value</th>
                        <th className="py-3 px-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider w-[100px] text-center">Status</th>
                        <th className="py-3 px-4 text-[11px] font-semibold text-gray-400 uppercase tracking-wider w-[80px] text-center">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {envVars.filter((v) => !deletedKeys.has(v.key)).map((v) => {
                        const isEdited = v.key in editedValues;
                        const displayValue = v.key in revealedKeys
                          ? revealedKeys[v.key]
                          : isEdited
                            ? editedValues[v.key]
                            : v.value;
                        const isDuplicate = newVars.some((nv) => nv.key.trim() === v.key);

                        return (
                          <tr key={v.key} className={`border-b border-gray-100 last:border-0 transition-colors ${v.is_readonly ? "bg-gray-50/40" : isEdited ? "bg-amber-50/30" : "hover:bg-gray-50/50"}`}>
                            <td className="py-2.5 px-4">
                              <div className="flex items-center gap-2">
                                <code className="text-[13px] font-mono text-gray-900">{v.key}</code>
                                {isDuplicate && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Duplicate</span>
                                )}
                              </div>
                            </td>
                            <td className="py-2.5 px-4">
                              {v.is_readonly ? (
                                <span className="text-[13px] font-mono text-gray-400">{displayValue}</span>
                              ) : (
                                <input
                                  type="text"
                                  value={displayValue}
                                  onChange={(e) => setEditedValues((prev) => ({ ...prev, [v.key]: e.target.value }))}
                                  className="w-full text-[13px] font-mono border border-gray-200 rounded-lg px-3 py-1.5 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                                  placeholder="Value"
                                />
                              )}
                            </td>
                            <td className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                {v.is_readonly && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium inline-flex items-center gap-0.5">
                                    <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>
                                    Locked
                                  </span>
                                )}
                                {v.is_sensitive && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Sensitive</span>
                                )}
                                {isEdited && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">Modified</span>
                                )}
                              </div>
                            </td>
                            <td className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                {v.is_sensitive && !(v.key in revealedKeys) && (
                                  <button
                                    onClick={() => handleReveal(v.key)}
                                    className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                                    title="Reveal value"
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.64 0 8.577 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.64 0-8.577-3.007-9.963-7.178Z" />
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                                    </svg>
                                  </button>
                                )}
                                {v.is_sensitive && v.key in revealedKeys && (
                                  <button
                                    onClick={() => setRevealedKeys((prev) => { const next = { ...prev }; delete next[v.key]; return next; })}
                                    className="p-1 rounded-md text-blue-500 hover:text-blue-700 hover:bg-blue-50 transition-colors"
                                    title="Hide value"
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
                                    </svg>
                                  </button>
                                )}
                                {!v.is_readonly && (
                                  <button
                                    onClick={() => {
                                      if (confirm(`Delete variable "${v.key}"?`)) {
                                        setDeletedKeys((prev) => new Set([...prev, v.key]));
                                      }
                                    }}
                                    className="p-1 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                    title="Delete variable"
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                                    </svg>
                                  </button>
                                )}
                                {isEdited && (
                                  <button
                                    onClick={() => setEditedValues((prev) => { const next = { ...prev }; delete next[v.key]; return next; })}
                                    className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                                    title="Undo changes"
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
                                    </svg>
                                  </button>
                                )}
                                {!v.is_readonly && (
                                  <button
                                    onClick={() => handleToggleSensitive(v.key, v.is_sensitive)}
                                    className={`p-1 rounded-md transition-colors ${v.is_sensitive ? "text-amber-500 hover:text-amber-700 hover:bg-amber-50" : "text-gray-400 hover:text-amber-500 hover:bg-amber-50"}`}
                                    title={v.is_sensitive ? "Unmark as sensitive" : "Mark as sensitive"}
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                                    </svg>
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        );
                      })}

                      {/* New variable rows */}
                      {newVars.map((nv, idx) => {
                        const isDuplicate = envVars.some((v) => v.key === nv.key.trim()) || newVars.filter((n, i) => i !== idx && n.key.trim() === nv.key.trim()).length > 0;
                        return (
                          <tr key={`new-${idx}`} className="border-b border-gray-100 last:border-0 bg-emerald-50/20">
                            <td className="py-2.5 px-4">
                              <div className="flex items-center gap-2">
                                <input
                                  type="text"
                                  value={nv.key}
                                  onChange={(e) => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, key: e.target.value } : v))}
                                  className="w-full text-[13px] font-mono border border-gray-200 rounded-lg px-3 py-1.5 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                                  placeholder="KEY_NAME"
                                />
                                {isDuplicate && nv.key.trim() && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium whitespace-nowrap">Duplicate</span>
                                )}
                              </div>
                            </td>
                            <td className="py-2.5 px-4">
                              <input
                                type="text"
                                value={nv.value}
                                onChange={(e) => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, value: e.target.value } : v))}
                                className="w-full text-[13px] font-mono border border-gray-200 rounded-lg px-3 py-1.5 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                                placeholder="value"
                              />
                            </td>
                            <td className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">New</span>
                                {nv.sensitive && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Sensitive</span>
                                )}
                              </div>
                            </td>
                            <td className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                <button
                                  onClick={() => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, sensitive: !v.sensitive } : v))}
                                  className={`p-1 rounded-md transition-colors ${nv.sensitive ? "text-amber-500 hover:text-amber-700 hover:bg-amber-50" : "text-gray-400 hover:text-amber-500 hover:bg-amber-50"}`}
                                  title={nv.sensitive ? "Unmark as sensitive" : "Mark as sensitive"}
                                >
                                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                                  </svg>
                                </button>
                                <button
                                  onClick={() => setNewVars((prev) => prev.filter((_, i) => i !== idx))}
                                  className="p-1 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                  title="Remove"
                                >
                                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                                  </svg>
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Add variable button */}
                <div className="px-4 py-3 border-t border-gray-100">
                  <button
                    onClick={() => setNewVars((prev) => [...prev, { key: "", value: "", sensitive: false }])}
                    className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                    </svg>
                    Add Variable
                  </button>
                </div>
              </div>
            )
          )}

          {/* Audit log sub-tab */}
          {envTab === "audit" && (
            <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
              {envAuditLog.length === 0 ? (
                <div className="px-6 py-12 text-center text-sm text-gray-400">No audit log entries yet.</div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {envAuditLog.map((entry, idx) => {
                    const isExpanded = expandedAudit.has(idx);
                    const date = new Date(entry.timestamp);
                    const timeStr = date.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

                    return (
                      <div key={idx}>
                        <button
                          onClick={() => setExpandedAudit((prev) => {
                            const next = new Set(prev);
                            next.has(idx) ? next.delete(idx) : next.add(idx);
                            return next;
                          })}
                          className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-gray-50/50 transition-colors"
                        >
                          <div className="flex-1 flex items-center gap-3 min-w-0">
                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                              entry.action === "update"
                                ? "bg-blue-50 text-blue-600"
                                : "bg-violet-50 text-violet-600"
                            }`}>
                              {entry.action === "update" ? "Update" : "Reveal"}
                            </span>
                            <span className="text-sm text-gray-900 truncate">{entry.user_email}</span>
                            {entry.action === "update" && entry.changes && (
                              <span className="text-xs text-gray-400">
                                {entry.changes.length} change{entry.changes.length !== 1 ? "s" : ""}
                              </span>
                            )}
                            {entry.action === "reveal" && entry.revealed_key && (
                              <code className="text-xs text-gray-500 font-mono">{entry.revealed_key}</code>
                            )}
                          </div>
                          <span className="text-xs text-gray-400 flex-shrink-0">{timeStr}</span>
                          <svg
                            className={`w-4 h-4 text-gray-400 transition-transform duration-200 flex-shrink-0 ${isExpanded ? "rotate-180" : ""}`}
                            fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                          </svg>
                        </button>
                        {isExpanded && entry.changes && (
                          <div className="px-5 pb-4 pt-1 space-y-1.5">
                            {entry.changes.map((c, ci) => (
                              <div key={ci} className="flex items-start gap-2 text-[13px] font-mono">
                                <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium mt-0.5 ${
                                  c.type === "added" ? "bg-emerald-50 text-emerald-600"
                                    : c.type === "deleted" ? "bg-red-50 text-red-600"
                                      : "bg-amber-50 text-amber-600"
                                }`}>
                                  {c.type}
                                </span>
                                <span className="text-gray-900">{c.key}</span>
                                {c.type === "modified" && (
                                  <span className="text-gray-400">
                                    <span className="line-through text-red-400">{c.old_preview}</span>
                                    {" → "}
                                    <span className="text-emerald-600">{c.new_preview}</span>
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      )}

      {/* ── Settings tab ── */}
      {tab === "settings" && (
        <div className="space-y-4">
          {envError && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
              </svg>
              {envError}
              <button onClick={() => setEnvError(null)} className="ml-auto text-red-400 hover:text-red-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}
          {envSuccess && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
              {envSuccess}
              <button onClick={() => setEnvSuccess(null)} className="ml-auto text-emerald-400 hover:text-emerald-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}
          {restartWarning && (
            <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-700">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              Restart the service for this setting to take effect everywhere.
              <button onClick={() => setRestartWarning(false)} className="ml-auto text-amber-400 hover:text-amber-600">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
          )}

          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">Allowed Email Domain</h2>
                <p className="text-xs text-gray-500 mt-0.5">Only users with these email domains can sign in to Loma.</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  disabled={!hasSettingsChanges || settingsSaving}
                  onClick={handleSettingsSave}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors press-scale ${
                    hasSettingsChanges
                      ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                      : "bg-gray-100 text-gray-400 cursor-not-allowed"
                  }`}
                >
                  {settingsSaving ? "Saving..." : "Save Settings"}
                </button>
              </div>
            </div>

            {envLoading ? (
              <div className="flex items-center justify-center h-40">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
              </div>
            ) : (
              <div className="p-5">
                <label className={`block rounded-lg border p-4 transition-colors ${
                  hasSettingsChanges ? "border-amber-200 bg-amber-50/30" : "border-gray-200 bg-gray-50/30"
                }`}>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="text-xs font-medium text-gray-700">Allowed email domains</span>
                    {hasSettingsChanges ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Modified</span>
                    ) : getEnvVar(ALLOWED_EMAIL_DOMAINS_KEY) ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Configured</span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Not set</span>
                    )}
                  </div>
                  <input
                    type="text"
                    value={allowedEmailDomainsValue}
                    onChange={(e) => handleAllowedEmailDomainsChange(e.target.value)}
                    placeholder="example.com,company.com"
                    className="w-full text-[13px] font-mono border border-gray-200 rounded-lg px-3 py-2 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                  />
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <code className="text-[11px] text-gray-400 font-mono truncate">{ALLOWED_EMAIL_DOMAINS_KEY}</code>
                    {hasSettingsChanges && (
                      <button
                        type="button"
                        onClick={() => setSettingsValues((prev) => { const next = { ...prev }; delete next[ALLOWED_EMAIL_DOMAINS_KEY]; return next; })}
                        className="text-[11px] text-gray-500 hover:text-gray-700"
                      >
                        Undo
                      </button>
                    )}
                  </div>
                </label>
              </div>
            )}
          </div>

          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100">
              <h2 className="text-sm font-semibold text-gray-900">Core Prompt</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                These sections are stored in MongoDB and loaded into Loma&apos;s base system prompt.
              </p>
            </div>

            {promptLoading ? (
              <div className="flex items-center justify-center h-40">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
              </div>
            ) : (
              <div className="divide-y divide-gray-100">
                {promptSettings.map((setting) => {
                  const changed = isPromptChanged(setting);
                  const draft = getPromptDraft(setting);
                  return (
                    <section key={setting.setting_key} className="p-5 space-y-3">
                      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold text-gray-900">{setting.title}</h3>
                            {changed ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Modified</span>
                            ) : setting.content ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Configured</span>
                            ) : (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Empty</span>
                            )}
                          </div>
                          <div className="text-xs text-gray-400 mt-1">
                            {setting.updated_at && setting.updated_by
                              ? `Last updated by ${setting.updated_by}`
                              : "No saved content yet"}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {setting.default_content && (
                            <button
                              type="button"
                              onClick={() => setPromptDrafts((prev) => ({ ...prev, [setting.setting_key]: setting.default_content }))}
                              disabled={!canSetPromptDefault(setting)}
                              className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                                canSetPromptDefault(setting)
                                  ? "border-gray-200 text-gray-600 hover:bg-gray-50"
                                  : "border-gray-100 text-gray-300 cursor-not-allowed"
                              }`}
                            >
                              Set default
                            </button>
                          )}
                          {changed && (
                            <button
                              type="button"
                              onClick={() => setPromptDrafts((prev) => { const next = { ...prev }; delete next[setting.setting_key]; return next; })}
                              className="px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
                            >
                              Undo
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={!changed || savingPromptKey === setting.setting_key}
                            onClick={() => handlePromptSave(setting)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                              changed
                                ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                                : "bg-gray-100 text-gray-400 cursor-not-allowed"
                            }`}
                          >
                            {savingPromptKey === setting.setting_key ? "Saving..." : "Save"}
                          </button>
                        </div>
                      </div>
                      <textarea
                        value={draft}
                        onChange={(e) => setPromptDrafts((prev) => ({ ...prev, [setting.setting_key]: e.target.value }))}
                        placeholder={setting.setting_key === "identity_guidelines"
                          ? "Describe Loma's identity, tone, operating rules, and general guidelines."
                          : "Describe the company, product, customer context, terminology, and other always-on company facts."}
                        className="w-full min-h-[260px] resize-y text-[13px] leading-6 font-mono border border-gray-200 rounded-lg px-3 py-2 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                      />
                    </section>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Usage tab ── */}
      {tab === "usage" && (
        <>
          <UsagePanel />
          <div className="mt-6">
            <h3 className="text-sm font-medium text-gray-600 mb-3">Server Terminal</h3>
            <WebTerminal />
          </div>
        </>
      )}

    </div>

    {/* Diff preview modal — outside the animate-fade-in-up div so fixed positioning works */}
    {showDiff && (
      <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => setShowDiff(false)}>
        <div className="bg-surface rounded-xl border border-gray-200 shadow-xl max-w-lg w-full max-h-[80vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
          <div className="px-5 py-4 border-b border-gray-200">
            <h3 className="text-sm font-semibold text-gray-900">Review Changes</h3>
            <p className="text-xs text-gray-500 mt-0.5">The following changes will be applied to the .env file.</p>
          </div>
          <div className="px-5 py-4 overflow-y-auto max-h-[50vh] space-y-2">
            {computeDiff().some((c) => CONNECTION_VARS.has(c.key)) && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700 mb-3">
                <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                </svg>
                Some of these variables require a service restart to take effect.
              </div>
            )}
            {computeDiff().map((c, i) => (
              <div key={i} className={`flex items-start gap-2 px-3 py-2 rounded-lg text-[13px] font-mono ${
                c.type === "added" ? "bg-emerald-50/50 border border-emerald-200/50"
                  : c.type === "deleted" ? "bg-red-50/50 border border-red-200/50"
                    : "bg-amber-50/50 border border-amber-200/50"
              }`}>
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium font-sans mt-0.5 ${
                  c.type === "added" ? "bg-emerald-100 text-emerald-700"
                    : c.type === "deleted" ? "bg-red-100 text-red-700"
                      : "bg-amber-100 text-amber-700"
                }`}>{c.type}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-gray-900 font-semibold">{c.key}</div>
                  {c.type === "modified" && (
                    <div className="text-xs mt-0.5">
                      <span className="text-red-400 line-through">{c.old_preview}</span>
                      {" → "}
                      <span className="text-emerald-600">{c.new_preview}</span>
                    </div>
                  )}
                  {c.type === "added" && (
                    <div className="text-xs text-emerald-600 mt-0.5">{c.new_preview}</div>
                  )}
                  {c.type === "deleted" && (
                    <div className="text-xs text-red-400 line-through mt-0.5">{c.old_preview}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
            <button
              onClick={() => setShowDiff(false)}
              className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
            >
              Cancel
            </button>
            <button
              disabled={saving}
              onClick={handleSave}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-accent-200 text-accent-on hover:bg-accent-300 transition-colors press-scale disabled:opacity-50"
            >
              {saving ? "Applying..." : "Apply Changes"}
            </button>
          </div>
        </div>
      </div>
    )}

    </>
  );
}
