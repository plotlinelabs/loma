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
  deleteUser,
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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertAction } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import {
  RiShieldCheckLine,
  RiArrowDownSLine,
  RiArrowRightSLine,
  RiCheckLine,
  RiCloseLine,
  RiAddLine,
  RiDeleteBinLine,
  RiEyeLine,
  RiEyeOffLine,
  RiLockLine,
  RiAlertLine,
  RiInformationLine,
  RiArrowGoBackLine,
  RiTeamLine,
  RiUserLine,
  RiLoader4Line,
} from "@remixicon/react";
import { cn } from "@/lib/utils";

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
      return <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">Connected</Badge>;
    if (oauthStatus === "expired")
      return <Badge variant="secondary" className="text-[10px] bg-amber-50 text-amber-600">Expired</Badge>;
    return <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">Not connected</Badge>;
  }

  // Loma-managed — show provenance
  if (role) {
    const isTeam = source && source !== "direct" && source !== "none";
    return (
      <span className="inline-flex flex-col items-center gap-0.5">
        <Badge variant="secondary" className="text-[10px] bg-blue-50 text-blue-600">{role}</Badge>
        {isTeam && (
          <span className="text-[8px] text-muted-foreground">via {source}</span>
        )}
      </span>
    );
  }
  return <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">No access</Badge>;
}

/* ── Page ─────────────────────────────────────────────────────────── */

