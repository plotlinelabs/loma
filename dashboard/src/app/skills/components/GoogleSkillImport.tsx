"use client";

import { useEffect, useState } from "react";
import { skillSourceRequest, type GoogleSkillPreview, type SkillDetailResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";

export default function GoogleSkillImport({ onImported }: { onImported: (slug: string) => void }) {
  const [enabled, setEnabled] = useState({ docs: false, sheets: false });
  const [headerRow, setHeaderRow] = useState(false);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const isSheet = /\/spreadsheets\/d\//.test(url);
  const provider = isSheet ? "google-sheets" : "google-docs";
  const [preview, setPreview] = useState<GoogleSkillPreview | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    for (const [key, provider] of [["docs", "google-docs"], ["sheets", "google-sheets"]] as const) {
      skillSourceRequest<{ enabled: boolean }>(`skill-sources/${provider}`).then(r => setEnabled(old => ({ ...old, [key]: r.enabled }))).catch(() => {});
    }
  }, []);

  async function loadPreview(tab_id?: string) {
    setBusy(true); setError(""); setPreview(null);
    try {
      const result = await skillSourceRequest<GoogleSkillPreview>(`skill-sources/${provider}/preview`, { url, tab_id, header_row: headerRow });
      setPreview(result); setName(result.title);
      setSlug(result.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function importSkill() {
    if (!preview?.hash) return;
    setBusy(true); setError("");
    try {
      const result = await skillSourceRequest<SkillDetailResponse>(`skill-sources/${provider}/import`, {
        header_row: headerRow,
        url, tab_id: preview.tab_id, name, slug, description, preview_hash: preview.hash,
        scope: shared ? "workspace" : "personal", confirm_workspace: shared,
      });
      setOpen(false); setPreview(null); onImported(result.slug || result.name);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!enabled.docs && !enabled.sheets) return null;
  return <>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}>Import from Google</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value); }}>
      <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>Instructions, shared with your team</DialogTitle>
          <DialogDescription>Link one Google Docs or Sheets tab. Regular skills stay unchanged. Importing will not edit your source.</DialogDescription></DialogHeader>
        <label className="text-sm space-y-2">Google Docs or Sheets URL<Input value={url} onChange={e => { setUrl(e.target.value); setPreview(null); }} placeholder="https://docs.google.com/document/d/.../edit" disabled={busy} /></label>
        {isSheet && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={headerRow} disabled={busy} onChange={e => { setHeaderRow(e.target.checked); setPreview(null); }} />Use the first nonempty row as column headers</label>}
        {isSheet && <p className="text-xs text-muted-foreground">Sheets sync is read-only in Loma. Edit cells in Google Sheets. Use a text-only tab; floating images and drawings cannot be detected or imported.</p>}
        <Button onClick={() => loadPreview()} disabled={busy || !url || (isSheet ? !enabled.sheets : !enabled.docs)}>{busy ? "Working..." : "Preview source"}</Button>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {preview && <div className="space-y-4">
          <p className="text-xs text-muted-foreground">Connected as {preview.connection_owner}</p>
          <label className="block text-sm">Source tab<select aria-label="Source tab" className="block w-full rounded-md border bg-background p-2 mt-1" value={preview.tab_id ?? ""} onChange={e => loadPreview(e.target.value)}>
            <option value="" disabled>Select a tab</option>{preview.tabs.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select></label>
          {!isSheet && !preview.can_edit && <p className="text-sm text-destructive">Your Google account needs edit access for two-way linking.</p>}
          {preview.content && <>
            {preview.disclosure && <p className="text-xs text-muted-foreground">{preview.disclosure}</p>}
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border bg-muted p-3 text-xs">{preview.content}</pre>
            <label className="block text-sm">Skill name<Input value={name} onChange={e => setName(e.target.value)} /></label>
            <label className="block text-sm">Stable slug<Input value={slug} onChange={e => setSlug(e.target.value)} /></label>
            <label className="block text-sm">When should Loma use this skill?<Input value={description} onChange={e => setDescription(e.target.value)} /></label>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} />I want to publish these instructions to the Loma workspace. Otherwise, keep them personal.</label>
            <p className="text-xs text-muted-foreground">{isSheet ? "Syncs about every five minutes. Hidden rows/columns, merged cells, formula errors and unsupported embedded content block sync. Supporting files stay in Loma." : "Syncs about every five minutes. Supports paragraphs, headings, simple lists, bold, italic and links. Unsupported content blocks sync. Supporting files stay in Loma."}</p>
            <Button onClick={importSkill} disabled={busy || !name || !slug || !description || (!isSheet && !preview.can_edit)}>Import and keep synced</Button>
          </>}
        </div>}
      </DialogContent>
    </Dialog>
  </>;
}
