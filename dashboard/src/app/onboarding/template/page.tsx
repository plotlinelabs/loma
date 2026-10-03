"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  RiAddLine,
  RiArrowDownLine,
  RiArrowUpLine,
  RiCloseLine,
  RiLoader4Line,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  FIELD_SOURCES,
  FIELD_TYPES,
  MILESTONES,
  fetchOnboardingConfig,
  fetchTemplateHistory,
  saveOnboardingTemplate,
} from "@/lib/onboarding-api";
import type {
  OnboardingConfig,
  OnboardingField,
  OnboardingModule,
  OnboardingRules,
  OnboardingStage,
  OnboardingTemplate,
  TemplateHistoryEntry,
} from "@/lib/onboarding-api";

const selectClass =
  "h-7 w-full rounded-md border border-input bg-transparent px-1.5 text-[12px] shadow-xs outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60";
const cellInput = "h-7 text-[12px]";

const RULES: { key: keyof OnboardingRules; label: string; hint: string }[] = [
  {
    key: "first_campaign_min_users",
    label: "First campaign: minimum users reached",
    hint: "A campaign only counts as the first campaign once it reached this many users. Stops test campaigns counting.",
  },
  {
    key: "adopting_min_campaigns",
    label: "Adopting: campaigns live in the last 30 days",
    hint: "Suggest the Adopting stage once this many campaigns are live.",
  },
  {
    key: "idle_days",
    label: "Idle: days after SDK go-live with no campaign",
    hint: "Flag the app as 'Live, no campaign' after this many days.",
  },
  {
    key: "stale_days",
    label: "Stale: days without any change",
    hint: "Flag a record nobody has updated for this long.",
  },
  {
    key: "pilot_ending_days",
    label: "Pilot ending: days before pilot end",
    hint: "Flag pilots ending within this window.",
  },
];

const ROLES = ["chatter", "analyst", "operator", "maintainer", "admin"];

function slug(label: string, taken: Set<string>) {
  let base = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 36);
  if (!/^[a-z]/.test(base)) base = `f_${base}`;
  let key = base || "field";
  let i = 2;
  while (taken.has(key)) key = `${base}_${i++}`;
  return key;
}

