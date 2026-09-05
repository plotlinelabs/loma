"use client";

import { useEffect, useState } from "react";
import { fetchGrainWebhook, rotateGrainWebhook, revokeGrainWebhook,
  type GrainWebhookStatus, type GrainWebhookCredential } from "@/lib/oauth-api";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";

export default function GrainWebhookPanel() {
  const [status, setStatus] = useState<GrainWebhookStatus | null>(null);
  const [credential, setCredential] = useState<GrainWebhookCredential | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<"rotate" | "revoke" | null>(null);
  useEffect(() => {
    let active = true;
    fetchGrainWebhook().then(value => { if (active) setStatus(value); })
      .catch(() => { if (active) setError("Could not load webhook settings. Reload to retry."); });
    return () => { active = false; };
  }, []);

  async function change(action: "rotate" | "revoke") {
    setBusy(true);
    setError("");
    setCredential(null);
    try {
      if (action === "rotate") {
        const issued = await rotateGrainWebhook();
        setStatus({ enabled: true, path: issued.path, expires_at: issued.expires_at });
        setCredential(issued);
      } else {
        await revokeGrainWebhook();
        setStatus(value => value ? { ...value, enabled: false, expires_at: null } : null);
      }
      setConfirm(null);
    } catch {
      // Never surface arbitrary response bodies beside one-time credentials.
      setError("Could not update the webhook. Reload its status before retrying.");
    } finally { setBusy(false); }
  }

  return <div className="mt-4 space-y-3 border-t pt-4">
    <div>
      <h3 className="text-xs font-semibold">Personal recording automation</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Deliver recording IDs through your automation sender. Transcripts remain private to you.
        This does not register a webhook automatically with Grain.
      </p>
    </div>
    <p className="text-xs text-muted-foreground" role="status">
      {!status ? "Loading settings..." : status.enabled
        ? `Enabled until ${new Date(status.expires_at!).toLocaleString()}` : "Not enabled"}
    </p>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex gap-2">
      <Button size="xs" variant="outline" disabled={busy || !status} onClick={() => setConfirm("rotate")}>
        {status?.enabled ? "Rotate webhook credential" : "Enable webhook"}
      </Button>
      {status?.enabled && <Button size="xs" variant="destructive" disabled={busy} onClick={() => setConfirm("revoke")}>Revoke</Button>}
    </div>
    <Dialog open={confirm !== null} onOpenChange={open => { if (!open && !busy) setConfirm(null); }}>
      <DialogContent><DialogHeader>
        <DialogTitle>{confirm === "revoke" ? "Revoke personal webhook?" : "Issue personal webhook credential?"}</DialogTitle>
        <DialogDescription>Existing credentials stop working immediately. Update your automation sender after rotation.</DialogDescription>
      </DialogHeader><DialogFooter>
        <Button variant="outline" disabled={busy} onClick={() => setConfirm(null)}>Cancel</Button>
        <Button disabled={busy} onClick={() => confirm && change(confirm)}>{busy ? "Saving..." : "Confirm"}</Button>
      </DialogFooter></DialogContent>
    </Dialog>
    <Dialog open={credential !== null} onOpenChange={open => { if (!open) setCredential(null); }}>
      <DialogContent><DialogHeader>
        <DialogTitle>Save your webhook credential</DialogTitle>
        <DialogDescription>Shown once. Store it securely in your sender, never in a URL or shared chat. Closing clears it from this page.</DialogDescription>
      </DialogHeader>
      {credential && <div className="space-y-3 text-xs">
        <p>POST to your externally reachable Loma backend origin with this path:</p>
        <code className="block break-all select-all">{credential.path}</code>
        <p>Authorization header:</p>
        <code className="block break-all select-all">{credential.authorization}</code>
        <p>JSON body:</p><code className="block select-all">{'{"recording_id":"YOUR_RECORDING_ID"}'}</code>
        <p>Expires {new Date(credential.expires_at).toLocaleString()}.</p>
      </div>}
      <DialogFooter><Button onClick={() => setCredential(null)}>I saved it securely</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}
