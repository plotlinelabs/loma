"use client";

import { useCallback, useEffect, useState } from "react";
import {
  RiAddLine,
  RiDeleteBinLine,
  RiFileCopyLine,
  RiKey2Line,
  RiCheckLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import ClientTimestamp from "@/components/ClientTimestamp";
import {
  ApiKeyRecord,
  createApiKey,
  fetchApiKeys,
  revokeApiKey,
} from "@/lib/api-keys-api";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-7 w-7 p-0 shrink-0"
      aria-label="Copy"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <RiCheckLine size={14} className="text-green-500" /> : <RiFileCopyLine size={14} />}
    </Button>
  );
}

/**
 * Personal API keys for the loma-tasks MCP server.
 * Rendered as the "API Keys" tab on the Integrations page.
 */
export default function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKeyRecord[] | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mcpUrl, setMcpUrl] = useState("");

  const load = useCallback(async () => {
    try {
      setKeys(await fetchApiKeys());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load keys");
      setKeys([]);
    }
  }, []);

  useEffect(() => {
    load();
    setMcpUrl(`${window.location.origin}/mcp/tasks`);
  }, [load]);

  const onCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const { key } = await createApiKey(name.trim() || "Unnamed key");
      setNewKey(key);
      setName("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create key");
    } finally {
      setCreating(false);
    }
  };

  const onRevoke = async (keyId: string) => {
    if (!window.confirm("Revoke this key? Anything using it will stop working immediately.")) return;
    try {
      await revokeApiKey(keyId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to revoke key");
    }
  };

  return (
    <div className="space-y-2">
      <p className="text-[13px] text-muted-foreground">
        Personal keys for connecting external agents (e.g. Hermes) to your Loma tasks over
        MCP. Each key acts as you and only sees your own board.
      </p>

      <Card>
        <CardContent className="space-y-2">
          <div className="text-[13px] font-semibold text-foreground">MCP endpoint</div>
          <div className="flex items-center gap-2">
            <code className="text-xs bg-muted rounded px-2 py-1.5 flex-1 overflow-x-auto whitespace-nowrap">
              {mcpUrl || "…"}
            </code>
            {mcpUrl && <CopyButton text={mcpUrl} />}
          </div>
          <p className="text-xs text-muted-foreground">
            Streamable HTTP transport. Authenticate with{" "}
            <code className="bg-muted rounded px-1">Authorization: Bearer &lt;your key&gt;</code>.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-3">
          <div className="text-[13px] font-semibold text-foreground">Create a key</div>
          <div className="flex items-center gap-2">
            <Input
              value={name}
              placeholder="Key name (e.g. Hermes)"
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !creating && onCreate()}
            />
            <Button size="sm" onClick={onCreate} disabled={creating} className="shrink-0">
              <RiAddLine size={16} />
              Create
            </Button>
          </div>
          {newKey && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 space-y-2">
              <div className="text-xs font-medium text-foreground">
                Copy your key now — it will not be shown again.
              </div>
              <div className="flex items-center gap-2">
                <code className="text-xs bg-muted rounded px-2 py-1.5 flex-1 overflow-x-auto whitespace-nowrap">
                  {newKey}
                </code>
                <CopyButton text={newKey} />
              </div>
            </div>
          )}
          {error && <div className="text-xs text-red-500">{error}</div>}
        </CardContent>
      </Card>

      <div className="space-y-2">
        {keys === null ? (
          <>
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </>
        ) : keys.length === 0 ? (
          <EmptyState
            icon={RiKey2Line}
            title="No API keys yet"
            description="Create a key above to connect an external agent."
          />
        ) : (
          keys.map((k) => (
            <Card key={k.key_id}>
              <CardContent className="flex items-center gap-3">
                <RiKey2Line size={16} className="text-muted-foreground shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-semibold text-foreground truncate">{k.name}</div>
                  <div className="text-xs text-muted-foreground">
                    <code>{k.key_prefix}…</code>
                    {" · created "}
                    {k.created_at ? <ClientTimestamp iso={k.created_at} variant="short" /> : "—"}
                    {" · last used "}
                    {k.last_used_at ? <ClientTimestamp iso={k.last_used_at} variant="short" /> : "never"}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 text-muted-foreground hover:text-red-500 shrink-0"
                  aria-label="Revoke key"
                  onClick={() => onRevoke(k.key_id)}
                >
                  <RiDeleteBinLine size={15} />
                </Button>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
