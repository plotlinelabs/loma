"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/bounded-work-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";

export type Source = { source_id: string; owner: string; title: string; content: string; readers?: string[]; version: number };
const empty = { title: "", content: "", readers: "" };
export function KnowledgeLibrary({ open, onOpenChange, owner, onSaved }: { open: boolean; onOpenChange: (v: boolean) => void; owner: string; onSaved: () => Promise<void> }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Source | null>(null);
  const [draft, setDraft] = useState(empty);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const readOnly = !!selected && selected.owner !== owner;
  const reload = async () => setSources((await api<{sources: Source[]}>("knowledge")).sources);
  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoading(true); setError(""); setNotice(""); setSelected(null); setDraft(empty); setDeleting(false);
    api<{sources: Source[]}>("knowledge").then(data => { if (current) setSources(data.sources); }).catch(e => {if (current) setError(e.message);}).finally(() => {if (current) setLoading(false);});
    return () => {current = false;};
  }, [open]);
  const choose = (source: Source | null) => {setSelected(source); setDraft(source ? {...source, readers: (source.readers || []).join("\n")} : empty); setDeleting(false); setError(""); setNotice("");};
  const save = async (remove = false) => {
    if (busy || readOnly) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const payload = { ...draft, version: selected?.version, readers: draft.readers.split(/[\n,]/).map(v => v.trim()).filter(Boolean) };
      if (remove && selected) await api(`knowledge/${selected.source_id}`, {version: selected.version}, "DELETE");
      else await api(selected ? `knowledge/${selected.source_id}` : "knowledge", payload);
      choose(null); setNotice(remove ? "Playbook deleted. Future reads and dependent actions are blocked. Earlier results are not erased." : "Playbook saved. Only explicitly attached jobs can read it.");
      await reload(); await onSaved();
    } catch (e) {setError(e instanceof Error ? e.message : "Could not save. Your text has been kept.");}
    finally {setBusy(false);}
  };
  return <Dialog open={open} onOpenChange={v => {if (!busy) onOpenChange(v);}}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>Playbook library</DialogTitle><DialogDescription>Reusable text sources, separate from personal memory. Private by default. Share only with named active workspace accounts; this never shares your connected accounts.</DialogDescription></DialogHeader>
    {error && <div role="alert" className="space-y-2 break-words text-sm text-destructive"><p>{error}</p><Button variant="outline" disabled={busy} onClick={() => void reload().then(() => setError("List refreshed. Your draft is kept. Select a playbook to load its latest revision before saving.")).catch(e => setError(e.message))}>Refresh source list</Button></div>}{notice && <p role="status" className="text-sm">{notice}</p>}
    {loading ? <p role="status">Loading playbooks…</p> : <div className="space-y-2"><Button variant="outline" disabled={busy} onClick={() => choose(null)}>New playbook</Button>{sources.length === 0 && <p className="text-sm text-muted-foreground">No playbooks yet. Paste a process or attach a text file below.</p>}{sources.map(source => <button key={source.source_id} disabled={busy} onClick={() => choose(source)} className="block w-full rounded-lg border p-3 text-left"><span className="block break-words text-sm font-medium">{source.title}</span><span className="block break-all text-xs text-muted-foreground">{source.owner === owner ? (source.readers?.length ? `Shared with ${source.readers.length} accounts` : "Private to you") : `Read-only · ${source.owner}`} · Revision {source.version}</span></button>)}</div>}
    <form className="space-y-3" onSubmit={e => {e.preventDefault(); void save();}}>
      {readOnly && <p className="rounded-lg bg-muted p-3 text-sm">You can attach this playbook to your work. Only its author can edit, delete or change sharing.</p>}
      {!readOnly && <div><Label htmlFor="playbook-file">Attach text or Markdown (up to 6 KB)</Label><Input id="playbook-file" type="file" accept=".txt,.md" disabled={busy} onChange={async e => {const file = e.target.files?.[0]; if (!file) return; if (file.size > 6000 || !/\.(txt|md)$/i.test(file.name)) {setError("Choose a .txt or .md file up to 6 KB."); return;} try {const content = await file.text(); setDraft(d => ({...d, title: d.title || file.name, content}));} catch {setError("Could not read the file. Paste the text instead.");}}} /></div>}
      <div><Label htmlFor="playbook-title">Playbook title</Label><Input id="playbook-title" required maxLength={120} readOnly={readOnly} disabled={busy} value={draft.title} onChange={e => setDraft({...draft,title:e.target.value})} /></div>
      <div><Label htmlFor="playbook-content">Source text</Label><Textarea id="playbook-content" required maxLength={6000} rows={7} readOnly={readOnly} disabled={busy} value={draft.content} onChange={e => setDraft({...draft,content:e.target.value})} /></div>
      {!readOnly && <div><Label htmlFor="playbook-readers">Share with these accounts (optional)</Label><Textarea id="playbook-readers" rows={2} disabled={busy} value={draft.readers} onChange={e => setDraft({...draft,readers:e.target.value})} placeholder="Exact workspace emails, one per line. Empty means private." /><p className="mt-1 text-xs text-muted-foreground">Removing a reader stops future retrievals and dependent actions. It cannot erase content already used in earlier results.</p></div>}
      <div className="flex flex-wrap gap-2">{!readOnly && <Button disabled={busy || loading}>{busy ? "Saving…" : selected ? "Update playbook" : "Save playbook"}</Button>}<Button type="button" variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>Close</Button>{selected && !readOnly && <Button type="button" variant="ghost" disabled={busy} onClick={() => setDeleting(true)}>Delete playbook</Button>}</div>
    </form>
    {deleting && <section className="space-y-2 rounded-lg border border-destructive p-3"><p className="text-sm">Delete this source for everyone? Jobs that depend on it will stop at their next access check.</p><div className="flex gap-2"><Button variant="destructive" disabled={busy} onClick={() => void save(true)}>Confirm delete</Button><Button variant="outline" disabled={busy} onClick={() => setDeleting(false)}>Keep playbook</Button></div></section>}
  </DialogContent></Dialog>;
}
