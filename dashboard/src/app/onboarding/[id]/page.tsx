"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { RiChat1Line, RiExternalLinkLine, RiLoader4Line } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import ClientTimestamp from "@/components/ClientTimestamp";
import {
  fetchOnboardingConfig,
  fetchOnboardingRecord,
  FLAG_COLORS,
  FLAG_LABELS,
  formatValue,
  updateOnboardingRecord,
} from "@/lib/onboarding-api";
import type {
  FieldValue,
  OnboardingConfig,
  OnboardingEvent,
  OnboardingField,
  OnboardingRecord,
} from "@/lib/onboarding-api";

const selectClass =
  "h-8 w-full rounded-md border border-input bg-transparent px-2 text-[13px] shadow-xs outline-none focus-visible:ring-1 focus-visible:ring-ring";

function FieldEditor({
  field,
  value,
  onChange,
  disabled,
}: {
  field: OnboardingField;
  value: FieldValue;
  onChange: (v: FieldValue) => void;
  disabled: boolean;
}) {
  if (field.type === "select") {
    return (
      <select
        className={selectClass}
        value={(value as string) ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">-</option>
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "multiselect") {
    const selected = new Set(Array.isArray(value) ? value : []);
    return (
      <div className="flex flex-wrap gap-1">
        {(field.options ?? []).map((o) => {
          const on = selected.has(o);
          return (
            <button
              key={o}
              type="button"
              disabled={disabled}
              onClick={() => {
                const next = new Set(selected);
                if (on) next.delete(o);
                else next.add(o);
                onChange(next.size ? [...next] : null);
              }}
              className={cn(
                "rounded border px-1.5 py-0.5 text-[11px] transition-colors",
                on ? "border-foreground/30 bg-foreground/10 text-foreground" : "border-border text-muted-foreground",
                !disabled && "hover:border-foreground/40",
              )}
            >
              {o}
            </button>
          );
        })}
      </div>
    );
  }
  if (field.type === "longtext") {
    return (
      <Textarea
        rows={4}
        className="text-[13px]"
        value={(value as string) ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <Input
      className="h-8 text-[13px]"
      type={field.type === "date" ? "date" : field.type === "number" ? "number" : "text"}
      value={value === null || value === undefined ? "" : String(value)}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

const SECTIONS: { title: string; keys: string[] }[] = [
  { title: "Status", keys: ["owner", "health", "blocker_owner", "next_step", "next_step_due", "target_go_live"] },
  { title: "Timeline", keys: ["contract_start", "kickoff_date", "dev_provisioned", "first_live", "pilot_end", "client_blocked_days", "pilot_outcome"] },
  { title: "Scope", keys: ["engagement_type", "platforms", "modules_in_scope", "modules_pending", "go_live_checklist"] },
  { title: "Commercial & links", keys: ["arr", "billing_status", "loss_reason", "org_id", "product_ids", "hubspot_url"] },
];

function sameValue(a: FieldValue, b: FieldValue) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export default function OnboardingRecordPage() {
  const { id } = useParams<{ id: string }>();
  const [config, setConfig] = useState<OnboardingConfig | null>(null);
  const [record, setRecord] = useState<OnboardingRecord | null>(null);
  const [events, setEvents] = useState<OnboardingEvent[]>([]);
  const [siblings, setSiblings] = useState<{ record_id: string; name: string; stage: string }[]>([]);
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [c, data] = await Promise.all([fetchOnboardingConfig(), fetchOnboardingRecord(id)]);
    setConfig(c);
    setRecord(data.record);
    setEvents(data.events);
    setSiblings(data.siblings);
    setDraft({});
    setNote("");
  }

  useEffect(() => {
    load().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const fieldMap = useMemo(() => Object.fromEntries((config?.fields ?? []).map((f) => [f.key, f])), [config]);
  const stageLabel = useMemo(
    () => Object.fromEntries((config?.stages ?? []).map((s) => [s.key, s.label])),
    [config],
  );
  const labelFor = (key: string) => (key === "stage" ? "Stage" : key === "account" ? "Account" : key === "name" ? "Name" : fieldMap[key]?.label ?? key);

  if (error && !record) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (!record || !config) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6 space-y-3">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const canEdit = config.can_edit;
  const current = (key: string): FieldValue =>
    key in draft ? draft[key] : key === "stage" || key === "account" || key === "name" ? (record as unknown as Record<string, FieldValue>)[key] : record.fields[key];
  const dirty = Object.keys(draft).filter((k) =>
    !sameValue(draft[k], k === "stage" || k === "account" || k === "name" ? (record as unknown as Record<string, FieldValue>)[k] : record.fields[k]),
  );
  const set = (key: string, v: FieldValue) => setDraft((d) => ({ ...d, [key]: v }));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const changes = Object.fromEntries(dirty.map((k) => [k, draft[k]]));
      await updateOnboardingRecord(id, changes, note.trim() || undefined);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const d = record.derived;
  const hubspot = formatValue(record.fields.hubspot_url);
  const askPrompt =
    `Look at the Onboarding record "${record.name}" (record id ${record.record_id}, account ${record.account}). ` +
    `Use tools/onboarding.py get to read it, check the CRM, product data, billing and call recordings as relevant, ` +
    `and tell me the current status, blockers and the next step.`;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-8">
      <Breadcrumb className="mb-3">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link href="/onboarding">Onboarding</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{record.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold truncate">{record.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
            <span>{record.account}</span>
            <Badge variant="outline" className="rounded font-normal">
              {stageLabel[record.stage] ?? record.stage}
            </Badge>
            {d.flags.map((f) => (
              <Badge key={f} variant="outline" className={cn("rounded text-[11px]", FLAG_COLORS[f])}>
                {FLAG_LABELS[f] ?? f}
              </Badge>
            ))}
            {hubspot && (
              <a href={hubspot} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline">
                HubSpot <RiExternalLinkLine size={12} />
              </a>
            )}
          </div>
        </div>
        <Button asChild size="sm" variant="outline">
          <Link href={`/chat?prompt=${encodeURIComponent(askPrompt)}`}>
            <RiChat1Line size={14} /> Ask Loma
          </Link>
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 mb-5">
        {[
          ["Days to live (net)", d.days_to_live_net],
          ["Days to live (gross)", d.days_to_live_gross],
          ["Pilot days left", d.pilot_days_left],
          ["Client-blocked days", record.fields.client_blocked_days ?? 0],
        ].map(([label, value]) => (
          <Card key={label as string} className="px-4 py-3 gap-0">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="text-xl font-semibold tabular-nums">{value === null || value === undefined ? "-" : String(value)}</div>
          </Card>
        ))}
      </div>

      {siblings.length > 0 && (
        <div className="mb-4 text-[13px]">
          <span className="text-muted-foreground">Other apps for {record.account}: </span>
          {siblings.map((s, i) => (
            <span key={s.record_id}>
              {i > 0 && ", "}
              <Link href={`/onboarding/${s.record_id}`} className="underline">
                {s.name}
              </Link>
              <span className="text-muted-foreground"> ({stageLabel[s.stage] ?? s.stage})</span>
            </span>
          ))}
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card className="p-4 gap-3">
            <div className="grid gap-3 md:grid-cols-3">
              <label className="space-y-1">
                <span className="text-xs text-muted-foreground">Stage</span>
                <select
                  className={selectClass}
                  value={current("stage") as string}
                  disabled={!canEdit}
                  onChange={(e) => set("stage", e.target.value)}
                >
                  {config.stages.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-xs text-muted-foreground">App name</span>
                <Input className="h-8 text-[13px]" value={(current("name") as string) ?? ""} disabled={!canEdit} onChange={(e) => set("name", e.target.value)} />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-muted-foreground">Account (groups apps)</span>
                <Input className="h-8 text-[13px]" value={(current("account") as string) ?? ""} disabled={!canEdit} onChange={(e) => set("account", e.target.value)} />
              </label>
            </div>
          </Card>

          {SECTIONS.map((section) => (
            <Card key={section.title} className="p-4 gap-3">
              <h2 className="text-[13px] font-semibold">{section.title}</h2>
              <div className="grid gap-3 md:grid-cols-2">
                {section.keys
                  .filter((k) => fieldMap[k])
                  .map((k) => {
                    const field = fieldMap[k];
                    const meta = record.meta[k];
                    const wide = field.type === "multiselect" || field.type === "longtext";
                    return (
                      <div key={k} className={cn("space-y-1", wide && "md:col-span-2")}>
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-xs text-muted-foreground">{field.label}</span>
                          {meta && (
                            <span className="text-[10px] text-muted-foreground/70" title={`${meta.by} · ${meta.at}`}>
                              {meta.source === "human" ? meta.by.split("@")[0] : `auto · ${meta.source}`}
                            </span>
                          )}
                        </div>
                        <FieldEditor field={field} value={current(k)} onChange={(v) => set(k, v)} disabled={!canEdit} />
                      </div>
                    );
                  })}
              </div>
            </Card>
          ))}

          {fieldMap.notes && (
            <Card className="p-4 gap-2">
              <h2 className="text-[13px] font-semibold">Notes</h2>
              <FieldEditor field={fieldMap.notes} value={current("notes")} onChange={(v) => set("notes", v)} disabled={!canEdit} />
            </Card>
          )}

          {canEdit && (
            <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-background/95 py-3">
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Optional note for the activity log"
                className="h-8 flex-1 min-w-48 text-[13px]"
              />
              <Button size="sm" variant="ghost" disabled={!dirty.length || saving} onClick={() => setDraft({})}>
                Discard
              </Button>
              <Button size="sm" disabled={(!dirty.length && !note.trim()) || saving} onClick={save}>
                {saving && <RiLoader4Line size={14} className="animate-spin" />}
                Save{dirty.length ? ` ${dirty.length} change${dirty.length > 1 ? "s" : ""}` : ""}
              </Button>
            </div>
          )}
        </div>

        <Card className="p-4 gap-3 self-start">
          <h2 className="text-[13px] font-semibold">Activity</h2>
          {events.length === 0 && <p className="text-[13px] text-muted-foreground">No activity yet.</p>}
          <ol className="space-y-3">
            {events.map((e) => (
              <li key={e.event_id} className="text-[12px] border-l border-border pl-3">
                <div className="text-muted-foreground">
                  <ClientTimestamp iso={e.at} variant="short" /> · {e.source === "human" ? e.actor.split("@")[0] : `auto · ${e.source}`}
                </div>
                {e.note && <div className="mt-0.5">{e.note}</div>}
                {e.changes.map((c) => (
                  <div key={c.field} className="mt-0.5">
                    <span className="font-medium">{labelFor(c.field)}</span>:{" "}
                    <span className="text-muted-foreground line-through">
                      {c.field === "stage" ? stageLabel[c.old as string] ?? formatValue(c.old) : formatValue(c.old)}
                    </span>{" "}
                    → {c.field === "stage" ? stageLabel[c.new as string] ?? formatValue(c.new) : formatValue(c.new)}
                  </div>
                ))}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