export default function AdminPage() {
  const { user: currentUser, isAdmin, hasRole, loading: userLoading } = useUser();
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
    setEnvVars((prev) => prev.map((v) => v.key === key ? { ...v, is_sensitive: newSensitive, masked: newSensitive, value: newSensitive ? "•••" : v.value } : v));
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
        <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <>
    <div className="space-y-5 animate-fade-in-up">
      {/* Header — changes based on active tab */}
      <div>
        <h1 className="text-xl md:text-2xl font-heading font-semibold text-foreground">
          {tab === "environment" ? "Environment Variables" : tab === "settings" ? "Settings" : tab === "usage" ? "Usage & Authentication" : "Users & Permissions"}
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
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
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList className="max-w-full overflow-x-auto">
          {isAdmin && (
            <TabsTrigger value="users" className="shrink-0">Users</TabsTrigger>
          )}
          <TabsTrigger value="teams" className="shrink-0">Teams</TabsTrigger>
          <TabsTrigger value="environment" className="shrink-0">Environment</TabsTrigger>
          <TabsTrigger value="settings" className="shrink-0">Settings</TabsTrigger>
          <TabsTrigger value="usage" className="shrink-0">Usage</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Role permissions reference — only for users/teams tabs */}
      {(tab === "users" || tab === "teams") && <Card>
        <Button
          variant="ghost"
          onClick={() => setShowRoles(!showRoles)}
          className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-muted/50 transition-colors h-auto rounded-none"
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
              <RiShieldCheckLine className="text-indigo-500" size={16} />
            </div>
            <div>
              <span className="text-sm font-medium text-foreground">Role Permissions</span>
              <div className="flex items-center gap-1.5 mt-0.5">
                {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => {
                  const m = ROLE_META[role];
                  return (
                    <Badge key={role} variant="secondary" className={cn("text-[10px] px-1.5 py-0.5", m.bg, m.color)}>
                      {m.label}
                    </Badge>
                  );
                })}
              </div>
            </div>
          </div>
          <RiArrowDownSLine
            className={cn("text-muted-foreground transition-transform duration-200", showRoles && "rotate-180")}
            size={20}
          />
        </Button>

        {showRoles && (
          <div className="border-t border-border px-5 pb-4 pt-2">
            {/* Role descriptions */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4">
              {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => {
                const m = ROLE_META[role];
                return (
                  <div key={role} className={cn("rounded-lg px-3 py-2", m.bg)}>
                    <div className={cn("text-xs font-semibold", m.color)}>{m.label}</div>
                    <div className="text-[11px] text-muted-foreground mt-0.5">{m.description}</div>
                  </div>
                );
              })}
            </div>

            {/* Permissions table */}
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="border-b border-border">
                    <TableHead className="py-2 pr-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Permission</TableHead>
                    {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => (
                      <TableHead key={role} className="py-2 px-3 text-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                        {ROLE_META[role].label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ROLE_PERMISSIONS.map((row) => (
                    <TableRow key={row.permission} className="border-b border-muted/50 last:border-0">
                      <TableCell className="py-2 pr-4 text-[12px] text-muted-foreground">{row.permission}</TableCell>
                      {(["admin", "maintainer", "operator", "analyst", "chatter"] as const).map((role) => (
                        <TableCell key={role} className="py-2 px-3 text-center">
                          {row[role] ? (
                            <RiCheckLine className="text-emerald-500 mx-auto" size={16} />
                          ) : (
                            <span className="text-muted-foreground/30">&mdash;</span>
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </Card>}

      {/* Summary cards — only for users/teams tabs */}
      {(tab === "users" || tab === "teams") && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 stagger-children">
          <Card className="p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
              <RiTeamLine className="text-blue-500" size={20} />
            </div>
            <div>
              <div className="text-xs text-muted-foreground font-medium">Total Users</div>
              <div className="text-xl font-semibold text-foreground mt-0.5">{users.length}</div>
            </div>
          </Card>
          <Card className="p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-emerald-50 flex items-center justify-center flex-shrink-0">
              <RiTeamLine className="text-emerald-500" size={20} />
            </div>
            <div>
              <div className="text-xs text-muted-foreground font-medium">Teams</div>
              <div className="text-xl font-semibold text-foreground mt-0.5">{teams.length}</div>
            </div>
          </Card>
          <Card className="p-4 flex items-start gap-3 hover-lift">
            <div className="w-10 h-10 rounded-lg bg-violet-50 flex items-center justify-center flex-shrink-0">
              <RiShieldCheckLine className="text-violet-500" size={20} />
            </div>
            <div>
              <div className="text-xs text-muted-foreground font-medium">OAuth-enabled</div>
              <div className="text-xl font-semibold text-foreground mt-0.5">
                {ALL_TOOLS.filter((t) => getToolMeta(t).supportsOAuth).length}
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* ── Users tab (admin only) ── */}
      {tab === "users" && isAdmin && (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <Table className="min-w-[600px]">
              <TableHeader>
                <TableRow className="border-b border-border bg-muted/60">
                  <TableHead className="py-3 px-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider sticky left-0 bg-muted/60 z-10 min-w-[180px]">
                    User
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider min-w-[100px]">
                    Role
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider min-w-[160px]">
                    Status
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-amber-50">
                        <img src="/claude.png" alt="Claude" className="w-4 h-4 rounded" />
                      </div>
                      <span className="text-[9px] text-muted-foreground font-medium leading-tight">Claude</span>
                    </div>
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-blue-50">
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
                          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
                          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
                          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
                        </svg>
                      </div>
                      <span className="text-[9px] text-muted-foreground font-medium leading-tight">Google</span>
                    </div>
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center min-w-[80px]">
                    <div className="inline-flex flex-col items-center gap-1">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-purple-50">
                        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
                          <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" fill="#E01E5A"/>
                          <path d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" fill="#36C5F0"/>
                          <path d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" fill="#2EB67D"/>
                          <path d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#ECB22E"/>
                        </svg>
                      </div>
                      <span className="text-[9px] text-muted-foreground font-medium leading-tight">Slack</span>
                    </div>
                  </TableHead>
                  <TableHead className="py-3 px-2 text-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider min-w-[90px]">
                    Actions
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow key={user.email} className="border-b border-border last:border-0 hover:bg-muted/50 transition-colors">
                    <TableCell className="py-3 px-4 sticky left-0 bg-card z-10">
                      <Link href={`/admin/${encodeURIComponent(user.email)}`} className="flex items-center gap-2.5 group">
                        <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                          <span className="text-sm font-medium text-brand-700">{user.avatar}</span>
                        </div>
                        <div>
                          <div className="text-sm font-medium text-foreground group-hover:text-brand-600 transition-colors">
                            {user.name}
                          </div>
                          <div className="text-xs text-muted-foreground">{user.email}</div>
                        </div>
                      </Link>
                    </TableCell>
                    <TableCell className="py-3 px-2 text-center">
                      <Select
                        value={user.system_role}
                        onValueChange={async (newRole: string) => {
                          const typedRole = newRole as SystemRole;
                          setUsers((prev) =>
                            prev.map((u) =>
                              u.email === user.email ? { ...u, system_role: typedRole } : u
                            )
                          );
                          try {
                            await updateUser(user.email, { system_role: typedRole });
                          } catch (err) {
                            console.error("Failed to update role:", err);
                            setUsers((prev) =>
                              prev.map((u) =>
                                u.email === user.email ? { ...u, system_role: user.system_role } : u
                              )
                            );
                          }
                        }}
                      >
                        <SelectTrigger size="sm" className="text-xs border-border bg-card text-foreground cursor-pointer">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="admin">Admin</SelectItem>
                          <SelectItem value="maintainer">Maintainer</SelectItem>
                          <SelectItem value="operator">Operator</SelectItem>
                          <SelectItem value="analyst">Analyst</SelectItem>
                          <SelectItem value="chatter">Chatter</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    {/* Approval status */}
                    <TableCell className="py-3 px-2 text-center">
                      {(user.status ?? "active") === "pending" ? (
                        <div className="flex items-center justify-center gap-1.5">
                          <Badge variant="secondary" className="text-[10px] bg-amber-50 text-amber-600">Pending</Badge>
                          <Button
                            size="xs"
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
                            className="text-[10px] bg-emerald-600 text-white hover:bg-emerald-700"
                          >
                            Approve
                          </Button>
                          <Button
                            variant="destructive"
                            size="xs"
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
                            className="text-[10px]"
                          >
                            Reject
                          </Button>
                        </div>
                      ) : (user.status ?? "active") === "rejected" ? (
                        <Badge variant="secondary" className="text-[10px] bg-red-50 text-red-500">Rejected</Badge>
                      ) : (
                        <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">Active</Badge>
                      )}
                    </TableCell>
                    {/* Claude connection + pool toggle */}
                    <TableCell className="py-3 px-2 text-center">
                      {user.claude_connected ? (
                        <div className="flex items-center justify-center gap-2">
                          <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">Connected</Badge>
                          <Label className="inline-flex items-center gap-1 cursor-pointer" title="Include in round-robin pool">
                            <Switch
                              size="sm"
                              checked={user.claude_pool_enabled !== false}
                              onCheckedChange={async (enabled) => {
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
                            />
                            <span className="text-[10px] text-muted-foreground">Pool</span>
                          </Label>
                        </div>
                      ) : (
                        <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">Not connected</Badge>
                      )}
                    </TableCell>
                    {/* Google connection */}
                    <TableCell className="py-3 px-2 text-center">
                      <StatusCell
                        role={null}
                        oauthStatus={user.tool_assignments?.["google-personal"]?.oauth_status ?? "not_connected"}
                        authMode="tool-managed"
                      />
                    </TableCell>
                    {/* Slack connection */}
                    <TableCell className="py-3 px-2 text-center">
                      <StatusCell
                        role={null}
                        oauthStatus={user.tool_assignments?.["slack-personal"]?.oauth_status ?? "not_connected"}
                        authMode="tool-managed"
                      />
                    </TableCell>
                    {/* Actions */}
                    <TableCell className="py-3 px-2 text-center">
                      {currentUser?.email === user.email ? (
                        <span className="text-[10px] text-muted-foreground/50">You</span>
                      ) : (
                        <Button
                          variant="link"
                          size="xs"
                          onClick={async () => {
                            if (
                              !confirm(
                                `Remove ${user.name || user.email} from the workspace? They will lose access immediately. This cannot be undone.`
                              )
                            )
                              return;
                            const prev = users;
                            setUsers((us) => us.filter((u) => u.email !== user.email));
                            try {
                              await deleteUser(user.email);
                            } catch (e) {
                              setUsers(prev);
                              alert(e instanceof Error ? e.message : "Failed to remove user");
                            }
                          }}
                          className="text-[11px] font-medium text-destructive hover:text-destructive/80"
                        >
                          Remove
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
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
                className="block"
              >
                <Card className="p-5 hover-lift transition-all duration-200 group">
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
                        <h3 className="text-sm font-semibold text-foreground group-hover:text-brand-600 transition-colors">
                          {team.name}
                        </h3>
                        <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">
                          {team.members.length} members
                        </Badge>
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
                          <span className="text-[10px] text-muted-foreground ml-1">+{team.members.length - 5}</span>
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
                              className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground"
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
                          <span className="text-[9px] text-muted-foreground px-1.5 py-0.5">
                            +{lomaTools.length + oauthTools.length - 9} more
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Arrow */}
                    <RiArrowRightSLine className="text-muted-foreground/50 group-hover:text-muted-foreground transition-colors flex-shrink-0 mt-1" size={20} />
                  </div>
                </Card>
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
            <Alert variant="destructive" className="bg-red-50 border-red-200 text-red-700">
              <RiInformationLine size={16} className="flex-shrink-0" />
              <AlertDescription>{envError}</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setEnvError(null)} className="text-red-400 hover:text-red-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}
          {envSuccess && (
            <Alert className="bg-emerald-50 border-emerald-200 text-emerald-700">
              <RiCheckLine size={16} className="flex-shrink-0" />
              <AlertDescription>{envSuccess}</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setEnvSuccess(null)} className="text-emerald-400 hover:text-emerald-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}
          {restartWarning && (
            <Alert className="bg-amber-50 border-amber-200 text-amber-700">
              <RiAlertLine size={16} className="flex-shrink-0" />
              <AlertDescription>Some changes require a service restart to take effect (e.g., database connections, Slack tokens, API keys).</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setRestartWarning(false)} className="text-amber-400 hover:text-amber-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}

          {/* AI provider keys */}
          <Card className="overflow-hidden">
            <CardHeader className="px-4 py-3 border-b border-border flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-sm">AI Providers</CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">Manage model-provider keys used by Dashboard Chat.</p>
              </div>
              <Badge variant="secondary" className="text-[11px] bg-gray-100 text-muted-foreground">
                Claude uses Agent SDK login
              </Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="grid grid-cols-1 lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x divide-border">
                {AI_PROVIDER_CARDS.map((provider) => {
                  const connected = isProviderConnected(provider.key);
                  const busy = savingProvider === provider.key;
                  return (
                    <div key={provider.key} className="p-4 space-y-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold text-foreground">{provider.name}</h3>
                            <Badge variant="secondary" className={cn("text-[10px]", connected ? "bg-emerald-50 text-emerald-600" : "bg-gray-100 text-muted-foreground")}>
                              {connected ? "Connected" : "Not connected"}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground mt-1">{provider.description}</p>
                        </div>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        <span className="font-medium text-foreground">Models:</span> {provider.models}
                      </div>
                      <Input
                        type="password"
                        value={providerInputs[provider.key] || ""}
                        onChange={(e) => setProviderInputs((prev) => ({ ...prev, [provider.key]: e.target.value }))}
                        placeholder={connected ? "Paste a new key to rotate" : `Paste ${provider.name} API key`}
                        className="w-full text-[13px] font-mono"
                      />
                      <div className="flex items-center gap-2">
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() => handleProviderConnect(provider.key, provider.name)}
                          className="bg-accent-200 text-accent-on hover:bg-accent-300"
                        >
                          {busy ? "Saving..." : connected ? "Rotate key" : "Connect"}
                        </Button>
                        {connected && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => handleProviderDisconnect(provider.key, provider.name)}
                          >
                            Disconnect
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div className="p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-foreground">Claude</h3>
                    <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">
                      Agent SDK
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">Uses the existing Claude Agent SDK account pool and pre-warmed Claude model.</p>
                  <div className="text-[11px] text-muted-foreground">
                    <span className="font-medium text-foreground">Models:</span> Claude only
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link href="/integrations/manage">
                      Manage Claude login
                    </Link>
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Summary stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Card className="p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
                <RiInformationLine className="text-blue-500" size={20} />
              </div>
              <div>
                <div className="text-xs text-muted-foreground font-medium">Total Variables</div>
                <div className="text-xl font-semibold text-foreground mt-0.5">{envVars.length}</div>
              </div>
            </Card>
            <Card className="p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-amber-50 flex items-center justify-center flex-shrink-0">
                <RiLockLine className="text-amber-500" size={20} />
              </div>
              <div>
                <div className="text-xs text-muted-foreground font-medium">Sensitive</div>
                <div className="text-xl font-semibold text-foreground mt-0.5">{envVars.filter((v) => v.is_sensitive).length}</div>
              </div>
            </Card>
            <Card className="p-4 flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center flex-shrink-0">
                <RiShieldCheckLine className="text-muted-foreground" size={20} />
              </div>
              <div>
                <div className="text-xs text-muted-foreground font-medium">Read-only</div>
                <div className="text-xl font-semibold text-foreground mt-0.5">{envVars.filter((v) => v.is_readonly).length}</div>
              </div>
            </Card>
          </div>

          {/* Sub-tab bar + save button */}
          <div className="flex items-center justify-between">
            <Tabs value={envTab} onValueChange={(v) => {
              setEnvTab(v as typeof envTab);
              if (v === "audit" && envAuditLog.length === 0) fetchEnvAuditLog().then(setEnvAuditLog).catch(console.error);
            }}>
              <TabsList>
                <TabsTrigger value="variables">Variables</TabsTrigger>
                <TabsTrigger value="audit">Audit Log</TabsTrigger>
              </TabsList>
            </Tabs>
            {envTab === "variables" && (
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
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
                  className="press-scale"
                >
                  {restarting ? "Restarting..." : "Restart Service"}
                </Button>
                <Button
                  disabled={!hasEnvChanges || saving}
                  onClick={() => setShowDiff(true)}
                  className={cn(
                    "press-scale",
                    hasEnvChanges
                      ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                      : "bg-gray-100 text-muted-foreground cursor-not-allowed"
                  )}
                >
                  Save Changes
                </Button>
              </div>
            )}
          </div>

          {/* Variables sub-tab */}
          {envTab === "variables" && (
            envLoading ? (
              <div className="flex items-center justify-center h-40">
                <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
              </div>
            ) : (
              <Card className="overflow-hidden">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow className="border-b border-border bg-muted/60">
                        <TableHead className="py-3 px-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-[280px]">Key</TableHead>
                        <TableHead className="py-3 px-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Value</TableHead>
                        <TableHead className="py-3 px-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-[100px] text-center">Status</TableHead>
                        <TableHead className="py-3 px-4 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-[80px] text-center">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {envVars.filter((v) => !deletedKeys.has(v.key)).map((v) => {
                        const isEdited = v.key in editedValues;
                        const displayValue = v.key in revealedKeys
                          ? revealedKeys[v.key]
                          : isEdited
                            ? editedValues[v.key]
                            : v.value;
                        const isDuplicate = newVars.some((nv) => nv.key.trim() === v.key);

                        return (
                          <TableRow key={v.key} className={cn("border-b border-border last:border-0 transition-colors", v.is_readonly ? "bg-muted/40" : isEdited ? "bg-amber-50/30" : "hover:bg-muted/50")}>
                            <TableCell className="py-2.5 px-4">
                              <div className="flex items-center gap-2">
                                <code className="text-[13px] font-mono text-foreground">{v.key}</code>
                                {isDuplicate && (
                                  <Badge variant="secondary" className="text-[9px] bg-amber-50 text-amber-600">Duplicate</Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="py-2.5 px-4">
                              {v.is_readonly ? (
                                <span className="text-[13px] font-mono text-muted-foreground">{displayValue}</span>
                              ) : (
                                <Input
                                  type="text"
                                  value={displayValue}
                                  onChange={(e) => setEditedValues((prev) => ({ ...prev, [v.key]: e.target.value }))}
                                  className="w-full text-[13px] font-mono"
                                  placeholder="Value"
                                />
                              )}
                            </TableCell>
                            <TableCell className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                {v.is_readonly && (
                                  <Badge variant="secondary" className="text-[9px] bg-gray-100 text-muted-foreground inline-flex items-center gap-0.5">
                                    <RiLockLine size={10} />
                                    Locked
                                  </Badge>
                                )}
                                {v.is_sensitive && (
                                  <Badge variant="secondary" className="text-[9px] bg-amber-50 text-amber-600">Sensitive</Badge>
                                )}
                                {isEdited && (
                                  <Badge variant="secondary" className="text-[9px] bg-blue-50 text-blue-600">Modified</Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                {v.is_sensitive && !(v.key in revealedKeys) && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon-xs"
                                        onClick={() => handleReveal(v.key)}
                                        className="text-muted-foreground hover:text-foreground"
                                      >
                                        <RiEyeLine size={16} />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Reveal value</TooltipContent>
                                  </Tooltip>
                                )}
                                {v.is_sensitive && v.key in revealedKeys && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon-xs"
                                        onClick={() => setRevealedKeys((prev) => { const next = { ...prev }; delete next[v.key]; return next; })}
                                        className="text-blue-500 hover:text-blue-700 hover:bg-blue-50"
                                      >
                                        <RiEyeOffLine size={16} />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Hide value</TooltipContent>
                                  </Tooltip>
                                )}
                                {!v.is_readonly && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon-xs"
                                        onClick={() => {
                                          if (confirm(`Delete variable "${v.key}"?`)) {
                                            setDeletedKeys((prev) => new Set([...prev, v.key]));
                                          }
                                        }}
                                        className="text-muted-foreground hover:text-destructive hover:bg-red-50"
                                      >
                                        <RiDeleteBinLine size={16} />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Delete variable</TooltipContent>
                                  </Tooltip>
                                )}
                                {isEdited && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon-xs"
                                        onClick={() => setEditedValues((prev) => { const next = { ...prev }; delete next[v.key]; return next; })}
                                        className="text-muted-foreground hover:text-foreground"
                                      >
                                        <RiArrowGoBackLine size={16} />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>Undo changes</TooltipContent>
                                  </Tooltip>
                                )}
                                {!v.is_readonly && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon-xs"
                                        onClick={() => handleToggleSensitive(v.key, v.is_sensitive)}
                                        className={cn("transition-colors", v.is_sensitive ? "text-amber-500 hover:text-amber-700 hover:bg-amber-50" : "text-muted-foreground hover:text-amber-500 hover:bg-amber-50")}
                                      >
                                        <RiLockLine size={16} />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>{v.is_sensitive ? "Unmark as sensitive" : "Mark as sensitive"}</TooltipContent>
                                  </Tooltip>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}

                      {/* New variable rows */}
                      {newVars.map((nv, idx) => {
                        const isDuplicate = envVars.some((v) => v.key === nv.key.trim()) || newVars.filter((n, i) => i !== idx && n.key.trim() === nv.key.trim()).length > 0;
                        return (
                          <TableRow key={`new-${idx}`} className="border-b border-border last:border-0 bg-emerald-50/20">
                            <TableCell className="py-2.5 px-4">
                              <div className="flex items-center gap-2">
                                <Input
                                  type="text"
                                  value={nv.key}
                                  onChange={(e) => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, key: e.target.value } : v))}
                                  className="w-full text-[13px] font-mono"
                                  placeholder="KEY_NAME"
                                />
                                {isDuplicate && nv.key.trim() && (
                                  <Badge variant="secondary" className="text-[9px] bg-amber-50 text-amber-600 whitespace-nowrap">Duplicate</Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="py-2.5 px-4">
                              <Input
                                type="text"
                                value={nv.value}
                                onChange={(e) => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, value: e.target.value } : v))}
                                className="w-full text-[13px] font-mono"
                                placeholder="value"
                              />
                            </TableCell>
                            <TableCell className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                <Badge variant="secondary" className="text-[9px] bg-emerald-50 text-emerald-600">New</Badge>
                                {nv.sensitive && (
                                  <Badge variant="secondary" className="text-[9px] bg-amber-50 text-amber-600">Sensitive</Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="py-2.5 px-4 text-center">
                              <div className="flex items-center justify-center gap-1">
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon-xs"
                                      onClick={() => setNewVars((prev) => prev.map((v, i) => i === idx ? { ...v, sensitive: !v.sensitive } : v))}
                                      className={cn("transition-colors", nv.sensitive ? "text-amber-500 hover:text-amber-700 hover:bg-amber-50" : "text-muted-foreground hover:text-amber-500 hover:bg-amber-50")}
                                    >
                                      <RiLockLine size={16} />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>{nv.sensitive ? "Unmark as sensitive" : "Mark as sensitive"}</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon-xs"
                                      onClick={() => setNewVars((prev) => prev.filter((_, i) => i !== idx))}
                                      className="text-muted-foreground hover:text-destructive hover:bg-red-50"
                                    >
                                      <RiCloseLine size={16} />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Remove</TooltipContent>
                                </Tooltip>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>

                {/* Add variable button */}
                <div className="px-4 py-3 border-t border-border">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setNewVars((prev) => [...prev, { key: "", value: "", sensitive: false }])}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <RiAddLine size={16} />
                    Add Variable
                  </Button>
                </div>
              </Card>
            )
          )}

          {/* Audit log sub-tab */}
          {envTab === "audit" && (
            <Card className="overflow-hidden">
              {envAuditLog.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">No audit log entries yet.</div>
              ) : (
                <div className="divide-y divide-border">
                  {envAuditLog.map((entry, idx) => {
                    const isExpanded = expandedAudit.has(idx);
                    const date = new Date(entry.timestamp);
                    const timeStr = date.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

                    return (
                      <div key={idx}>
                        <Button
                          variant="ghost"
                          onClick={() => setExpandedAudit((prev) => {
                            const next = new Set(prev);
                            next.has(idx) ? next.delete(idx) : next.add(idx);
                            return next;
                          })}
                          className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-muted/50 transition-colors h-auto rounded-none"
                        >
                          <div className="flex-1 flex items-center gap-3 min-w-0">
                            <Badge variant="secondary" className={cn("text-[10px]", entry.action === "update" ? "bg-blue-50 text-blue-600" : "bg-violet-50 text-violet-600")}>
                              {entry.action === "update" ? "Update" : "Reveal"}
                            </Badge>
                            <span className="text-sm text-foreground truncate">{entry.user_email}</span>
                            {entry.action === "update" && entry.changes && (
                              <span className="text-xs text-muted-foreground">
                                {entry.changes.length} change{entry.changes.length !== 1 ? "s" : ""}
                              </span>
                            )}
                            {entry.action === "reveal" && entry.revealed_key && (
                              <code className="text-xs text-muted-foreground font-mono">{entry.revealed_key}</code>
                            )}
                          </div>
                          <span className="text-xs text-muted-foreground flex-shrink-0">{timeStr}</span>
                          <RiArrowDownSLine
                            className={cn("text-muted-foreground transition-transform duration-200 flex-shrink-0", isExpanded && "rotate-180")}
                            size={16}
                          />
                        </Button>
                        {isExpanded && entry.changes && (
                          <div className="px-5 pb-4 pt-1 space-y-1.5">
                            {entry.changes.map((c, ci) => (
                              <div key={ci} className="flex items-start gap-2 text-[13px] font-mono">
                                <Badge variant="secondary" className={cn("text-[10px] mt-0.5 font-sans",
                                  c.type === "added" ? "bg-emerald-50 text-emerald-600"
                                    : c.type === "deleted" ? "bg-red-50 text-red-600"
                                      : "bg-amber-50 text-amber-600"
                                )}>
                                  {c.type}
                                </Badge>
                                <span className="text-foreground">{c.key}</span>
                                {c.type === "modified" && (
                                  <span className="text-muted-foreground">
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
            </Card>
          )}

        </div>
      )}

      {/* ── Settings tab ── */}
      {tab === "settings" && (
        <div className="space-y-4">
          {envError && (
            <Alert variant="destructive" className="bg-red-50 border-red-200 text-red-700">
              <RiInformationLine size={16} className="flex-shrink-0" />
              <AlertDescription>{envError}</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setEnvError(null)} className="text-red-400 hover:text-red-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}
          {envSuccess && (
            <Alert className="bg-emerald-50 border-emerald-200 text-emerald-700">
              <RiCheckLine size={16} className="flex-shrink-0" />
              <AlertDescription>{envSuccess}</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setEnvSuccess(null)} className="text-emerald-400 hover:text-emerald-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}
          {restartWarning && (
            <Alert className="bg-amber-50 border-amber-200 text-amber-700">
              <RiAlertLine size={16} className="flex-shrink-0" />
              <AlertDescription>Restart the service for this setting to take effect everywhere.</AlertDescription>
              <AlertAction>
                <Button variant="ghost" size="icon-xs" onClick={() => setRestartWarning(false)} className="text-amber-400 hover:text-amber-600">
                  <RiCloseLine size={16} />
                </Button>
              </AlertAction>
            </Alert>
          )}

          <Card className="overflow-hidden">
            <CardHeader className="px-5 py-4 border-b border-border flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <CardTitle className="text-sm">Allowed Email Domain</CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">Only users with these email domains can sign in to Loma.</p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  disabled={!hasSettingsChanges || settingsSaving}
                  onClick={handleSettingsSave}
                  className={cn(
                    "press-scale",
                    hasSettingsChanges
                      ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                      : "bg-gray-100 text-muted-foreground cursor-not-allowed"
                  )}
                >
                  {settingsSaving ? "Saving..." : "Save Settings"}
                </Button>
              </div>
            </CardHeader>

            {envLoading ? (
              <div className="flex items-center justify-center h-40">
                <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
              </div>
            ) : (
              <CardContent className="p-5">
                <Label className={cn("block rounded-lg border p-4 transition-colors",
                  hasSettingsChanges ? "border-amber-200 bg-amber-50/30" : "border-border bg-muted/30"
                )}>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <Label className="text-xs font-medium text-foreground">Allowed email domains</Label>
                    {hasSettingsChanges ? (
                      <Badge variant="secondary" className="text-[10px] bg-amber-100 text-amber-700">Modified</Badge>
                    ) : getEnvVar(ALLOWED_EMAIL_DOMAINS_KEY) ? (
                      <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">Configured</Badge>
                    ) : (
                      <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">Not set</Badge>
                    )}
                  </div>
                  <Input
                    type="text"
                    value={allowedEmailDomainsValue}
                    onChange={(e) => handleAllowedEmailDomainsChange(e.target.value)}
                    placeholder="example.com,company.com"
                    className="w-full text-[13px] font-mono"
                  />
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <code className="text-[11px] text-muted-foreground font-mono truncate">{ALLOWED_EMAIL_DOMAINS_KEY}</code>
                    {hasSettingsChanges && (
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => setSettingsValues((prev) => { const next = { ...prev }; delete next[ALLOWED_EMAIL_DOMAINS_KEY]; return next; })}
                        className="text-[11px] text-muted-foreground hover:text-foreground"
                      >
                        Undo
                      </Button>
                    )}
                  </div>
                </Label>
              </CardContent>
            )}
          </Card>

          <Card className="overflow-hidden">
            <CardHeader className="px-5 py-4 border-b border-border">
              <CardTitle className="text-sm">Core Prompt</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                These sections are stored in MongoDB and loaded into Loma&apos;s base system prompt.
              </p>
            </CardHeader>

            {promptLoading ? (
              <div className="flex items-center justify-center h-40">
                <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="divide-y divide-border">
                {promptSettings.map((setting) => {
                  const changed = isPromptChanged(setting);
                  const draft = getPromptDraft(setting);
                  return (
                    <section key={setting.setting_key} className="p-5 space-y-3">
                      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold text-foreground">{setting.title}</h3>
                            {changed ? (
                              <Badge variant="secondary" className="text-[10px] bg-amber-100 text-amber-700">Modified</Badge>
                            ) : setting.content ? (
                              <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">Configured</Badge>
                            ) : (
                              <Badge variant="secondary" className="text-[10px] bg-gray-100 text-muted-foreground">Empty</Badge>
                            )}
                          </div>
                          <div className="text-xs text-muted-foreground mt-1">
                            {setting.updated_at && setting.updated_by
                              ? `Last updated by ${setting.updated_by}`
                              : "No saved content yet"}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {setting.default_content && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setPromptDrafts((prev) => ({ ...prev, [setting.setting_key]: setting.default_content }))}
                              disabled={!canSetPromptDefault(setting)}
                            >
                              Set default
                            </Button>
                          )}
                          {changed && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setPromptDrafts((prev) => { const next = { ...prev }; delete next[setting.setting_key]; return next; })}
                            >
                              Undo
                            </Button>
                          )}
                          <Button
                            size="sm"
                            disabled={!changed || savingPromptKey === setting.setting_key}
                            onClick={() => handlePromptSave(setting)}
                            className={cn(
                              changed
                                ? "bg-accent-200 text-accent-on hover:bg-accent-300"
                                : "bg-gray-100 text-muted-foreground cursor-not-allowed"
                            )}
                          >
                            {savingPromptKey === setting.setting_key ? "Saving..." : "Save"}
                          </Button>
                        </div>
                      </div>
                      <Textarea
                        value={draft}
                        onChange={(e) => setPromptDrafts((prev) => ({ ...prev, [setting.setting_key]: e.target.value }))}
                        placeholder={setting.setting_key === "identity_guidelines"
                          ? "Describe Loma's identity, tone, operating rules, and general guidelines."
                          : "Describe the company, product, customer context, terminology, and other always-on company facts."}
                        className="w-full min-h-[260px] resize-y text-[13px] leading-6 font-mono border border-border rounded-lg px-3 py-2 bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-accent-200"
                      />
                    </section>
                  );
                })}
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ── Usage tab ── */}
      {tab === "usage" && (
        <>
          <UsagePanel />
          <div className="mt-6">
            <h3 className="text-sm font-medium text-muted-foreground mb-3">Server Terminal</h3>
            <WebTerminal />
          </div>
        </>
      )}

    </div>

    {/* Diff preview modal */}
    <Dialog open={showDiff} onOpenChange={setShowDiff}>
      <DialogContent className="max-w-lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle className="text-sm">Review Changes</DialogTitle>
          <DialogDescription className="text-xs">The following changes will be applied to the .env file.</DialogDescription>
        </DialogHeader>
        <div className="overflow-y-auto max-h-[50vh] space-y-2">
          {computeDiff().some((c) => CONNECTION_VARS.has(c.key)) && (
            <Alert className="bg-amber-50 border-amber-200 text-amber-700 mb-3">
              <RiAlertLine size={14} className="flex-shrink-0" />
              <AlertDescription className="text-xs">Some of these variables require a service restart to take effect.</AlertDescription>
            </Alert>
          )}
          {computeDiff().map((c, i) => (
            <div key={i} className={cn("flex items-start gap-2 px-3 py-2 rounded-lg text-[13px] font-mono",
              c.type === "added" ? "bg-emerald-50/50 border border-emerald-200/50"
                : c.type === "deleted" ? "bg-red-50/50 border border-red-200/50"
                  : "bg-amber-50/50 border border-amber-200/50"
            )}>
              <Badge variant="secondary" className={cn("text-[10px] font-sans mt-0.5",
                c.type === "added" ? "bg-emerald-100 text-emerald-700"
                  : c.type === "deleted" ? "bg-red-100 text-red-700"
                    : "bg-amber-100 text-amber-700"
              )}>{c.type}</Badge>
              <div className="flex-1 min-w-0">
                <div className="text-foreground font-semibold">{c.key}</div>
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
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setShowDiff(false)}
          >
            Cancel
          </Button>
          <Button
            disabled={saving}
            onClick={handleSave}
            className="bg-accent-200 text-accent-on hover:bg-accent-300 press-scale"
          >
            {saving ? "Applying..." : "Apply Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    </>
  );
}
