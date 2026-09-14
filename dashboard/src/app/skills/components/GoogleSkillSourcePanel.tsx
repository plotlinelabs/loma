"use client";
import { useState } from "react";
import { skillSourceRequest, type GoogleSkillSource } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function GoogleSkillSourcePanel({ source, slug, onUpdated, disabled = false, statusUnknown = false }: { statusUnknown?: boolean; disabled?: boolean; source: GoogleSkillSource; slug: string; onUpdated: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<{ status: string; created_at: string }[] | null>(null);
  async function action(action: string) {
    if (action === "disconnect" && !window.confirm("Keep the last validated instructions as a regular skill? The Google Doc will not be deleted.")) return;
    setBusy(true); setError("");
    try { await skillSourceRequest(`skills/${encodeURIComponent(slug)}/source`, { action }); await onUpdated(); }
    catch (e) { setError((e as Error).message); await onUpdated().catch(() => {}); } finally { setBusy(false); }
  }
  return <section aria-label="Google Docs source" className="border-b px-5 py-3 space-y-2 text-xs">
    <div className="flex flex-wrap items-center gap-2"><span className="font-medium">Google Docs</span><span className="rounded bg-muted px-2 py-1">{statusUnknown ? "status unavailable" : source.status.replaceAll("_", " ")}{!source.auto_sync_enabled && " · auto-sync paused"}</span>
      <a className="text-primary hover:underline" href={`https://docs.google.com/document/d/${source.document_id}/edit?tab=${source.tab_id}`} target="_blank" rel="noreferrer">{source.title} / {source.tab_title} ↗</a></div>
    <p className="text-muted-foreground">Sync connection: {source.connection_owner}. Saves use your own Google account.</p>
    <p className="text-muted-foreground">Last checked: {source.last_checked ? new Date(source.last_checked).toLocaleString() : "Not yet"} · Last published: {source.last_published ? new Date(source.last_published).toLocaleString() : "Not yet"}</p>
    {(error || source.error) && <p role="alert" className="text-destructive">{error || source.error}</p>}
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" disabled={busy || disabled} onClick={() => action("sync")}>Sync now</Button>
      <Button variant="ghost" size="sm" disabled={busy || disabled} onClick={() => action(source.auto_sync_enabled ? "pause" : "resume")}>{source.auto_sync_enabled ? "Pause auto-sync" : "Resume auto-sync"}</Button>
      <Button variant="ghost" size="sm" disabled={busy || disabled} onClick={() => action("disconnect")}>Disconnect</Button>
      <Button variant="ghost" size="sm" onClick={async () => { try { const r = await skillSourceRequest<{ history: { status: string; created_at: string }[] }>(`skills/${encodeURIComponent(slug)}/source/history`); setHistory(history ? null : r.history); } catch (e) { setError((e as Error).message); } }}>Sync history</Button>
    </div>
    {history && <ul className="max-h-28 overflow-auto">{history.map((h, i) => <li key={i}>{new Date(h.created_at).toLocaleString()}: {h.status.replaceAll("_", " ")}</li>)}</ul>}
  </section>;
}
