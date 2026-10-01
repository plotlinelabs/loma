"use client";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Settings = { bounded_work_enabled: boolean; ashby_allowed_users: string[]; overrides?: string[] };
type Status = { settings?: Settings; configured?: boolean; connected?: boolean; scheduler_running?: boolean; error?: string };
export default function HumanTaskSetup() {
  const [status, setStatus] = useState<Status>({});
  const [busy, setBusy] = useState(false);
  const [bounded, setBounded] = useState(false);
  const [users, setUsers] = useState("");
  const check = useCallback(async (method = "GET", settings?: Settings) => {
    setBusy(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/gateway-setup`, { method, cache: "no-store",
        ...(settings ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "save-settings",
          settings }) } : {}) });
      const data: Status = await res.json();
      setStatus(data);
      if (data.settings) { setBounded(data.settings.bounded_work_enabled); setUsers(data.settings.ashby_allowed_users.join("\n")); }
    } catch { setStatus({ error: "Unable to check setup. Please retry." }); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void check(); }, [check]);
  return <Card>
    <CardHeader><CardTitle>Human task approvals and agent settings</CardTitle></CardHeader>
    <CardContent className="space-y-3 text-sm">
      <p>Connect dashboard decisions to the backend. The key is generated internally and stored in Loma&apos;s existing database. No secret copying or dashboard environment changes are needed.</p>
      <p role="status">{status.connected ? "Gateway connected." : status.configured ? "Gateway configured; connection not verified." : "Gateway not configured."} {status.connected && (status.scheduler_running ? "Agent continuation is running." : "Agent continuation is not running.")}</p>
      {status.error && <p role="alert" className="text-destructive">{status.error}</p>}
      {status.connected && !status.scheduler_running && <p>The deployment’s existing scheduler off-switch is active, or the backend worker is unavailable. Decisions remain saved, but the agent will not resume until the deployment owner restores the worker. This setup never edits environment files.</p>}
      {status.settings && <fieldset className="space-y-3 border rounded-md p-3">
        <legend>Agent settings (admin only)</legend>
        <label className="flex items-center gap-2"><input type="checkbox" checked={bounded} onChange={e => setBounded(e.target.checked)} disabled={busy || status.settings.overrides?.includes("LOMA_BOUNDED_WORK_ENABLED")} /> Enable bounded agent work</label>
        <label className="block" htmlFor="ashby-users">Ashby allowed users (one email per line)</label>
        <textarea id="ashby-users" className="w-full border rounded-md p-2" rows={3} value={users} onChange={e => setUsers(e.target.value)} disabled={busy || status.settings.overrides?.includes("ASHBY_ALLOWED_USERS")} />
        <p>Only existing active accounts can be allowed. An empty list denies all Ashby access. Changes use the existing database, never environment files.</p>
        {!!status.settings.overrides?.length && <p>Deployment overrides take precedence: {status.settings.overrides.join(", ")}. These are not changed here.</p>}
        <Button disabled={busy} onClick={() => void check("POST", { bounded_work_enabled: bounded, ashby_allowed_users: users.split(/[\n,]/).map(v => v.trim()).filter(Boolean) })}>Save agent settings</Button>
      </fieldset>}
      <div className="flex gap-2">
        <Button disabled={busy} onClick={() => void check("POST")}>{busy ? "Checking..." : status.configured ? "Reconnect gateway" : "Set up approvals"}</Button>
        <Button variant="outline" disabled={busy} onClick={() => void check()}>Refresh status</Button>
      </div>
    </CardContent>
  </Card>;
}
