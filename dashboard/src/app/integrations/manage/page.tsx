"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchOAuthConnections,
  getGoogleAuthorizeUrl,
  disconnectGoogle,
  getSlackAuthorizeUrl,
  disconnectSlack,
  type OAuthConnection,
} from "../../../lib/oauth-api";
import {
  fetchClaudeAuthStatus,
  disconnectClaude,
  getClaudeLoginTerminalToken,
  type ClaudeAuthStatus,
} from "../../../lib/claude-auth-api";
import {
  fetchIntegrations,
  connectIntegration,
  disconnectIntegration,
  addCustomConnector,
  removeCustomConnector,
  getWebhookUrl,
  type Integration,
} from "../../../lib/integration-api";
import dynamic from "next/dynamic";
import { useUser } from "../../../lib/UserContext";

const WebTerminal = dynamic(() => import("../../../components/WebTerminal"), { ssr: false });

/* ── Helpers ──────────────────────────────────────────────────────── */

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const SCOPE_LABELS: Record<string, string> = {
  "https://www.googleapis.com/auth/gmail.readonly": "Read emails",
  "https://www.googleapis.com/auth/gmail.compose": "Compose & send emails",
  "https://www.googleapis.com/auth/drive": "Read & write Drive files",
  "https://www.googleapis.com/auth/calendar.readonly": "Read calendar",
  "https://www.googleapis.com/auth/calendar.events": "Manage events",
  "https://www.googleapis.com/auth/spreadsheets": "Read & edit Sheets",
  "https://www.googleapis.com/auth/documents": "Read & edit Docs",
  "https://www.googleapis.com/auth/presentations": "Read & edit Slides",
  "https://www.googleapis.com/auth/userinfo.email": "Verify identity",
};

const SLACK_SCOPE_LABELS: Record<string, string> = {
  "channels:history": "Read channel messages",
  "channels:read": "List channels",
  "groups:history": "Read private channels",
  "groups:read": "List private channels",
  "im:history": "Read DMs",
  "im:read": "List DMs",
  "mpim:history": "Read group DMs",
  "mpim:read": "List group DMs",
  "chat:write": "Send messages as you",
  "search:read": "Search messages",
  "users:read": "View users",
};

function formatScope(scope: string): string {
  return SCOPE_LABELS[scope] || SLACK_SCOPE_LABELS[scope] || scope.split("/").pop() || scope;
}

/* ── Components ───────────────────────────────────────────────────── */

function StatusBadge({ status }: { status: string }) {
  if (status === "connected")
    return (
      <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 font-medium">
        Connected
      </span>
    );
  if (status === "system_managed")
    return (
      <span className="text-xs px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 font-medium">
        System-managed
      </span>
    );
  if (status === "expired")
    return (
      <span className="text-xs px-2.5 py-1 rounded-full bg-amber-50 text-amber-600 font-medium">
        Expired
      </span>
    );
  return (
    <span className="text-xs px-2.5 py-1 rounded-full bg-gray-100 text-gray-400 font-medium">
      Not connected
    </span>
  );
}

