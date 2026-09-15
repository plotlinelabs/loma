"use client";

import { useEffect, useState } from "react";
import { skillSourceRequest, type GoogleSkillPreview, type SkillDetailResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";

export default function GoogleSkillImport({ onImported }: { onImported: (slug: string) => void }) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<GoogleSkillPreview | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { skillSourceRequest<{ enabled: boolean }>("skill-sources/google-docs").then(r => setEnabled(r.enabled)).catch(() => {}); }, []);

  async function loadPreview(tab_id?: string) {
    setBusy(true); setError(""); setPreview(null);
    try {
      const result = await skillSourceRequest<GoogleSkillPreview>("skill-sources/google-docs/preview", { url, tab_id });
      setPreview(result); setName(result.title);
      setSlug(result.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function importSkill() {
    if (!preview?.hash) return;
    setBusy(true); setError("");
    try {
      const result = await skillSourceRequest<SkillDetailResponse>("skill-sources/google-docs/import", {
        url, tab_id: preview.tab_id, name, slug, description, preview_hash: preview.hash,
        scope: shared ? "workspace" : "personal", confirm_workspace: shared,
      });
      setOpen(false); setPreview(null); onImported(result.slug || result.name);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!enabled) return null;
  return <>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}>Import from Google Docs</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value); }}>
      <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>Instructions, shared with your team</DialogTitle>
          <DialogDescription>Link one Google Doc tab. Regular Loma skills stay unchanged. Importing will not edit your document.</DialogDescription></DialogHeader>
        <label className="text-sm space-y-2">Google Docs URL<Input value={url} onChange={e => { setUrl(e.target.value); setPreview(null); }} placeholder="https://docs.google.com/document/d/.../edit" disabled={busy} /></label>
        <Button onClick={() => loadPreview()} disabled={busy || !url}>{busy ? "Working..." : "Preview document"}</Button>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {preview && <div className="space-y-4">
          <p className="text-xs text-muted-foreground">Connected as {preview.connection_owner}</p>
          <label className="block text-sm">Document tab<select aria-label="Document tab" className="block w-full rounded-md border bg-background p-2 mt-1" value={preview.tab_id || ""} onChange={e => loadPreview(e.target.value)}>
            <option value="" disabled>Select a tab</option>{preview.tabs.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select></label>
          {!preview.can_edit && <p className="text-sm text-destructive">Your Google account needs edit access for two-way linking.</p>}
          {preview.content && <>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border bg-muted p-3 text-xs">{preview.content}</pre>
            <label className="block text-sm">Skill name<Input value={name} onChange={e => setName(e.target.value)} /></label>
            <label className="block text-sm">Stable slug<Input value={slug} onChange={e => setSlug(e.target.value)} /></label>
            <label className="block text-sm">When should Loma use this skill?<Input value={description} onChange={e => setDescription(e.target.value)} /></label>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} />I want to publish these instructions to the Loma workspace. Otherwise, keep them personal.</label>
            <p className="text-xs text-muted-foreground">Syncs every five minutes. Supports paragraphs, headings, simple lists, bold, italic and links. Unsupported content blocks sync. Supporting files stay in Loma.</p>
            <Button onClick={importSkill} disabled={busy || !name || !slug || !description || !preview.can_edit}>Import and keep synced</Button>
          </>}
        </div>}
      </DialogContent>
    </Dialog>
  </>;
}
