"use client";

import { useState } from "react";
import { RiAddLine, RiDeleteBinLine, RiEditLine, RiListCheck3 } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { usePromptTemplates } from "@/hooks/usePromptTemplates";
import {
  createPromptTemplate,
  deletePromptTemplate,
  updatePromptTemplate,
  type PromptTemplate,
  type PromptTemplateInput,
} from "@/lib/api";

const EMPTY: PromptTemplateInput = { name: "", description: "", input_hint: "", body: "", plan_mode: false };

/** User menu > Templates: the built-in slash commands, plus the user's own. */
export function PromptTemplatesDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { templates, reload } = usePromptTemplates();
  // null = list view; otherwise the form, editing `editingName` (or creating).
  const [draft, setDraft] = useState<PromptTemplateInput | null>(null);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const startEdit = (template?: PromptTemplate) => {
    setError("");
    setEditingName(template?.name ?? null);
    setDraft(template
      ? { name: template.name, description: template.description, input_hint: template.input_hint, body: template.body, plan_mode: template.plan_mode }
      : EMPTY);
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      if (editingName) {
        const { name: _name, ...rest } = draft;
        void _name;
        await updatePromptTemplate(editingName, rest);
      } else {
        await createPromptTemplate(draft);
      }
      await reload();
      setDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the template");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (name: string) => {
    setError("");
    try {
      await deletePromptTemplate(name);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't delete the template");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { setDraft(null); setError(""); onOpenChange(next); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogTitle>Templates</DialogTitle>
        <DialogDescription>Type / in any chat to use one. Yours are private to you.</DialogDescription>

        {draft ? (
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-[13px]">
              Command
              <div className="flex items-center gap-1">
                <span className="font-mono text-muted-foreground">/</span>
                <Input
                  value={draft.name}
                  disabled={!!editingName}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })}
                  placeholder="standup"
                  maxLength={32}
                  className="font-mono"
                />
              </div>
            </label>
            <label className="flex flex-col gap-1 text-[13px]">
              Description
              <Input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Write my standup update" maxLength={120} />
            </label>
            <label className="flex flex-col gap-1 text-[13px]">
              What it expects (optional)
              <Input value={draft.input_hint} onChange={(e) => setDraft({ ...draft, input_hint: e.target.value })} placeholder="<date or PR links>" maxLength={60} className="font-mono" />
            </label>
            <label className="flex flex-col gap-1 text-[13px]">
              Instructions
              <Textarea
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                rows={6}
                placeholder="Write my standup update from {input}: yesterday, today, blockers."
                className="text-[13px]"
              />
              <span className="text-xs text-muted-foreground">{"{input}"} is replaced by what you type after the command.</span>
            </label>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="flex items-center gap-1.5"><RiListCheck3 size={14} />Plan first (turns on plan mode)</span>
              <Switch checked={draft.plan_mode} onCheckedChange={(on) => setDraft({ ...draft, plan_mode: on })} />
            </label>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>Cancel</Button>
              <Button size="sm" disabled={saving || !draft.name || !draft.body.trim()} onClick={save}>
                {editingName ? "Save" : "Add template"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            <div className="max-h-[50dvh] overflow-y-auto">
              {templates.map((t) => (
                <div key={t.name} className="group/template flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted/60">
                  <span className="font-mono text-[13px]">/{t.name}</span>
                  {t.input_hint && <span className="font-mono text-[11px] text-muted-foreground">{t.input_hint}</span>}
                  <span className="ml-auto truncate pl-2 text-xs text-muted-foreground">{t.description}</span>
                  {t.builtin ? (
                    <span className="shrink-0 text-[10px] text-muted-foreground">built-in</span>
                  ) : (
                    <span className="flex shrink-0 items-center opacity-0 transition-opacity group-hover/template:opacity-100 group-focus-within/template:opacity-100 pointer-coarse:opacity-100">
                      <Button variant="ghost" size="icon-sm" aria-label={`Edit /${t.name}`} onClick={() => startEdit(t)}>
                        <RiEditLine size={14} />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label={`Delete /${t.name}`} onClick={() => remove(t.name)} className="hover:text-destructive">
                        <RiDeleteBinLine size={14} />
                      </Button>
                    </span>
                  )}
                </div>
              ))}
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <Button variant="outline" size="sm" className="mt-2 self-start" onClick={() => startEdit()}>
              <RiAddLine size={14} />New template
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