function GoogleLogo() {
  return (
    <svg className="w-8 h-8" viewBox="0 0 24 24">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}

function SlackLogo() {
  return (
    <svg className="w-8 h-8" viewBox="0 0 24 24">
      <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" fill="#E01E5A"/>
      <path d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" fill="#36C5F0"/>
      <path d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" fill="#2EB67D"/>
      <path d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#ECB22E"/>
    </svg>
  );
}

function ClaudeLogo() {
  return (
    <img src="/claude.png" alt="Claude" className="w-8 h-8 rounded" />
  );
}

function LinearLogo() {
  return (
    <img src="/linear.png" alt="Linear" className="w-8 h-8 rounded" />
  );
}

/* ── Connect Modal ────────────────────────────────────────────────── */

function ConnectModal({
  integration,
  onClose,
  onConnected,
}: {
  integration: Integration;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [extraFieldValues, setExtraFieldValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const extraFields = integration.extra_fields || [];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim()) return;
    // Check required extra fields
    for (const field of extraFields) {
      if (field.required && !extraFieldValues[field.key]?.trim()) return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const extras = Object.fromEntries(
        Object.entries(extraFieldValues).filter(([, v]) => v.trim())
      );
      await connectIntegration(integration.provider, apiKey.trim(), webhookSecret.trim(), extras);
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4">
        <form onSubmit={handleSubmit}>
          <div className="p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-1">
              Connect {integration.display_name}
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              {integration.description}
            </p>

            {error && (
              <div className="bg-red-50 text-red-700 text-sm px-3 py-2 rounded-lg border border-red-100 mb-4">
                {error}
              </div>
            )}

            <div className="space-y-4">
              {/* Extra fields (e.g., GitBook URL) — rendered before the main auth field */}
              {extraFields.map((field) => (
                <div key={field.key}>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {field.label}
                    {!field.required && <span className="text-gray-400 font-normal ml-1">(optional)</span>}
                  </label>
                  <input
                    type="text"
                    value={extraFieldValues[field.key] || ""}
                    onChange={(e) => setExtraFieldValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                    placeholder={field.placeholder || ""}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    autoFocus={extraFields[0]?.key === field.key}
                  />
                </div>
              ))}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {integration.auth_label}
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={`Paste your ${integration.auth_label.toLowerCase()}`}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  autoFocus={extraFields.length === 0}
                />
                <a
                  href={integration.auth_help_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-blue-600 hover:text-blue-800 mt-1 inline-block"
                >
                  Where do I find this?
                </a>
              </div>

              {integration.has_webhook && integration.webhook_secret_label && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {integration.webhook_secret_label}
                    <span className="text-gray-400 font-normal ml-1">(optional)</span>
                  </label>
                  <input
                    type="password"
                    value={webhookSecret}
                    onChange={(e) => setWebhookSecret(e.target.value)}
                    placeholder="Paste your webhook signing secret"
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
              )}
            </div>
          </div>

          <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!apiKey.trim() || submitting || extraFields.some((f) => f.required && !extraFieldValues[f.key]?.trim())}
              className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {submitting ? "Connecting..." : "Connect"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────────────────── */

export default function IntegrationsPage() {
  const [connections, setConnections] = useState<OAuthConnection[]>([]);
  const [orgIntegrations, setOrgIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [connectingSlack, setConnectingSlack] = useState(false);
  const [disconnectingSlack, setDisconnectingSlack] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Org integration modal state
  const [connectModalTarget, setConnectModalTarget] = useState<Integration | null>(null);
  const [disconnectingOrg, setDisconnectingOrg] = useState<string | null>(null);
  const [webhookUrls, setWebhookUrls] = useState<Record<string, string>>({});

  // Role check — only maintainers+ can see org integrations
  const { hasRole, isAdmin } = useUser();
  const canManageOrgIntegrations = hasRole("maintainer");

  // Custom MCP connector modal state (admin only)
  const [showCustomModal, setShowCustomModal] = useState(false);
  const [customForm, setCustomForm] = useState({ name: "", url: "", token: "", authHeader: "" });
  const [customAdvanced, setCustomAdvanced] = useState(false);
  const [addingCustom, setAddingCustom] = useState(false);
  const [removingCustom, setRemovingCustom] = useState<string | null>(null);

  // Claude Code integration state
  const [claudeAuth, setClaudeAuth] = useState<ClaudeAuthStatus | null>(null);
  const [showClaudeTerminal, setShowClaudeTerminal] = useState(false);
  const [claudeAutoCommand, setClaudeAutoCommand] = useState<string | undefined>();
  const [disconnectingClaude, setDisconnectingClaude] = useState(false);

  const loadConnections = useCallback(async () => {
    try {
      const [conns, claude, orgInteg] = await Promise.all([
        fetchOAuthConnections().catch(() => []),
        fetchClaudeAuthStatus().catch(() => null),
        fetchIntegrations().catch(() => []),
      ]);
      setConnections(conns);
      if (claude) setClaudeAuth(claude);
      setOrgIntegrations(orgInteg);

      // Load webhook URLs for connected integrations
      const urls: Record<string, string> = {};
      for (const integ of orgInteg) {
        if ((integ.status === "connected" || integ.status === "system_managed") && integ.has_webhook) {
          try {
            urls[integ.provider] = await getWebhookUrl(integ.provider);
          } catch {
            // ignore
          }
        }
      }
      setWebhookUrls(urls);

      setError(null);
    } catch (e) {
      console.error("Failed to load connections:", e);
      setError("Failed to load integrations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConnections();
  }, [loadConnections]);

  // Poll claude auth status while login terminal is open
  useEffect(() => {
    if (!showClaudeTerminal) return;
    const interval = setInterval(async () => {
      try {
        const status = await fetchClaudeAuthStatus();
        if (status.connected) {
          setClaudeAuth(status);
          setShowClaudeTerminal(false);
        }
      } catch {
        // ignore polling errors
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [showClaudeTerminal]);

  // Listen for OAuth popup completion (Google or Slack)
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "oauth-complete") {
        if (event.data.provider === "slack") setConnectingSlack(false);
        else setConnecting(false);
        loadConnections();
      } else if (event.data?.type === "oauth-error") {
        if (event.data.provider === "slack") setConnectingSlack(false);
        else setConnecting(false);
        setError(event.data.error || "OAuth failed");
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [loadConnections]);

  const handleConnect = async () => {
    setConnecting(true);
    setError(null);
    try {
      const url = await getGoogleAuthorizeUrl();
      // Open in popup
      const w = 500;
      const h = 600;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      window.open(
        url,
        "google-oauth",
        `width=${w},height=${h},left=${left},top=${top},popup=yes`,
      );
    } catch (e) {
      setConnecting(false);
      setError("Failed to start OAuth flow");
    }
  };

  const handleDisconnect = async () => {
    if (!confirm("Disconnect your Google account? Loma will no longer be able to access your Gmail, Drive, Calendar, Sheets, Docs, or Slides.")) {
      return;
    }
    setDisconnecting(true);
    setError(null);
    try {
      await disconnectGoogle();
      await loadConnections();
    } catch (e) {
      setError("Failed to disconnect");
    } finally {
      setDisconnecting(false);
    }
  };

  const handleConnectSlack = async () => {
    setConnectingSlack(true);
    setError(null);
    try {
      const url = await getSlackAuthorizeUrl();
      const w = 600;
      const h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      window.open(
        url,
        "slack-oauth",
        `width=${w},height=${h},left=${left},top=${top},popup=yes`,
      );
    } catch (e) {
      setConnectingSlack(false);
      setError("Failed to start Slack OAuth flow");
    }
  };

  const handleDisconnectSlack = async () => {
    if (!confirm("Disconnect your Slack account? Loma will no longer be able to read or send messages as you.")) {
      return;
    }
    setDisconnectingSlack(true);
    setError(null);
    try {
      await disconnectSlack();
      await loadConnections();
    } catch (e) {
      setError("Failed to disconnect Slack");
    } finally {
      setDisconnectingSlack(false);
    }
  };

  const handleConnectClaude = async () => {
    setError(null);
    try {
      const { autoCommand } = await getClaudeLoginTerminalToken();
      setClaudeAutoCommand(autoCommand);
      setShowClaudeTerminal(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start Claude login");
    }
  };

  const handleDisconnectClaude = async () => {
    if (!confirm("Disconnect your Claude Code account? Tasks you trigger will use the shared account instead.")) return;
    setDisconnectingClaude(true);
    setError(null);
    try {
      await disconnectClaude();
      setClaudeAuth({ connected: false });
      setShowClaudeTerminal(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to disconnect Claude");
    } finally {
      setDisconnectingClaude(false);
    }
  };

  const handleDisconnectOrg = async (provider: string, displayName: string) => {
    if (!confirm(`Disconnect ${displayName}? Loma will lose access to ${displayName} tools and webhooks.`)) return;
    setDisconnectingOrg(provider);
    setError(null);
    try {
      await disconnectIntegration(provider);
      await loadConnections();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to disconnect ${displayName}`);
    } finally {
      setDisconnectingOrg(null);
    }
  };

  const handleOrgConnected = async () => {
    setConnectModalTarget(null);
    await loadConnections();
  };

  const handleAddCustomConnector = async () => {
    if (!customForm.name.trim() || !customForm.url.trim()) return;
    setAddingCustom(true);
    setError(null);
    try {
      await addCustomConnector({
        name: customForm.name.trim(),
        url: customForm.url.trim(),
        token: customForm.token.trim() || undefined,
        authHeader: customForm.authHeader.trim() || undefined,
      });
      setShowCustomModal(false);
      setCustomForm({ name: "", url: "", token: "", authHeader: "" });
      setCustomAdvanced(false);
      await loadConnections();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add connector");
    } finally {
      setAddingCustom(false);
    }
  };

  const handleRemoveCustomConnector = async (provider: string, displayName: string) => {
    if (!confirm(`Remove ${displayName}? Its MCP tools will no longer be available to any user.`)) return;
    setRemovingCustom(provider);
    setError(null);
    try {
      await removeCustomConnector(provider);
      await loadConnections();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to remove ${displayName}`);
    } finally {
      setRemovingCustom(null);
    }
  };

  const copyWebhookUrl = (url: string) => {
    navigator.clipboard.writeText(url);
  };

  const handleClaudeTerminalDone = () => {
    setShowClaudeTerminal(false);
    // Refresh status after login
    loadConnections();
  };

  const PROVIDER_LOGOS: Record<string, () => React.ReactNode> = {
    gitbook: () => <img src="/gitbook.png" alt="GitBook" className="w-8 h-8 rounded" />,
    clickhouse: () => <img src="/clickhouse.png" alt="ClickHouse" className="w-8 h-8 rounded" />,
    mongodb: () => <img src="/mongodb.png" alt="MongoDB" className="w-8 h-8 rounded" />,
    athena: () => <img src="/athena.png" alt="AWS Athena" className="w-8 h-8 rounded" />,
    github: () => <img src="/github-icon.png" alt="GitHub" className="w-8 h-8 rounded" />,
    hubspot: () => <img src="/hubspot.png" alt="HubSpot" className="w-8 h-8 rounded" />,
    notion: () => <img src="/notion.png" alt="Notion" className="w-8 h-8 rounded" />,
    linear: () => <LinearLogo />,
    sentry: () => <img src="/sentry.png" alt="Sentry" className="w-8 h-8 rounded" />,
  };

  const googleConn = connections.find((c) => c.provider === "google");
  const isConnected = googleConn?.status === "connected";
  const isExpired = googleConn?.status === "expired";

  const slackConn = connections.find((c) => c.provider === "slack");
  const isSlackConnected = slackConn?.status === "connected";
  const isSlackExpired = slackConn?.status === "expired";

  // Split org integrations into user-managed, system-managed, and custom connectors
  const userManagedIntegrations = orgIntegrations.filter((i) => !i.is_custom && i.status !== "system_managed");
  const systemManagedIntegrations = orgIntegrations.filter((i) => !i.is_custom && i.status === "system_managed");
  const customConnectors = orgIntegrations.filter((i) => i.is_custom);

  return (
    <div className="space-y-6 animate-fade-in-up">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Integrations</h1>
        <p className="text-sm text-gray-500 mt-1">
          Connect tools and personal accounts to let Loma work across your stack
        </p>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 text-sm px-4 py-3 rounded-lg border border-red-100">
          {error}
        </div>
      )}

      {/* Connect Modal */}
      {connectModalTarget && (
        <ConnectModal
          integration={connectModalTarget}
          onClose={() => setConnectModalTarget(null)}
          onConnected={handleOrgConnected}
        />
      )}

      {/* Add Custom Connector Modal — admin only */}
      {showCustomModal && isAdmin && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-surface rounded-2xl border border-gray-200 w-full max-w-lg p-6 shadow-xl">
            <div className="flex items-start justify-between mb-1">
              <h2 className="text-lg font-semibold text-gray-900">Add custom connector</h2>
              <button
                onClick={() => setShowCustomModal(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="text-sm text-gray-500 mb-5">
              Connect the agent to any remote MCP server. Its tools become available to every user.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Name</label>
                <input
                  type="text"
                  value={customForm.name}
                  onChange={(e) => setCustomForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="Acme MCP"
                  className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Remote MCP server URL</label>
                <input
                  type="url"
                  value={customForm.url}
                  onChange={(e) => setCustomForm((f) => ({ ...f, url: e.target.value }))}
                  placeholder="https://mcp.example.com/mcp"
                  className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>

              <button
                type="button"
                onClick={() => setCustomAdvanced((v) => !v)}
                className="text-xs font-medium text-gray-500 hover:text-gray-700"
              >
                {customAdvanced ? "▾" : "▸"} Advanced settings
              </button>
              {customAdvanced && (
                <div className="space-y-4 pl-1">
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Access token (optional)</label>
                    <input
                      type="password"
                      value={customForm.token}
                      onChange={(e) => setCustomForm((f) => ({ ...f, token: e.target.value }))}
                      placeholder="Sent as the auth header value"
                      className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">Auth header name (optional)</label>
                    <input
                      type="text"
                      value={customForm.authHeader}
                      onChange={(e) => setCustomForm((f) => ({ ...f, authHeader: e.target.value }))}
                      placeholder="Authorization"
                      className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    />
                    <p className="text-[11px] text-gray-400 mt-1">
                      Defaults to <code>Authorization</code>. The token is sent as this header&apos;s value.
                    </p>
                  </div>
                </div>
              )}

              <p className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
                Only add MCP servers you trust — their tools run for every user&apos;s agent. Interactive-OAuth
                servers are not supported; use token/header auth.
              </p>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setShowCustomModal(false)}
                className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleAddCustomConnector}
                disabled={addingCustom || !customForm.name.trim() || !customForm.url.trim()}
                className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {addingCustom ? "Adding..." : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <div className="animate-pulse text-gray-400 text-sm">Loading integrations...</div>
        </div>
      ) : (
        <div className="space-y-8">
          {/* Org Integrations — maintainer+ only */}
          {canManageOrgIntegrations && userManagedIntegrations.length > 0 && (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
                  Org Integrations
                </h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Shared across the team — connect with an API key
                </p>
              </div>

              {userManagedIntegrations.map((integ) => {
                const isOrgConnected = integ.status === "connected";
                const Logo = PROVIDER_LOGOS[integ.provider];
                return (
                  <div
                    key={integ.provider}
                    className="bg-surface rounded-xl border border-gray-200 overflow-hidden"
                  >
                    <div className="p-6">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-4">
                          <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                            {Logo ? <Logo /> : (
                              <span className="text-lg font-bold text-gray-400">
                                {integ.display_name[0]}
                              </span>
                            )}
                          </div>
                          <div>
                            <div className="flex items-center gap-3">
                              <h2 className="text-base font-semibold text-gray-900">
                                {integ.display_name}
                              </h2>
                              <StatusBadge status={integ.status} />
                            </div>
                            <p className="text-sm text-gray-500 mt-0.5">
                              {integ.description}
                            </p>
                          </div>
                        </div>

                        <div>
                          {isOrgConnected ? (
                            <button
                              onClick={() => handleDisconnectOrg(integ.provider, integ.display_name)}
                              disabled={disconnectingOrg === integ.provider}
                              className="text-sm px-4 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
                            >
                              {disconnectingOrg === integ.provider ? "Disconnecting..." : "Disconnect"}
                            </button>
                          ) : (
                            <button
                              onClick={() => setConnectModalTarget(integ)}
                              className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                            >
                              Connect
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Connected details */}
                      {isOrgConnected && (
                        <div className="mt-5 pt-5 border-t border-gray-100 space-y-3">
                          {integ.connected_at && (
                            <p className="text-xs text-gray-400">
                              Connected {formatDate(integ.connected_at)}
                              {integ.connected_by && ` by ${integ.connected_by}`}
                            </p>
                          )}

                          <div className="flex flex-wrap gap-2">
                            <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                              MCP tools active
                            </span>
                            {integ.has_webhook && integ.has_webhook_secret && (
                              <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                                Webhooks configured
                              </span>
                            )}
                            {integ.has_webhook && !integ.has_webhook_secret && (
                              <span className="text-xs px-2.5 py-1 rounded-full bg-amber-50 text-amber-600 border border-amber-100">
                                Webhook secret not set
                              </span>
                            )}
                          </div>

                          {/* Webhook URL */}
                          {integ.has_webhook && webhookUrls[integ.provider] && (
                            <div>
                              <p className="text-xs font-medium text-gray-500 mb-1">
                                Webhook URL
                              </p>
                              <div className="flex items-center gap-2">
                                <code className="text-xs bg-gray-50 text-gray-600 px-3 py-1.5 rounded-lg border border-gray-100 flex-1 truncate">
                                  {webhookUrls[integ.provider]}
                                </code>
                                <button
                                  onClick={() => copyWebhookUrl(webhookUrls[integ.provider])}
                                  className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors shrink-0"
                                >
                                  Copy
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Not connected */}
                      {!isOrgConnected && (
                        <div className="mt-5 pt-5 border-t border-gray-100">
                          <p className="text-sm text-gray-500">
                            Connect your {integ.display_name} account to enable MCP tools
                            {integ.has_webhook ? ", webhook event ingestion, and agent capabilities" : " and agent capabilities"}.
                          </p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            {["MCP tools", integ.has_webhook ? "Event ingestion" : null, "Agent skills"].filter(Boolean).map((cap) => (
                              <span
                                key={cap}
                                className="text-xs px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-600"
                              >
                                {cap}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* System-Managed Integrations */}
          {canManageOrgIntegrations && systemManagedIntegrations.length > 0 && (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
                  System-Managed Integrations
                </h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Configured via server environment — no manual setup needed
                </p>
              </div>

              {systemManagedIntegrations.map((integ) => {
                const Logo = PROVIDER_LOGOS[integ.provider];
                return (
                  <div
                    key={integ.provider}
                    className="bg-surface rounded-xl border border-gray-200 overflow-hidden"
                  >
                    <div className="p-6">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-4">
                          <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                            {Logo ? <Logo /> : (
                              <span className="text-lg font-bold text-gray-400">
                                {integ.display_name[0]}
                              </span>
                            )}
                          </div>
                          <div>
                            <div className="flex items-center gap-3">
                              <h2 className="text-base font-semibold text-gray-900">
                                {integ.display_name}
                              </h2>
                              <StatusBadge status="system_managed" />
                            </div>
                            <p className="text-sm text-gray-500 mt-0.5">
                              {integ.description}
                            </p>
                          </div>
                        </div>
                      </div>

                      <div className="mt-5 pt-5 border-t border-gray-100">
                        <div className="flex flex-wrap gap-2">
                          <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                            MCP tools active
                          </span>
                          <span className="text-xs px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 border border-blue-100">
                            Configured via server environment
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Custom MCP Connectors — admin only */}
          {isAdmin && (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
                    Custom Connectors
                  </h2>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Add any remote MCP server — its tools become available to every user
                  </p>
                </div>
                <button
                  onClick={() => setShowCustomModal(true)}
                  className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors shrink-0"
                >
                  Add custom connector
                </button>
              </div>

              {customConnectors.length === 0 ? (
                <div className="bg-surface rounded-xl border border-dashed border-gray-200 p-6 text-center">
                  <p className="text-sm text-gray-400">
                    No custom connectors yet. Add a remote MCP server to extend the agent.
                  </p>
                </div>
              ) : (
                customConnectors.map((integ) => (
                  <div
                    key={integ.provider}
                    className="bg-surface rounded-xl border border-gray-200 overflow-hidden"
                  >
                    <div className="p-6">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-4">
                          <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                            <span className="text-lg font-bold text-gray-400">
                              {integ.display_name[0]}
                            </span>
                          </div>
                          <div>
                            <div className="flex items-center gap-3">
                              <h2 className="text-base font-semibold text-gray-900">
                                {integ.display_name}
                              </h2>
                              <StatusBadge status="connected" />
                            </div>
                            <p className="text-sm text-gray-500 mt-0.5 font-mono break-all">
                              {integ.url}
                            </p>
                          </div>
                        </div>
                        <button
                          onClick={() => handleRemoveCustomConnector(integ.provider, integ.display_name)}
                          disabled={removingCustom === integ.provider}
                          className="text-sm px-4 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50 shrink-0"
                        >
                          {removingCustom === integ.provider ? "Removing..." : "Remove"}
                        </button>
                      </div>
                      <div className="mt-5 pt-5 border-t border-gray-100 flex flex-wrap items-center gap-2">
                        <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                          MCP tools active (mcp__{integ.provider})
                        </span>
                        <span className="text-xs px-2.5 py-1 rounded-full bg-gray-50 text-gray-500 border border-gray-100">
                          {integ.has_token ? "Token auth" : "No auth"}
                        </span>
                        {integ.connected_by && (
                          <span className="text-xs text-gray-400">
                            Added {formatDate(integ.connected_at)} by {integ.connected_by}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Personal Integrations */}
          <div className="space-y-4">
            {canManageOrgIntegrations && orgIntegrations.length > 0 && (
              <div>
                <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
                  Personal Integrations
                </h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Scoped to your account — Loma acts on your behalf
                </p>
              </div>
            )}

          {/* Google Integration Card */}
          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                    <GoogleLogo />
                  </div>
                  <div>
                    <div className="flex items-center gap-3">
                      <h2 className="text-base font-semibold text-gray-900">
                        Google
                      </h2>
                      <StatusBadge status={googleConn?.status || "not_connected"} />
                    </div>
                    <p className="text-sm text-gray-500 mt-0.5">
                      Gmail, Drive, Calendar, Sheets, Docs, Slides
                    </p>
                  </div>
                </div>

                <div>
                  {isConnected ? (
                    <button
                      onClick={handleDisconnect}
                      disabled={disconnecting}
                      className="text-sm px-4 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
                    >
                      {disconnecting ? "Disconnecting..." : "Disconnect"}
                    </button>
                  ) : (
                    <button
                      onClick={handleConnect}
                      disabled={connecting}
                      className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
                    >
                      {connecting ? "Connecting..." : isExpired ? "Reconnect" : "Connect Google"}
                    </button>
                  )}
                </div>
              </div>

              {/* Connected details */}
              {isConnected && googleConn && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  {googleConn.connected_at && (
                    <p className="text-xs text-gray-400 mb-3">
                      Connected {formatDate(googleConn.connected_at)}
                    </p>
                  )}
                  <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
                      Permissions granted
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {(googleConn.scopes || []).map((scope) => (
                        <span
                          key={scope}
                          className="text-xs px-2.5 py-1 rounded-full bg-gray-50 text-gray-600 border border-gray-100"
                        >
                          {formatScope(scope)}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Expired notice */}
              {isExpired && (
                <div className="mt-4 p-3 bg-amber-50 rounded-lg border border-amber-100">
                  <p className="text-sm text-amber-700">
                    Your Google connection has expired. Please reconnect to restore access.
                  </p>
                </div>
              )}

              {/* Not connected — description */}
              {!isConnected && !isExpired && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  <p className="text-sm text-gray-500">
                    Connect your Google account to let Loma access Gmail, Drive, Calendar,
                    Sheets, Docs, and Slides on your behalf. Your tokens are encrypted and stored securely.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {["Read emails", "Compose & send emails", "Read & write Drive", "Calendar", "Sheets", "Read & edit Docs", "Read & edit Slides"].map((perm) => (
                      <span
                        key={perm}
                        className="text-xs px-2.5 py-1 rounded-full bg-blue-50 text-blue-600"
                      >
                        {perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Slack Integration Card */}
          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                    <SlackLogo />
                  </div>
                  <div>
                    <div className="flex items-center gap-3">
                      <h2 className="text-base font-semibold text-gray-900">
                        Slack
                      </h2>
                      <StatusBadge status={slackConn?.status || "not_connected"} />
                    </div>
                    <p className="text-sm text-gray-500 mt-0.5">
                      Read, search, and send messages as you
                    </p>
                  </div>
                </div>

                <div>
                  {isSlackConnected ? (
                    <button
                      onClick={handleDisconnectSlack}
                      disabled={disconnectingSlack}
                      className="text-sm px-4 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
                    >
                      {disconnectingSlack ? "Disconnecting..." : "Disconnect"}
                    </button>
                  ) : (
                    <button
                      onClick={handleConnectSlack}
                      disabled={connectingSlack}
                      className="text-sm px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
                    >
                      {connectingSlack ? "Connecting..." : isSlackExpired ? "Reconnect" : "Connect Slack"}
                    </button>
                  )}
                </div>
              </div>

              {/* Connected details */}
              {isSlackConnected && slackConn && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  {slackConn.connected_at && (
                    <p className="text-xs text-gray-400 mb-3">
                      Connected {formatDate(slackConn.connected_at)}
                    </p>
                  )}
                  <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
                      Permissions granted
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {(slackConn.scopes || []).map((scope) => (
                        <span
                          key={scope}
                          className="text-xs px-2.5 py-1 rounded-full bg-gray-50 text-gray-600 border border-gray-100"
                        >
                          {formatScope(scope)}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Expired notice */}
              {isSlackExpired && (
                <div className="mt-4 p-3 bg-amber-50 rounded-lg border border-amber-100">
                  <p className="text-sm text-amber-700">
                    Your Slack connection has expired. Please reconnect to restore access.
                  </p>
                </div>
              )}

              {/* Not connected — description */}
              {!isSlackConnected && !isSlackExpired && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  <p className="text-sm text-gray-500">
                    Connect your Slack account to let Loma read channels, search messages, view unreads,
                    and send messages as you. Your token is encrypted and stored securely.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {["Read channels", "Read DMs", "Send messages as you", "Search messages", "View unreads"].map((perm) => (
                      <span
                        key={perm}
                        className="text-xs px-2.5 py-1 rounded-full bg-purple-50 text-purple-600"
                      >
                        {perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Claude Code Integration Card */}
          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center">
                    <ClaudeLogo />
                  </div>
                  <div>
                    <div className="flex items-center gap-3">
                      <h2 className="text-base font-semibold text-gray-900">
                        Claude Code
                      </h2>
                      <StatusBadge status={claudeAuth?.connected ? "connected" : "not_connected"} />
                    </div>
                    <p className="text-sm text-gray-500 mt-0.5">
                      Your account joins the shared round-robin pool
                    </p>
                  </div>
                </div>

                <div>
                  {claudeAuth?.connected ? (
                    <button
                      onClick={handleDisconnectClaude}
                      disabled={disconnectingClaude}
                      className="text-sm px-4 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
                    >
                      {disconnectingClaude ? "Disconnecting..." : "Disconnect"}
                    </button>
                  ) : (
                    <button
                      onClick={handleConnectClaude}
                      disabled={showClaudeTerminal}
                      className="text-sm px-4 py-2 rounded-lg bg-[#D97706] text-white hover:bg-[#B45309] transition-colors disabled:opacity-50"
                    >
                      {showClaudeTerminal ? "Logging in..." : "Login with Claude"}
                    </button>
                  )}
                </div>
              </div>

              {/* Connected details */}
              {claudeAuth?.connected && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  <div className="flex items-center gap-4 text-sm">
                    {claudeAuth.email && (
                      <div>
                        <span className="text-gray-500">Account: </span>
                        <span className="text-gray-900">{claudeAuth.email}</span>
                      </div>
                    )}
                    {claudeAuth.authMethod && (
                      <div>
                        <span className="text-gray-500">Auth: </span>
                        <span className="text-gray-900">{claudeAuth.authMethod}</span>
                      </div>
                    )}
                    <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600">
                      In round-robin pool
                    </span>
                  </div>
                </div>
              )}

              {/* Login terminal */}
              {showClaudeTerminal && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-sm text-gray-600">
                      Complete the OAuth flow in the terminal below:
                    </p>
                    <button
                      onClick={handleClaudeTerminalDone}
                      className="text-xs text-blue-600 hover:text-blue-800"
                    >
                      Done
                    </button>
                  </div>
                  <WebTerminal
                    autoCommand={claudeAutoCommand}
                    tokenEndpoint="/api/terminal/token"
                  />
                </div>
              )}

              {/* Not connected — description */}
              {!claudeAuth?.connected && !showClaudeTerminal && (
                <div className="mt-5 pt-5 border-t border-gray-100">
                  <p className="text-sm text-gray-500">
                    Connect your Claude Code subscription (Pro, Max, or Teams) to join the shared
                    round-robin pool. All connected accounts are used to process tasks across the team.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {["Shared pool", "Round-robin usage", "Rate limit rotation"].map((perm) => (
                      <span
                        key={perm}
                        className="text-xs px-2.5 py-1 rounded-full bg-amber-50 text-amber-600"
                      >
                        {perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
          </div>
        </div>
      )}

      {/* Info section */}
      <div className="bg-gray-50 rounded-xl border border-gray-100 p-5">
        <h3 className="text-sm font-medium text-gray-700 mb-2">How it works</h3>
        <ul className="text-sm text-gray-500 space-y-1.5">
          <li>Org integrations are shared across the team — connect with an API key</li>
          <li>System-managed integrations are configured on the server — no setup needed</li>
          <li>Personal integrations are scoped to your account only</li>
          <li>All tokens and keys are encrypted at rest</li>
          <li>You can disconnect at any time to revoke access</li>
        </ul>
      </div>
    </div>
  );
}
