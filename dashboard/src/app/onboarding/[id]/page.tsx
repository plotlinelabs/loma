"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  RiChat1Line,
  RiCheckLine,
  RiExternalLinkLine,
  RiLightbulbLine,
  RiLoader4Line,
} from "@remixicon/react";
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
  FIELD_SOURCES,
  FLAG_COLORS,
  FLAG_LABELS,
  formatValue,
  milestoneIndex,
  MODULE_LAYERS,
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

const SOURCE_LABEL = Object.fromEntries(
  FIELD_SOURCES.map((s) => [s.key, s.label]),
);
const LAYER_KEYS = new Set(MODULE_LAYERS.map((l) => l.key));

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
                on
                  ? "border-foreground/30 bg-foreground/10 text-foreground"
                  : "border-border text-muted-foreground",
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
        rows={3}
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
      type={
        field.type === "date"
          ? "date"
          : field.type === "number"
            ? "number"
            : "text"
      }
      value={value === null || value === undefined ? "" : String(value)}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function sameValue(a: FieldValue, b: FieldValue) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function ModuleMatrix({
  config,
  value,
  onToggle,
  disabled,
  gaps,
}: {
  config: OnboardingConfig;
  value: (layer: string) => string[];
  onToggle: (layer: string, module: string) => void;
  disabled: boolean;
  gaps: OnboardingRecord["derived"]["module_gaps"];
}) {
  const layers = MODULE_LAYERS.filter((l) =>
    config.fields.some((f) => f.key === l.key),
  );
  const paidSet = new Set(value("modules_paid"));
  const statusFor = (m: string) => {
    if (gaps.paid_not_integrated.includes(m))
      return { text: "Paid, not integrated", cls: "text-orange-700" };
    if (gaps.enabled_not_paid.includes(m))
      return { text: "Enabled, not paid", cls: "text-violet-700" };
    if (gaps.integrated_not_used.includes(m))
      return { text: "Integrated, not used", cls: "text-amber-700" };
    if (paidSet.size && !paidSet.has(m)) return { text: "Upsell", cls: "text-muted-foreground" };
    return null;
  };
  return (
    <Card className="p-4 gap-3" data-testid="module-matrix">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[13px] font-semibold">Modules</h2>
        <span className="text-[11px] text-muted-foreground">
          Paid is ticked by a person at kickoff. The other columns are filled
          from product data
        </span>
      </div>
      {!paidSet.size && (
        <div className="rounded bg-amber-50 px-2 py-1 text-[12px] text-amber-800">
          Paid modules not ticked yet, so paid vs integrated gaps can&apos;t be
          checked.
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 pr-2 text-left font-normal">Module</th>
              {layers.map((l) => (
                <th
                  key={l.key}
                  className="w-[78px] py-1 text-center font-normal"
                  title={l.hint}
                >
                  {l.label}
                </th>
              ))}
              <th className="py-1 pl-2 text-left font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {config.modules.map((m) => {
              const st = statusFor(m.label);
              return (
                <tr key={m.key} className="border-t border-border/60">
                  <td className="py-1 pr-2">{m.label}</td>
                  {layers.map((l) => {
                    const on = value(l.key).includes(m.label);
                    return (
                      <td key={l.key} className="py-1 text-center">
                        <button
                          type="button"
                          disabled={disabled}
                          aria-label={`${m.label} ${l.label}`}
                          aria-pressed={on}
                          onClick={() => onToggle(l.key, m.label)}
                          className={cn(
                            "inline-flex h-5 w-5 items-center justify-center rounded border transition-colors",
                            on
                              ? l.key === "modules_paid"
                                ? "border-foreground bg-foreground text-background"
                                : "border-emerald-600 bg-emerald-600 text-white"
                              : "border-border",
                            !disabled && "hover:border-foreground/50",
                          )}
                        >
                          {on && <RiCheckLine size={12} />}
                        </button>
                      </td>
                    );
                  })}
                  <td className={cn("py-1 pl-2 whitespace-nowrap", st?.cls)}>
                    {st?.text ?? ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Journey({
  record,
  config,
}: {
  record: OnboardingRecord;
  config: OnboardingConfig;
}) {
  const f = record.fields;
  const d = record.derived;
  const pos = config.stages.findIndex((s) => s.key === record.stage);
  const reached = (milestone: string) => {
    const i = milestoneIndex(config.stages, milestone);
    return i >= 0 && pos >= i;
  };
  const handed = config.stages.find((s) => s.terminal);
  const steps: { label: string; date?: string; done: boolean; sub?: string }[] = [
    { label: "Kickoff", date: formatValue(f.kickoff_date), done: !!f.kickoff_date },
    {
      label: "Dev provisioned",
      date: formatValue(f.dev_provisioned),
      done: !!f.dev_provisioned,
    },
    {
      label: "SDK live",
      date: formatValue(f.first_live),
      done: !!f.first_live || reached("sdk_live"),
      sub:
        d.days_to_live_net !== null ? `${d.days_to_live_net}d net` : undefined,
    },
    {
      label: `First campaign (${config.rules.first_campaign_min_users}+ users)`,
      date: d.first_campaign_qualified ? formatValue(f.first_campaign_live) : "",
      done: d.first_campaign_qualified,
      sub:
        d.days_live_to_first_campaign !== null
          ? `${d.days_live_to_first_campaign}d after live`
          : d.days_live_without_campaign !== null
            ? `none yet · ${d.days_live_without_campaign}d`
            : undefined,
    },
    {
      label: "Adopting",
      done: reached("adopting"),
      sub: f.live_campaigns ? `${formatValue(f.live_campaigns)} campaigns (30d)` : undefined,
    },
    {
      label: "Handed over",
      done: !!handed && record.stage === handed.key,
    },
  ];
  return (
    <Card className="p-4 gap-2" data-testid="journey">
      <h2 className="text-[13px] font-semibold">Journey</h2>
      <ol className="grid grid-cols-3 gap-y-3 md:grid-cols-6">
        {steps.map((s, i) => (
          <li key={s.label} className="relative pr-2">
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                  s.done
                    ? "border-emerald-600 bg-emerald-600 text-white"
                    : "border-border bg-background",
                )}
              >
                {s.done && <RiCheckLine size={10} />}
              </span>
              {i < steps.length - 1 && (
                <span
                  className={cn(
                    "h-px flex-1",
                    s.done ? "bg-emerald-600/50" : "bg-border",
                  )}
                />
              )}
            </div>
            <div className="mt-1 text-[12px] font-medium leading-tight">
              {s.label}
            </div>
            {s.date && (
              <div className="text-[11px] text-muted-foreground">{s.date}</div>
            )}
            {s.sub && (
              <div className="text-[11px] text-muted-foreground">{s.sub}</div>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

export default function OnboardingRecordPage() {
  const { id } = useParams<{ id: string }>();
  const [config, setConfig] = useState<OnboardingConfig | null>(null);
  const [record, setRecord] = useState<OnboardingRecord | null>(null);
  const [events, setEvents] = useState<OnboardingEvent[]>([]);
  const [siblings, setSiblings] = useState<
    { record_id: string; name: string; stage: string }[]
  >([]);
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [c, data] = await Promise.all([
      fetchOnboardingConfig(),
      fetchOnboardingRecord(id),
    ]);
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

  const fieldMap = useMemo(
    () => Object.fromEntries((config?.fields ?? []).map((f) => [f.key, f])),
    [config],
  );
  const stageLabel = useMemo(
    () =>
      Object.fromEntries((config?.stages ?? []).map((s) => [s.key, s.label])),
    [config],
  );
  // Sections in template order; module layers and notes have their own cards.
  const sections = useMemo(() => {
    const out: { title: string; fields: OnboardingField[] }[] = [];
    for (const f of config?.fields ?? []) {
      if (LAYER_KEYS.has(f.key) || f.key === "notes") continue;
      const title = f.section || "Other";
      let s = out.find((x) => x.title === title);
      if (!s) {
        s = { title, fields: [] };
        out.push(s);
      }
      s.fields.push(f);
    }
    return out;
  }, [config]);

  const labelFor = (key: string) =>
    key === "stage"
      ? "Stage"
      : key === "account"
        ? "Account"
        : key === "name"
          ? "Name"
          : (fieldMap[key]?.label ?? key);

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
  const isCore = (k: string) => k === "stage" || k === "account" || k === "name";
  const saved = (k: string): FieldValue =>
    isCore(k)
      ? (record as unknown as Record<string, FieldValue>)[k]
      : record.fields[k];
  const current = (key: string): FieldValue =>
    key in draft ? draft[key] : saved(key);
  const dirty = Object.keys(draft).filter((k) => !sameValue(draft[k], saved(k)));
  const set = (key: string, v: FieldValue) =>
    setDraft((d) => ({ ...d, [key]: v }));
  const layerValue = (k: string) =>
    Array.isArray(current(k)) ? (current(k) as string[]) : [];
  const toggleModule = (layer: string, module: string) => {
    const list = layerValue(layer);
    const next = list.includes(module)
      ? list.filter((m) => m !== module)
      : [...list, module];
    set(layer, next.length ? next : null);
  };
  const missing = new Set(record.derived.missing_required);

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
    `and tell me the current status, blockers, module gaps and the next step.`;

  const statCards: [string, string | number | null | undefined][] = [
    ["Days to live (net)", d.days_to_live_net],
    ["Days to live (gross)", d.days_to_live_gross],
    [
      "Live to 1st campaign",
      d.days_live_to_first_campaign !== null
        ? `${d.days_live_to_first_campaign}d`
        : d.days_live_without_campaign !== null
          ? `none · ${d.days_live_without_campaign}d`
          : null,
    ],
    ["Pilot days left", d.pilot_days_left],
    ["MTU vs contract", d.mtu_usage_pct !== null ? `${d.mtu_usage_pct}%` : null],
  ];

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
              <Badge
                key={f}
                variant="outline"
                className={cn("rounded text-[11px]", FLAG_COLORS[f])}
              >
                {FLAG_LABELS[f] ?? f}
              </Badge>
            ))}
            {hubspot && (
              <a
                href={hubspot}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
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

      {d.suggested_stage && canEdit && current("stage") === record.stage && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-[13px]">
          <RiLightbulbLine size={15} className="text-primary" />
          <span>
            Product data shows this app has reached{" "}
            <b>{stageLabel[d.suggested_stage] ?? d.suggested_stage}</b>.
          </span>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-7"
            onClick={() => set("stage", d.suggested_stage)}
          >
            Move stage
          </Button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5 mb-4">
        {statCards.map(([label, value]) => (
          <Card key={label} className="px-4 py-3 gap-0">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="text-xl font-semibold tabular-nums">
              {value === null || value === undefined ? "-" : String(value)}
            </div>
          </Card>
        ))}
      </div>

      {siblings.length > 0 && (
        <div className="mb-4 text-[13px]">
          <span className="text-muted-foreground">
            Other apps for {record.account}:{" "}
          </span>
          {siblings.map((s, i) => (
            <span key={s.record_id}>
              {i > 0 && ", "}
              <Link href={`/onboarding/${s.record_id}`} className="underline">
                {s.name}
              </Link>
              <span className="text-muted-foreground">
                {" "}
                ({stageLabel[s.stage] ?? s.stage})
              </span>
            </span>
          ))}
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="space-y-4 min-w-0">
          <Journey record={record} config={config} />

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
                <Input
                  className="h-8 text-[13px]"
                  value={(current("name") as string) ?? ""}
                  disabled={!canEdit}
                  onChange={(e) => set("name", e.target.value)}
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-muted-foreground">
                  Account (groups apps)
                </span>
                <Input
                  className="h-8 text-[13px]"
                  value={(current("account") as string) ?? ""}
                  disabled={!canEdit}
                  onChange={(e) => set("account", e.target.value)}
                />
              </label>
            </div>
            {config.stages.find((s) => s.key === current("stage"))
              ?.description && (
              <p className="text-[11px] text-muted-foreground">
                Stage rule:{" "}
                {
                  config.stages.find((s) => s.key === current("stage"))
                    ?.description
                }
              </p>
            )}
          </Card>

          <ModuleMatrix
            config={config}
            value={layerValue}
            onToggle={toggleModule}
            disabled={!canEdit}
            gaps={d.module_gaps}
          />

          {sections.map((section) => (
            <Card key={section.title} className="p-4 gap-3">
              <h2 className="text-[13px] font-semibold">{section.title}</h2>
              <div className="grid gap-3 md:grid-cols-2">
                {section.fields.map((field) => {
                  const k = field.key;
                  const meta = record.meta[k];
                  const wide =
                    field.type === "multiselect" || field.type === "longtext";
                  const need = missing.has(k);
                  return (
                    <div
                      key={k}
                      className={cn(
                        "space-y-1",
                        wide && "md:col-span-2",
                        need && "rounded-md bg-amber-50/70 -m-1 p-1",
                      )}
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-xs text-muted-foreground">
                          {field.label}
                          {need && (
                            <span className="ml-1 text-amber-700">
                              · needed at this stage
                            </span>
                          )}
                        </span>
                        {meta ? (
                          <span
                            className="text-[10px] text-muted-foreground/70"
                            title={`${meta.by} · ${meta.at}`}
                          >
                            {meta.source === "human"
                              ? meta.by.split("@")[0]
                              : `auto · ${meta.source}`}
                          </span>
                        ) : field.source && field.source !== "human" ? (
                          <span className="text-[10px] text-muted-foreground/60">
                            from {SOURCE_LABEL[field.source] ?? field.source}
                          </span>
                        ) : null}
                      </div>
                      <FieldEditor
                        field={field}
                        value={current(k)}
                        onChange={(v) => set(k, v)}
                        disabled={!canEdit}
                      />
                    </div>
                  );
                })}
              </div>
            </Card>
          ))}

          {fieldMap.notes && (
            <Card className="p-4 gap-2">
              <h2 className="text-[13px] font-semibold">Notes</h2>
              <FieldEditor
                field={fieldMap.notes}
                value={current("notes")}
                onChange={(v) => set("notes", v)}
                disabled={!canEdit}
              />
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
              <Button
                size="sm"
                variant="ghost"
                disabled={!dirty.length || saving}
                onClick={() => setDraft({})}
              >
                Discard
              </Button>
              <Button
                size="sm"
                disabled={(!dirty.length && !note.trim()) || saving}
                onClick={save}
              >
                {saving && <RiLoader4Line size={14} className="animate-spin" />}
                Save
                {dirty.length
                  ? ` ${dirty.length} change${dirty.length > 1 ? "s" : ""}`
                  : ""}
              </Button>
            </div>
          )}
        </div>

        <Card className="p-4 gap-3 self-start">
          <h2 className="text-[13px] font-semibold">Activity</h2>
          {events.length === 0 && (
            <p className="text-[13px] text-muted-foreground">No activity yet.</p>
          )}
          <ol className="space-y-3">
            {events.map((e) => (
              <li
                key={e.event_id}
                className="text-[12px] border-l border-border pl-3"
              >
                <div className="text-muted-foreground">
                  <ClientTimestamp iso={e.at} variant="short" /> ·{" "}
                  {e.source === "human"
                    ? e.actor.split("@")[0]
                    : `auto · ${e.source}`}
                </div>
                {e.note && <div className="mt-0.5">{e.note}</div>}
                {e.changes.map((c) => (
                  <div key={c.field} className="mt-0.5">
                    <span className="font-medium">{labelFor(c.field)}</span>:{" "}
                    {formatValue(c.old) !== "" && (
                      <>
                        <span className="text-muted-foreground line-through">
                          {c.field === "stage"
                            ? (stageLabel[c.old as string] ?? formatValue(c.old))
                            : formatValue(c.old)}
                        </span>{" "}
                        →{" "}
                      </>
                    )}
                    {formatValue(c.new) === "" ? (
                      <span className="text-muted-foreground">cleared</span>
                    ) : c.field === "stage" ? (
                      (stageLabel[c.new as string] ?? formatValue(c.new))
                    ) : (
                      formatValue(c.new)
                    )}
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