function move<T>(list: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

function RowActions({
  index,
  count,
  onMove,
  onRemove,
  disabled,
  removeTitle,
}: {
  index: number;
  count: number;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  disabled: boolean;
  removeTitle?: string;
}) {
  return (
    <div className="flex items-center justify-end gap-0.5">
      <Button
        size="icon"
        variant="ghost"
        className="h-6 w-6"
        disabled={disabled || index === 0}
        onClick={() => onMove(-1)}
        aria-label="Move up"
      >
        <RiArrowUpLine size={13} />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="h-6 w-6"
        disabled={disabled || index === count - 1}
        onClick={() => onMove(1)}
        aria-label="Move down"
      >
        <RiArrowDownLine size={13} />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="h-6 w-6 text-muted-foreground hover:text-red-600"
        disabled={disabled}
        onClick={onRemove}
        aria-label="Remove"
        title={removeTitle}
      >
        <RiCloseLine size={13} />
      </Button>
    </div>
  );
}

function toTemplate(c: OnboardingConfig): OnboardingTemplate {
  return {
    stages: c.stages.map((s) => ({ ...s })),
    fields: c.fields.map((f) => ({ ...f })),
    modules: c.modules.map((m) => ({ ...m })),
    rules: { ...c.rules },
    edit_min_role: c.edit_min_role,
    template_min_role: c.template_min_role,
  };
}

export default function OnboardingTemplatePage() {
  const [config, setConfig] = useState<OnboardingConfig | null>(null);
  const [tpl, setTpl] = useState<OnboardingTemplate | null>(null);
  const [history, setHistory] = useState<TemplateHistoryEntry[]>([]);
  const [newKeys, setNewKeys] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState("fields");

  async function load() {
    const [c, h] = await Promise.all([
      fetchOnboardingConfig(),
      fetchTemplateHistory(),
    ]);
    setConfig(c);
    setTpl(toTemplate(c));
    setHistory(h);
    setNewKeys(new Set());
  }

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);

  const dirty = useMemo(
    () =>
      !!config && !!tpl && JSON.stringify(toTemplate(config)) !== JSON.stringify(tpl),
    [config, tpl],
  );

  if (!tpl || !config) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6 space-y-3">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : (
          <>
            <Skeleton className="h-6 w-64" />
            <Skeleton className="h-64 w-full" />
          </>
        )}
      </div>
    );
  }

  const canEdit = config.can_edit_template;
  const stageOptions = tpl.stages;
  const sections = [...new Set(tpl.fields.map((f) => f.section || "Other"))];

  const patch = <K extends "stages" | "fields" | "modules">(
    coll: K,
    i: number,
    value: Partial<OnboardingTemplate[K][number]>,
  ) =>
    setTpl((t) =>
      t
        ? {
            ...t,
            [coll]: (t[coll] as unknown[]).map((x, j) =>
              j === i ? { ...(x as object), ...value } : x,
            ),
          }
        : t,
    );
  const setList = <K extends "stages" | "fields" | "modules">(
    coll: K,
    list: OnboardingTemplate[K],
  ) => setTpl((t) => (t ? { ...t, [coll]: list } : t));

  function addItem(coll: "stages" | "fields" | "modules") {
    if (!tpl) return;
    const taken = new Set((tpl[coll] as { key: string }[]).map((x) => x.key));
    const key = slug(
      coll === "stages" ? "new_stage" : coll === "modules" ? "new_module" : "new_field",
      taken,
    );
    setNewKeys((s) => new Set(s).add(`${coll}:${key}`));
    if (coll === "stages") {
      // New stages go before the terminal ones.
      const firstTerminal = tpl.stages.findIndex((s) => s.terminal);
      const at = firstTerminal < 0 ? tpl.stages.length : firstTerminal;
      const next = [...tpl.stages];
      next.splice(at, 0, { key, label: "", description: "" });
      setList("stages", next);
    } else if (coll === "modules") {
      setList("modules", [...tpl.modules, { key, label: "" }]);
    } else {
      setList("fields", [
        ...tpl.fields,
        { key, label: "", type: "text", section: sections[0] ?? "Other", source: "human" },
      ]);
    }
  }

  // New items get their key from the label; saved keys never change, so
  // stored values stay attached.
  function relabel(coll: "stages" | "fields" | "modules", i: number, label: string) {
    if (!tpl) return;
    const item = tpl[coll][i] as { key: string };
    const isNew = newKeys.has(`${coll}:${item.key}`);
    if (!isNew) return patch(coll, i, { label } as never);
    const taken = new Set(
      (tpl[coll] as { key: string }[]).filter((_, j) => j !== i).map((x) => x.key),
    );
    const key = slug(label || "new", taken);
    setNewKeys((s) => {
      const next = new Set(s);
      next.delete(`${coll}:${item.key}`);
      next.add(`${coll}:${key}`);
      return next;
    });
    patch(coll, i, { label, key } as never);
  }

  async function save() {
    if (!tpl) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const res = await saveOnboardingTemplate(tpl);
      setSaved(res.changes);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8">
      <Breadcrumb className="mb-3">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link href="/onboarding">Onboarding</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>Template</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Onboarding template</h1>
          <p className="text-[13px] text-muted-foreground">
            The format of client data we track: stages, fields, the module
            catalogue and the rules behind flags. Changes apply to every
            record and are versioned.
          </p>
        </div>
        <div className="text-[12px] text-muted-foreground text-right">
          {config.version ? (
            <>
              Version {config.version}
              {config.updated_by ? ` · ${config.updated_by.split("@")[0]}` : ""}
              {config.updated_at ? (
                <>
                  {" · "}
                  <ClientTimestamp iso={config.updated_at} variant="short" />
                </>
              ) : null}
            </>
          ) : (
            "Default template (never edited)"
          )}
        </div>
      </div>

      {!canEdit && (
        <Alert className="mb-4">
          <AlertDescription>
            You can view the template. Editing needs the{" "}
            <b>{config.template_min_role}</b> role or higher.
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="whitespace-pre-line">
            {error.split("; ").join("\n")}
          </AlertDescription>
        </Alert>
      )}
      {saved && (
        <Alert className="mb-4 border-emerald-200 bg-emerald-50">
          <AlertDescription>
            {saved.length
              ? `Saved: ${saved.join(" · ")}`
              : "No changes to save."}
          </AlertDescription>
        </Alert>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-3">
          <TabsTrigger value="fields">Fields ({tpl.fields.length})</TabsTrigger>
          <TabsTrigger value="stages">Stages ({tpl.stages.length})</TabsTrigger>
          <TabsTrigger value="modules">
            Modules ({tpl.modules.length})
          </TabsTrigger>
          <TabsTrigger value="rules">Rules & access</TabsTrigger>
          <TabsTrigger value="history">History ({history.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="fields">
          <Card className="p-0 gap-0 overflow-x-auto">
            <table className="w-full text-[12px]" data-testid="template-fields">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-normal w-[180px]">Field</th>
                  <th className="px-2 py-2 text-left font-normal w-[110px]">Type</th>
                  <th className="px-2 py-2 text-left font-normal w-[120px]">Section</th>
                  <th className="px-2 py-2 text-left font-normal w-[120px]">Filled by</th>
                  <th className="px-2 py-2 text-left font-normal w-[130px]">
                    Required from
                  </th>
                  <th className="px-2 py-2 text-center font-normal w-[60px]">On card</th>
                  <th className="px-2 py-2 text-left font-normal">Options</th>
                  <th className="w-[84px]" />
                </tr>
              </thead>
              <tbody>
                {tpl.fields.map((f: OnboardingField, i) => {
                  const isNew = newKeys.has(`fields:${f.key}`);
                  return (
                    <tr key={`${f.key}-${i}`} className="border-t align-top">
                      <td className="px-2 py-1.5">
                        <Input
                          className={cellInput}
                          value={f.label}
                          disabled={!canEdit}
                          placeholder="Label"
                          onChange={(e) => relabel("fields", i, e.target.value)}
                        />
                        <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                          {f.key}
                          {isNew && " · new"}
                        </div>
                      </td>
                      <td className="px-2 py-1.5">
                        <select
                          className={selectClass}
                          value={f.type}
                          disabled={!canEdit || !!f.options_from}
                          onChange={(e) =>
                            patch("fields", i, {
                              type: e.target.value as OnboardingField["type"],
                            })
                          }
                        >
                          {FIELD_TYPES.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-2 py-1.5">
                        <Input
                          className={cellInput}
                          list="onb-sections"
                          value={f.section ?? ""}
                          disabled={!canEdit}
                          onChange={(e) => patch("fields", i, { section: e.target.value })}
                        />
                      </td>
                      <td className="px-2 py-1.5">
                        <select
                          className={selectClass}
                          value={f.source ?? "human"}
                          disabled={!canEdit}
                          onChange={(e) =>
                            patch("fields", i, {
                              source: e.target.value as OnboardingField["source"],
                            })
                          }
                        >
                          {FIELD_SOURCES.map((s) => (
                            <option key={s.key} value={s.key}>
                              {s.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-2 py-1.5">
                        <select
                          className={selectClass}
                          value={f.required_from ?? ""}
                          disabled={!canEdit}
                          onChange={(e) =>
                            patch("fields", i, {
                              required_from: e.target.value || null,
                            })
                          }
                        >
                          <option value="">Optional</option>
                          {stageOptions
                            .filter((s) => !s.terminal)
                            .map((s) => (
                              <option key={s.key} value={s.key}>
                                {s.label || s.key}
                              </option>
                            ))}
                        </select>
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <Switch
                          size="sm"
                          checked={!!f.on_card}
                          disabled={!canEdit}
                          onCheckedChange={(v) => patch("fields", i, { on_card: v })}
                          aria-label={`Show ${f.label} on card`}
                        />
                      </td>
                      <td className="px-2 py-1.5">
                        {f.options_from === "modules" ? (
                          <span className="text-muted-foreground">
                            From the module catalogue
                          </span>
                        ) : f.type === "select" || f.type === "multiselect" ? (
                          <Input
                            className={cellInput}
                            value={(f.options ?? []).join(", ")}
                            disabled={!canEdit}
                            placeholder="Comma separated"
                            onChange={(e) =>
                              patch("fields", i, {
                                options: e.target.value
                                  .split(",")
                                  .map((o) => o.trimStart()),
                              })
                            }
                            onBlur={(e) =>
                              patch("fields", i, {
                                options: e.target.value
                                  .split(",")
                                  .map((o) => o.trim())
                                  .filter(Boolean),
                              })
                            }
                          />
                        ) : (
                          <span className="text-muted-foreground/60">-</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <RowActions
                          index={i}
                          count={tpl.fields.length}
                          disabled={!canEdit}
                          onMove={(dir) => setList("fields", move(tpl.fields, i, dir))}
                          onRemove={() =>
                            setList(
                              "fields",
                              tpl.fields.filter((_, j) => j !== i),
                            )
                          }
                          removeTitle="Hides the field. Values already saved are kept"
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <datalist id="onb-sections">
              {sections.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Card>
          {canEdit && (
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => addItem("fields")}
            >
              <RiAddLine size={14} /> Add field
            </Button>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            Removing a field hides it but keeps saved values, so adding it back
            with the same key restores them. &quot;Required from&quot; marks the
            field as needed once a record reaches that stage. &quot;Filled
            by&quot; tells people and sync agents who owns the value.
          </p>
        </TabsContent>

        <TabsContent value="stages">
          <Card className="p-0 gap-0 overflow-x-auto">
            <table className="w-full text-[12px]" data-testid="template-stages">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-normal w-[30px]">#</th>
                  <th className="px-2 py-2 text-left font-normal w-[200px]">Stage</th>
                  <th className="px-2 py-2 text-left font-normal">
                    Enter when (rule shown to the team)
                  </th>
                  <th className="px-2 py-2 text-left font-normal w-[140px]">
                    Milestone
                  </th>
                  <th className="px-2 py-2 text-center font-normal w-[70px]">
                    End stage
                  </th>
                  <th className="w-[84px]" />
                </tr>
              </thead>
              <tbody>
                {tpl.stages.map((s: OnboardingStage, i) => (
                  <tr
                    key={`${s.key}-${i}`}
                    className={cn("border-t align-top", s.milestone && "bg-emerald-50/40")}
                  >
                    <td className="px-2 py-2 text-muted-foreground tabular-nums">
                      {i + 1}
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellInput}
                        value={s.label}
                        disabled={!canEdit}
                        placeholder="Label"
                        onChange={(e) => relabel("stages", i, e.target.value)}
                      />
                      <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                        {s.key}
                      </div>
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellInput}
                        value={s.description ?? ""}
                        disabled={!canEdit}
                        onChange={(e) =>
                          patch("stages", i, { description: e.target.value })
                        }
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <select
                        className={selectClass}
                        value={s.milestone ?? ""}
                        disabled={!canEdit}
                        onChange={(e) =>
                          patch("stages", i, { milestone: e.target.value || null })
                        }
                      >
                        <option value="">-</option>
                        {MILESTONES.map((m) => (
                          <option key={m.key} value={m.key}>
                            {m.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <Switch
                        size="sm"
                        checked={!!s.terminal}
                        disabled={!canEdit}
                        onCheckedChange={(v) => patch("stages", i, { terminal: v })}
                        aria-label={`${s.label} is an end stage`}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <RowActions
                        index={i}
                        count={tpl.stages.length}
                        disabled={!canEdit}
                        onMove={(dir) => setList("stages", move(tpl.stages, i, dir))}
                        onRemove={() =>
                          setList(
                            "stages",
                            tpl.stages.filter((_, j) => j !== i),
                          )
                        }
                        removeTitle="Only possible when no record is in this stage"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          {canEdit && (
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => addItem("stages")}
            >
              <RiAddLine size={14} /> Add stage
            </Button>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            Milestones connect a stage to product data: <b>SDK live</b> (first
            prod data), <b>First campaign</b> (a campaign reached the minimum
            users) and <b>Adopting</b> (enough campaigns live). Cards suggest
            moving to a milestone stage when the data shows it. End stages
            (handed over, churned) stop flags.
          </p>
        </TabsContent>

        <TabsContent value="modules">
          <Card className="p-0 gap-0 overflow-x-auto">
            <table className="w-full text-[12px]" data-testid="template-modules">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 text-left font-normal w-[180px]">Module</th>
                  <th className="px-2 py-2 text-left font-normal">
                    Enabled when (dashboard switch)
                  </th>
                  <th className="px-2 py-2 text-left font-normal">
                    Integrated when
                  </th>
                  <th className="px-2 py-2 text-left font-normal">In use when</th>
                  <th className="w-[84px]" />
                </tr>
              </thead>
              <tbody>
                {tpl.modules.map((m: OnboardingModule, i) => (
                  <tr key={`${m.key}-${i}`} className="border-t align-top">
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellInput}
                        value={m.label}
                        disabled={!canEdit}
                        placeholder="Label"
                        onChange={(e) => relabel("modules", i, e.target.value)}
                      />
                      <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                        {m.key}
                      </div>
                    </td>
                    {(["enabled_signal", "integrated_signal", "usage_signal"] as const).map(
                      (k) => (
                        <td key={k} className="px-2 py-1.5">
                          <Input
                            className={cellInput}
                            value={m[k] ?? ""}
                            disabled={!canEdit}
                            onChange={(e) => patch("modules", i, { [k]: e.target.value })}
                          />
                        </td>
                      ),
                    )}
                    <td className="px-2 py-1.5">
                      <RowActions
                        index={i}
                        count={tpl.modules.length}
                        disabled={!canEdit}
                        onMove={(dir) => setList("modules", move(tpl.modules, i, dir))}
                        onRemove={() =>
                          setList(
                            "modules",
                            tpl.modules.filter((_, j) => j !== i),
                          )
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          {canEdit && (
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => addItem("modules")}
            >
              <RiAddLine size={14} /> Add module
            </Button>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            One list drives all four columns on a client page: Paid, Enabled,
            Integrated and In use. The &quot;when&quot; columns tell the team
            and the sync agent how each layer is checked. Records store the
            module label, so rename a module only before it is in use.
          </p>
        </TabsContent>

        <TabsContent value="rules">
          <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
            <Card className="p-4 gap-3">
              <h2 className="text-[13px] font-semibold">Rules</h2>
              {RULES.map((r) => (
                <label
                  key={r.key}
                  className="grid items-start gap-2 md:grid-cols-[1fr_110px]"
                >
                  <span>
                    <span className="block text-[13px]">{r.label}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {r.hint}
                    </span>
                  </span>
                  <Input
                    type="number"
                    min={0}
                    className="h-8 text-[13px] tabular-nums"
                    value={tpl.rules[r.key]}
                    disabled={!canEdit}
                    data-testid={`rule-${r.key}`}
                    onChange={(e) =>
                      setTpl((t) =>
                        t
                          ? {
                              ...t,
                              rules: {
                                ...t.rules,
                                [r.key]:
                                  e.target.value === "" ? 0 : Number(e.target.value),
                              },
                            }
                          : t,
                      )
                    }
                  />
                </label>
              ))}
            </Card>
            <Card className="p-4 gap-3 self-start">
              <h2 className="text-[13px] font-semibold">Access</h2>
              {(
                [
                  ["edit_min_role", "Edit records"],
                  ["template_min_role", "Edit this template"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="space-y-1">
                  <span className="text-xs text-muted-foreground">{label}</span>
                  <select
                    className={cn(selectClass, "h-8 text-[13px]")}
                    value={tpl[key]}
                    disabled={!canEdit}
                    onChange={(e) =>
                      setTpl((t) => (t ? { ...t, [key]: e.target.value } : t))
                    }
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r === "chatter" ? "Everyone signed in" : `${r} and above`}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <p className="text-[11px] text-muted-foreground">
                Everyone can always view Onboarding.
              </p>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="history">
          <Card className="p-4 gap-3">
            {history.length === 0 && (
              <p className="text-[13px] text-muted-foreground">
                No template changes yet. The default template is in use.
              </p>
            )}
            <ol className="space-y-3">
              {history.map((h) => (
                <li key={h.version} className="border-l pl-3 text-[12px]">
                  <div className="text-muted-foreground">
                    <Badge variant="outline" className="mr-1 rounded text-[10px]">
                      v{h.version}
                    </Badge>
                    <ClientTimestamp iso={h.at} variant="short" /> ·{" "}
                    {h.by.split("@")[0]}
                  </div>
                  <ul className="mt-1 list-disc pl-4">
                    {h.changes.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          </Card>
        </TabsContent>
      </Tabs>

      {canEdit && (
        <div className="sticky bottom-0 mt-4 flex items-center gap-2 border-t bg-background/95 py-3">
          <span className="text-[12px] text-muted-foreground">
            {dirty ? "Unsaved changes" : "No changes"}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            disabled={!dirty || saving}
            onClick={() => {
              setTpl(toTemplate(config));
              setNewKeys(new Set());
              setError(null);
            }}
          >
            Discard
          </Button>
          <Button size="sm" disabled={!dirty || saving} onClick={save}>
            {saving && <RiLoader4Line size={14} className="animate-spin" />}
            Save template
          </Button>
        </div>
      )}
    </div>
  );
}
