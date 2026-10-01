"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Status = { configured?: boolean; connected?: boolean; scheduler_running?: boolean; error?: string };
export default function HumanTaskSetup() {
  const [status, setStatus] = useState<Status>({});
  const [busy, setBusy] = useState(false);
  async function check(method = "GET") {
    setBusy(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/gateway-setup`, { method, cache: "no-store" });
      setStatus(await res.json());
    } catch { setStatus({ error: "Unable to check setup. Please retry." }); }
    finally { setBusy(false); }
  }
  useEffect(() => { void check(); }, []);
  return <Card>
    <CardHeader><CardTitle>Human task approvals</CardTitle></CardHeader>
    <CardContent className="space-y-3 text-sm">
      <p>Connect dashboard decisions to the backend. The key is generated internally and stored in Loma&apos;s existing database. No secret copying or dashboard environment changes are needed.</p>
      <p role="status">{status.connected ? "Gateway connected." : status.configured ? "Gateway configured; connection not verified." : "Gateway not configured."} {status.connected && (status.scheduler_running ? "Agent continuation is running." : "Agent continuation is not running.")}</p>
      {status.error && <p role="alert" className="text-destructive">{status.error}</p>}
      {status.connected && !status.scheduler_running && <p>Set <code>LOMA_ENABLE_SCHEDULER</code> to <code>true</code> in Environment below, save, then click Restart Service. This enables all scheduled flows, not just human tasks. Refresh this check after restart.</p>}
      <div className="flex gap-2">
        <Button disabled={busy} onClick={() => void check("POST")}>{busy ? "Checking..." : status.configured ? "Reconnect gateway" : "Set up approvals"}</Button>
        <Button variant="outline" disabled={busy} onClick={() => void check()}>Refresh status</Button>
      </div>
    </CardContent>
  </Card>;
}
