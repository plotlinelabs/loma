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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RiCloseLine, RiFileCopyLine, RiArrowDownSLine, RiArrowRightSLine } from "@remixicon/react";

const WebTerminal = dynamic(() => import("../../../components/WebTerminal"), { ssr: false });

/* -- Helpers ---------------------------------------------------------------- */

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

/* -- Components ------------------------------------------------------------- */

function StatusBadge({ status }: { status: string }) {
  if (status === "connected")
    return (
      <Badge className="bg-emerald-50 text-emerald-600 border-transparent">
        Connected
      </Badge>
    );
  if (status === "system_managed")
    return (
      <Badge className="bg-blue-50 text-blue-600 border-transparent">
        System-managed
      </Badge>
    );
  if (status === "expired")
    return (
      <Badge className="bg-amber-50 text-amber-600 border-transparent">
        Expired
      </Badge>
    );
  return (
    <Badge variant="secondary">
      Not connected
    </Badge>
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

/* -- Connect Modal ---------------------------------------------------------- */

function ConnectModal({
  integration,
  open,
  onClose,
  onConnected,
}: {
  integration: Integration;
  open: boolean;
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
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Connect {integration.display_name}</DialogTitle>
            <DialogDescription>{integration.description}</DialogDescription>
          </DialogHeader>

          {error && (
            <Alert variant="destructive" className="mt-4">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-4 mt-2">
            {extraFields.map((field) => (
              <div key={field.key}>
                <Label className="mb-1">
                  {field.label}
                  {!field.required && <span className="text-muted-foreground font-normal ml-1">(optional)</span>}
                </Label>
                <Input
                  type="text"
                  value={extraFieldValues[field.key] || ""}
                  onChange={(e) => setExtraFieldValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                  placeholder={field.placeholder || ""}
                  autoFocus={extraFields[0]?.key === field.key}
                />
              </div>
            ))}

            <div>
              <Label className="mb-1">{integration.auth_label}</Label>
              <Input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={`Paste your ${integration.auth_label.toLowerCase()}`}
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
                <Label className="mb-1">
                  {integration.webhook_secret_label}
                  <span className="text-muted-foreground font-normal ml-1">(optional)</span>
                </Label>
                <Input
                  type="password"
                  value={webhookSecret}
                  onChange={(e) => setWebhookSecret(e.target.value)}
                  placeholder="Paste your webhook signing secret"
                />
              </div>
            )}
          </div>

          <DialogFooter className="mt-4">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!apiKey.trim() || submitting || extraFields.some((f) => f.required && !extraFieldValues[f.key]?.trim())}
            >
              {submitting ? "Connecting..." : "Connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* -- Main Page -------------------------------------------------------------- */

export default function IntegrationsPage() {
  const [connections, setConnections] = useState<OAuthConnection[]>([]);
  const [orgIntegrations, setOrgIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [connectingSlack, setConnectingSlack] = useState(false);
  const [disconnectingSlack, setDisconnectingSlack] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [connectModalTarget, setConnectModalTarget] = useState<Integration | null>(null);
  const [disconnectingOrg, setDisconnectingOrg] = useState<string | null>(null);
  const [webhookUrls, setWebhookUrls] = useState<Record<string, string>>({});

  const { hasRole, isAdmin } = useUser();
  const canManageOrgIntegrations = hasRole("maintainer");

  const [showCustomModal, setShowCustomModal] = useState(false);
  const [customForm, setCustomForm] = useState({ name: "", url: "", token: "", authHeader: "" });
  const [customAdvanced, setCustomAdvanced] = useState(false);
  const [addingCustom, setAddingCustom] = useState(false);
  const [removingCustom, setRemovingCustom] = useState<string | null>(null);

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
      const w = 500;
      const h = 600;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(
        url,
        "google-oauth",
        `width=${w},height=${h},left=${left},top=${top},popup=yes`,
      );
      if (!popup) {
        setConnecting(false);
        setError("Popup blocked. Allow popups for this site and try again.");
      }
    } catch (e) {
      setConnecting(false);
      setError(e instanceof Error ? e.message : "Failed to start OAuth flow");
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
      setError(e instanceof Error ? e.message : "Failed to disconnect");
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
      const popup = window.open(
        url,
        "slack-oauth",
        `width=${w},height=${h},left=${left},top=${top},popup=yes`,
      );
      if (!popup) {
        setConnectingSlack(false);
        setError("Popup blocked. Allow popups for this site and try again.");
      }
    } catch (e) {
      setConnectingSlack(false);
      setError(e instanceof Error ? e.message : "Failed to start Slack OAuth flow");
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
      setError(e instanceof Error ? e.message : "Failed to disconnect Slack");
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

  const userManagedIntegrations = orgIntegrations.filter((i) => !i.is_custom && i.status !== "system_managed");
  const systemManagedIntegrations = orgIntegrations.filter((i) => !i.is_custom && i.status === "system_managed");
  const customConnectors = orgIntegrations.filter((i) => i.is_custom);

  return (
    <div className="space-y-2 animate-fade-in-up">
      {/* Header */}
      <div>
        <h1 className="text-xl font-heading font-semibold text-foreground">Integrations</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Connect tools and personal accounts to let Loma work across your stack
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Connect Modal */}
      {connectModalTarget && (
        <ConnectModal
          integration={connectModalTarget}
          open={!!connectModalTarget}
          onClose={() => setConnectModalTarget(null)}
          onConnected={handleOrgConnected}
        />
      )}

      {/* Add Custom Connector Modal -- admin only */}
      <Dialog open={showCustomModal && isAdmin} onOpenChange={(o) => { if (!o) setShowCustomModal(false); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add custom connector</DialogTitle>
            <DialogDescription>
              Connect the agent to any remote MCP server. Its tools become available to every user.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <Label className="mb-1">Name</Label>
              <Input
                type="text"
                value={customForm.name}
                onChange={(e) => setCustomForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Acme MCP"
              />
            </div>
            <div>
              <Label className="mb-1">Remote MCP server URL</Label>
              <Input
                type="url"
                value={customForm.url}
                onChange={(e) => setCustomForm((f) => ({ ...f, url: e.target.value }))}
                placeholder="https://mcp.example.com/mcp"
              />
            </div>

            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={() => setCustomAdvanced((v) => !v)}
            >
              {customAdvanced ? <RiArrowDownSLine size={12} /> : <RiArrowRightSLine size={12} />}
              Advanced settings
            </Button>
            {customAdvanced && (
              <div className="space-y-4 pl-1">
                <div>
                  <Label className="mb-1">Access token (optional)</Label>
                  <Input
                    type="password"
                    value={customForm.token}
                    onChange={(e) => setCustomForm((f) => ({ ...f, token: e.target.value }))}
                    placeholder="Sent as the auth header value"
                  />
                </div>
                <div>
                  <Label className="mb-1">Auth header name (optional)</Label>
                  <Input
                    type="text"
                    value={customForm.authHeader}
                    onChange={(e) => setCustomForm((f) => ({ ...f, authHeader: e.target.value }))}
                    placeholder="Authorization"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Defaults to <code>Authorization</code>. The token is sent as this header&apos;s value.
                  </p>
                </div>
              </div>
            )}

            <Alert>
              <AlertDescription className="text-xs text-amber-600">
                Only add MCP servers you trust — their tools run for every user&apos;s agent. Interactive-OAuth
                servers are not supported; use token/header auth.
              </AlertDescription>
            </Alert>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCustomModal(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleAddCustomConnector}
              disabled={addingCustom || !customForm.name.trim() || !customForm.url.trim()}
            >
              {addingCustom ? "Adding..." : "Add"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {loading ? (
        <Card>
          <CardContent className="p-6 text-center">
            <div className="animate-pulse text-muted-foreground text-sm">Loading integrations...</div>
          </CardContent>
        </Card>
      ) : (
        <Tabs defaultValue="org">
          <TabsList>
            <TabsTrigger value="org">Org</TabsTrigger>
            <TabsTrigger value="system">System</TabsTrigger>
            <TabsTrigger value="custom">Custom</TabsTrigger>
            <TabsTrigger value="personal">Personal</TabsTrigger>
          </TabsList>

          {/* Org Integrations */}
          <TabsContent value="org">
            {canManageOrgIntegrations && userManagedIntegrations.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
              {userManagedIntegrations.map((integ) => {
                const isOrgConnected = integ.status === "connected";
                const Logo = PROVIDER_LOGOS[integ.provider];
                return (
                  <Card key={integ.provider}>
                    <CardContent>
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center shrink-0">
                          {Logo ? <Logo /> : (
                            <span className="text-lg font-bold text-muted-foreground">
                              {integ.display_name[0]}
                            </span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <h2 className="text-sm font-semibold text-foreground truncate">
                            {integ.display_name}
                          </h2>
                          <p className="text-xs text-muted-foreground line-clamp-1">
                            {integ.description}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center justify-between mt-2">
                        <StatusBadge status={integ.status} />
                        {isOrgConnected ? (
                          <Button
                            variant="destructive"
                            size="xs"
                            onClick={() => handleDisconnectOrg(integ.provider, integ.display_name)}
                            disabled={disconnectingOrg === integ.provider}
                          >
                            {disconnectingOrg === integ.provider ? "..." : "Disconnect"}
                          </Button>
                        ) : (
                          <Button
                            size="xs"
                            onClick={() => setConnectModalTarget(integ)}
                          >
                            Connect
                          </Button>
                        )}
                      </div>

                      {isOrgConnected && (
                        <>
                          <Separator className="my-5" />
                          <div className="space-y-2">
                            {integ.connected_at && (
                              <p className="text-xs text-muted-foreground">
                                Connected {formatDate(integ.connected_at)}
                                {integ.connected_by && ` by ${integ.connected_by}`}
                              </p>
                            )}

                            <div className="flex flex-wrap gap-2">
                              <Badge className="bg-emerald-50 text-emerald-600 border-emerald-100">
                                MCP tools active
                              </Badge>
                              {integ.has_webhook && integ.has_webhook_secret && (
                                <Badge className="bg-emerald-50 text-emerald-600 border-emerald-100">
                                  Webhooks configured
                                </Badge>
                              )}
                              {integ.has_webhook && !integ.has_webhook_secret && (
                                <Badge className="bg-amber-50 text-amber-600 border-amber-100">
                                  Webhook secret not set
                                </Badge>
                              )}
                            </div>

                            {integ.has_webhook && webhookUrls[integ.provider] && (
                              <div>
                                <p className="text-xs font-medium text-muted-foreground mb-1">
                                  Webhook URL
                                </p>
                                <div className="flex items-center gap-2">
                                  <code className="text-xs bg-muted text-muted-foreground px-3 py-1.5 rounded-lg border border-border flex-1 truncate">
                                    {webhookUrls[integ.provider]}
                                  </code>
                                  <Button
                                    variant="outline"
                                    size="xs"
                                    onClick={() => copyWebhookUrl(webhookUrls[integ.provider])}
                                  >
                                    <RiFileCopyLine size={12} />
                                    Copy
                                  </Button>
                                </div>
                              </div>
                            )}
                          </div>
                        </>
                      )}

                      {!isOrgConnected && (
                        <>
                          <Separator className="my-5" />
                          <p className="text-sm text-muted-foreground">
                            Connect your {integ.display_name} account to enable MCP tools
                            {integ.has_webhook ? ", webhook event ingestion, and agent capabilities" : " and agent capabilities"}.
                          </p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {["MCP tools", integ.has_webhook ? "Event ingestion" : null, "Agent skills"].filter(Boolean).map((cap) => (
                              <Badge
                                key={cap}
                                className="bg-indigo-50 text-indigo-600 border-transparent"
                              >
                                {cap}
                              </Badge>
                            ))}
                          </div>
                        </>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
              </div>
            ) : (
              <Card className="border-dashed">
                <CardContent className="p-3 text-center">
                  <p className="text-sm text-muted-foreground">
                    No org integrations available.
                  </p>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* System-Managed Integrations */}
          <TabsContent value="system">
            {canManageOrgIntegrations && systemManagedIntegrations.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
              {systemManagedIntegrations.map((integ) => {
                const Logo = PROVIDER_LOGOS[integ.provider];
                return (
                  <Card key={integ.provider}>
                    <CardContent>
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center shrink-0">
                          {Logo ? <Logo /> : (
                            <span className="text-lg font-bold text-muted-foreground">
                              {integ.display_name[0]}
                            </span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <h2 className="text-sm font-semibold text-foreground truncate">
                            {integ.display_name}
                          </h2>
                          <p className="text-xs text-muted-foreground line-clamp-1">
                            {integ.description}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 mt-2">
                        <Badge variant="outline" className="text-[10px] bg-emerald-50 text-emerald-600 border-emerald-100">
                          MCP tools
                        </Badge>
                        <Badge variant="outline" className="text-[10px] bg-blue-50 text-blue-600 border-blue-100">
                          Server configured
                        </Badge>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
              </div>
            ) : (
              <Card className="border-dashed">
                <CardContent className="p-3 text-center">
                  <p className="text-sm text-muted-foreground">
                    No system-managed integrations configured.
                  </p>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Custom MCP Connectors */}
          <TabsContent value="custom">
            <div className="space-y-2">
              {isAdmin && (
                <div className="flex justify-end">
                  <Button size="sm" onClick={() => setShowCustomModal(true)}>
                    Add custom connector
                  </Button>
                </div>
              )}

              {customConnectors.length === 0 ? (
                <Card className="border-dashed">
                  <CardContent className="p-3 text-center">
                    <p className="text-sm text-muted-foreground">
                      No custom connectors yet.{isAdmin ? " Add a remote MCP server to extend the agent." : ""}
                    </p>
                  </CardContent>
                </Card>
              ) : (
                customConnectors.map((integ) => (
                  <Card key={integ.provider}>
                    <CardContent>
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center">
                            <span className="text-lg font-bold text-muted-foreground">
                              {integ.display_name[0]}
                            </span>
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <h2 className="text-sm font-semibold text-foreground">
                                {integ.display_name}
                              </h2>
                              <StatusBadge status="connected" />
                            </div>
                            <p className="text-sm text-muted-foreground mt-0.5 font-mono break-all">
                              {integ.url}
                            </p>
                          </div>
                        </div>
                        {isAdmin && (
                          <Button
                            variant="destructive"
                            size="sm"
                            className="shrink-0 whitespace-nowrap"
                            onClick={() => handleRemoveCustomConnector(integ.provider, integ.display_name)}
                            disabled={removingCustom === integ.provider}
                          >
                            {removingCustom === integ.provider ? "Removing..." : "Remove"}
                          </Button>
                        )}
                      </div>
                      <Separator className="my-5" />
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className="bg-emerald-50 text-emerald-600 border-emerald-100">
                          MCP tools active (mcp__{integ.provider})
                        </Badge>
                        <Badge variant="secondary">
                          {integ.has_token ? "Token auth" : "No auth"}
                        </Badge>
                        {integ.connected_by && (
                          <span className="text-xs text-muted-foreground">
                            Added {formatDate(integ.connected_at)} by {integ.connected_by}
                          </span>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))
              )}
            </div>
          </TabsContent>

          {/* Personal Integrations */}
          <TabsContent value="personal">
            <div className="space-y-2">
              {/* Google Integration Card */}
              <Card>
                <CardContent>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center">
                        <GoogleLogo />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h2 className="text-sm font-semibold text-foreground">
                            Google
                          </h2>
                          <StatusBadge status={googleConn?.status || "not_connected"} />
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                          Gmail, Drive, Calendar, Sheets, Docs, Slides
                        </p>
                      </div>
                    </div>

                    <div className="shrink-0">
                      {isConnected ? (
                        <Button
                          variant="destructive"
                          size="sm"
                          className="whitespace-nowrap"
                          onClick={handleDisconnect}
                          disabled={disconnecting}
                        >
                          {disconnecting ? "Disconnecting..." : "Disconnect"}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          className="whitespace-nowrap"
                          onClick={handleConnect}
                          disabled={connecting}
                        >
                          {connecting ? "Connecting..." : isExpired ? "Reconnect" : "Connect Google"}
                        </Button>
                      )}
                    </div>
                  </div>

                  {isConnected && googleConn && (
                    <>
                      <Separator className="my-5" />
                      {googleConn.connected_at && (
                        <p className="text-xs text-muted-foreground mb-2">
                          Connected {formatDate(googleConn.connected_at)}
                        </p>
                      )}
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
                          Permissions granted
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {(googleConn.scopes || []).map((scope) => (
                            <Badge
                              key={scope}
                              variant="outline"
                            >
                              {formatScope(scope)}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    </>
                  )}

                  {isExpired && (
                    <Alert className="mt-2 bg-amber-50 border-amber-100">
                      <AlertDescription className="text-amber-700">
                        Your Google connection has expired. Please reconnect to restore access.
                      </AlertDescription>
                    </Alert>
                  )}

                  {!isConnected && !isExpired && (
                    <>
                      <Separator className="my-5" />
                      <p className="text-sm text-muted-foreground">
                        Connect your Google account to let Loma access Gmail, Drive, Calendar,
                        Sheets, Docs, and Slides on your behalf. Your tokens are encrypted and stored securely.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {["Read emails", "Compose & send emails", "Read & write Drive", "Calendar", "Sheets", "Read & edit Docs", "Read & edit Slides"].map((perm) => (
                          <Badge
                            key={perm}
                            className="bg-blue-50 text-blue-600 border-transparent"
                          >
                            {perm}
                          </Badge>
                        ))}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              {/* Slack Integration Card */}
              <Card>
                <CardContent>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center">
                        <SlackLogo />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h2 className="text-sm font-semibold text-foreground">
                            Slack
                          </h2>
                          <StatusBadge status={slackConn?.status || "not_connected"} />
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                          Read, search, and send messages as you
                        </p>
                      </div>
                    </div>

                    <div className="shrink-0">
                      {isSlackConnected ? (
                        <Button
                          variant="destructive"
                          size="sm"
                          className="whitespace-nowrap"
                          onClick={handleDisconnectSlack}
                          disabled={disconnectingSlack}
                        >
                          {disconnectingSlack ? "Disconnecting..." : "Disconnect"}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          className="whitespace-nowrap"
                          onClick={handleConnectSlack}
                          disabled={connectingSlack}
                        >
                          {connectingSlack ? "Connecting..." : isSlackExpired ? "Reconnect" : "Connect Slack"}
                        </Button>
                      )}
                    </div>
                  </div>

                  {isSlackConnected && slackConn && (
                    <>
                      <Separator className="my-5" />
                      {slackConn.connected_at && (
                        <p className="text-xs text-muted-foreground mb-2">
                          Connected {formatDate(slackConn.connected_at)}
                        </p>
                      )}
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">
                          Permissions granted
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {(slackConn.scopes || []).map((scope) => (
                            <Badge
                              key={scope}
                              variant="outline"
                            >
                              {formatScope(scope)}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    </>
                  )}

                  {isSlackExpired && (
                    <Alert className="mt-2 bg-amber-50 border-amber-100">
                      <AlertDescription className="text-amber-700">
                        Your Slack connection has expired. Please reconnect to restore access.
                      </AlertDescription>
                    </Alert>
                  )}

                  {!isSlackConnected && !isSlackExpired && (
                    <>
                      <Separator className="my-5" />
                      <p className="text-sm text-muted-foreground">
                        Connect your Slack account to let Loma read channels, search messages, view unreads,
                        and send messages as you. Your token is encrypted and stored securely.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {["Read channels", "Read DMs", "Send messages as you", "Search messages", "View unreads"].map((perm) => (
                          <Badge
                            key={perm}
                            className="bg-purple-50 text-purple-600 border-transparent"
                          >
                            {perm}
                          </Badge>
                        ))}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              {/* Claude Code Integration Card */}
              <Card>
                <CardContent>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-muted rounded-lg flex items-center justify-center">
                        <ClaudeLogo />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h2 className="text-sm font-semibold text-foreground">
                            Claude Code
                          </h2>
                          <StatusBadge status={claudeAuth?.connected ? "connected" : "not_connected"} />
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                          Your account joins the shared round-robin pool
                        </p>
                      </div>
                    </div>

                    <div className="shrink-0">
                      {claudeAuth?.connected ? (
                        <Button
                          variant="destructive"
                          size="sm"
                          className="whitespace-nowrap"
                          onClick={handleDisconnectClaude}
                          disabled={disconnectingClaude}
                        >
                          {disconnectingClaude ? "Disconnecting..." : "Disconnect"}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          className="bg-amber-600 text-white hover:bg-amber-700 whitespace-nowrap"
                          onClick={handleConnectClaude}
                          disabled={showClaudeTerminal}
                        >
                          {showClaudeTerminal ? "Logging in..." : "Login with Claude"}
                        </Button>
                      )}
                    </div>
                  </div>

                  {claudeAuth?.connected && (
                    <>
                      <Separator className="my-5" />
                      <div className="flex items-center gap-3 text-sm">
                        {claudeAuth.email && (
                          <div>
                            <span className="text-muted-foreground">Account: </span>
                            <span className="text-foreground">{claudeAuth.email}</span>
                          </div>
                        )}
                        {claudeAuth.authMethod && (
                          <div>
                            <span className="text-muted-foreground">Auth: </span>
                            <span className="text-foreground">{claudeAuth.authMethod}</span>
                          </div>
                        )}
                        <Badge className="bg-emerald-50 text-emerald-600 border-transparent">
                          In round-robin pool
                        </Badge>
                      </div>
                    </>
                  )}

                  {showClaudeTerminal && (
                    <>
                      <Separator className="my-5" />
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-sm text-muted-foreground">
                          Complete the OAuth flow in the terminal below:
                        </p>
                        <Button variant="link" size="xs" onClick={handleClaudeTerminalDone}>
                          Done
                        </Button>
                      </div>
                      <WebTerminal
                        autoCommand={claudeAutoCommand}
                        tokenEndpoint="/api/terminal/token"
                      />
                    </>
                  )}

                  {!claudeAuth?.connected && !showClaudeTerminal && (
                    <>
                      <Separator className="my-5" />
                      <p className="text-sm text-muted-foreground">
                        Connect your Claude Code subscription (Pro, Max, or Teams) to join the shared
                        round-robin pool. All connected accounts are used to process tasks across the team.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {["Shared pool", "Round-robin usage", "Rate limit rotation"].map((perm) => (
                          <Badge
                            key={perm}
                            className="bg-amber-50 text-amber-600 border-transparent"
                          >
                            {perm}
                          </Badge>
                        ))}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>
      )}

      {/* Info section */}
      <Card className="bg-muted/50">
        <CardContent>
          <h3 className="text-sm font-medium text-foreground mb-2">How it works</h3>
          <ul className="text-sm text-muted-foreground space-y-1.5">
            <li>Org integrations are shared across the team — connect with an API key</li>
            <li>System-managed integrations are configured on the server — no setup needed</li>
            <li>Personal integrations are scoped to your account only</li>
            <li>All tokens and keys are encrypted at rest</li>
            <li>You can disconnect at any time to revoke access</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
