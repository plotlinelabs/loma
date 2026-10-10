"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiArrowRightLine,
  RiArrowRightSLine,
  RiKanbanView2,
  RiLayoutGridLine,
  RiSearchLine,
  RiTableLine,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/EmptyState";
import {
  createOnboardingRecord,
  fetchOnboardingConfig,
  fetchOnboardingRecords,
  updateOnboardingRecord,
  FLAG_BORDER,
  FLAG_COLORS,
  FLAG_LABELS,
  formatValue,
  milestoneIndex,
} from "@/lib/onboarding-api";
import type {
  OnboardingConfig,
  OnboardingRecord,
  OnboardingStage,
} from "@/lib/onboarding-api";

type ViewKey =
  | "active"
  | "integrating"
  | "live"
  | "idle"
  | "attention"
  | "handed_over"
  | "closed"
  | "all";

const VIEWS: { key: ViewKey; label: string }[] = [
  { key: "active", label: "All active" },
  { key: "integrating", label: "In integration" },
  { key: "live", label: "Live / Adopting" },
  { key: "idle", label: "Live, no campaign" },
  { key: "attention", label: "Needs attention" },
  { key: "handed_over", label: "Handed over" },
  { key: "closed", label: "Churned / Lost" },
  { key: "all", label: "All" },
];

// Fields with their own place on the card, so the generic list skips them.
const CARD_SPECIAL = new Set([
  "owner",
  "next_step",
  "next_step_due",
  "first_campaign_live",
]);

/**
 * Stage groups derived from the template: everything before the stage
 * carrying the `sdk_live` milestone is "integration", from it onwards is
 * "live". Terminal stages are split by position (first = handed over).
 */
function stageGroups(stages: OnboardingStage[]) {
  const liveAt = milestoneIndex(stages, "sdk_live");
  const cut = liveAt < 0 ? stages.length : liveAt;
  const open = stages.filter((s) => !s.terminal);
  const terminal = stages.filter((s) => s.terminal);
  return {
    integrating: open.filter((s) => stages.indexOf(s) < cut).map((s) => s.key),
    live: open.filter((s) => stages.indexOf(s) >= cut).map((s) => s.key),
    handedOver: terminal.slice(0, 1).map((s) => s.key),
    closed: terminal.slice(1).map((s) => s.key),
  };
}

type Groups = ReturnType<typeof stageGroups>;

function stagesForView(view: ViewKey, g: Groups): string[] | null {
  switch (view) {
    case "active":
      return [...g.integrating, ...g.live];
    case "integrating":
      return g.integrating;
    case "live":
    case "idle":
      return g.live;
    case "handed_over":
      return g.handedOver;
    case "closed":
      return g.closed;
    default:
      return null; // all stages
  }
}

function inView(r: OnboardingRecord, view: ViewKey, g: Groups) {
  if (view === "attention") return r.derived.flags.length > 0;
  if (view === "idle") return r.derived.flags.includes("idle");
  const allowed = stagesForView(view, g);
  return allowed === null || allowed.includes(r.stage);
}

function median(nums: number[]): number | null {
  if (!nums.length) return null;
  const s = [...nums].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "red";
}) {
  return (
    <Card className="px-4 py-3 gap-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          "text-xl font-semibold tabular-nums mt-0.5",
          tone === "red" && value !== 0 && "text-red-600",
        )}
      >
        {value}
      </div>
      {hint && (
        <div className="text-[11px] text-muted-foreground/80 mt-0.5">
          {hint}
        </div>
      )}
    </Card>
  );
}

type Layout = "board" | "table";

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  if (children === "" || children === null || children === undefined)
    return null;
  return (
    <div className="flex gap-1.5 text-[12px] leading-5">
      <span className="text-muted-foreground shrink-0 w-[104px] truncate">
        {label}
      </span>
      <span className="min-w-0 truncate">{children}</span>
    </div>
  );
}

function shortDate(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function AdoptionLine({ r, minUsers }: { r: OnboardingRecord; minUsers: number }) {
  const d = r.derived;
  const f = r.fields;
  if (d.first_campaign_qualified) {
    return (
      <div className="mt-1.5 rounded bg-emerald-50 px-2 py-1 text-[12px] leading-snug text-emerald-800">
        First campaign {shortDate(String(f.first_campaign_live))}
        {f.first_campaign_users ? ` · ${formatValue(f.first_campaign_users)} users` : ""}
        {d.days_live_to_first_campaign !== null
          ? ` · ${d.days_live_to_first_campaign}d after live`
          : ""}
      </div>
    );
  }
  if (d.days_live_without_campaign !== null) {
    const idle = d.flags.includes("idle");
    return (
      <div
        className={cn(
          "mt-1.5 rounded px-2 py-1 text-[12px] leading-snug",
          idle ? "bg-red-50 text-red-700" : "bg-muted/50 text-muted-foreground",
        )}
        title={`A first campaign counts once it reaches ${minUsers}+ users`}
      >
        No campaign with {minUsers}+ users · {d.days_live_without_campaign}d since
        live
      </div>
    );
  }
  return null;
}

function ModulesLine({ r, config }: { r: OnboardingRecord; config: OnboardingConfig }) {
  const f = r.fields;
  const list = (k: string) => (Array.isArray(f[k]) ? (f[k] as string[]) : []);
  const paid = list("modules_paid");
  const integrated = list("modules_integrated");
  const inUse = list("modules_in_use");
  const integ = r.derived.integration;
  if (!paid.length && !integrated.length && !inUse.length && !integ.tracked) return null;
  const gaps = r.derived.module_gaps;
  // One chip per group the client has something in: integrated / paid.
  const basis = paid.length ? paid : integrated;
  const groups = config.module_groups
    .map((g) => {
      const labels = config.modules.filter((m) => m.group === g.key).map((m) => m.label);
      const total = labels.filter((l) => basis.includes(l)).length;
      const done = labels.filter((l) => basis.includes(l) && integrated.includes(l)).length;
      return { label: g.label.replace(/ campaigns$/, ""), total, done };
    })
    .filter((g) => g.total > 0);
  return (
    <div className="mt-1.5 text-[12px] leading-5" data-testid="card-modules">
      <div className="flex items-center gap-1.5 tabular-nums">
        <span className="text-muted-foreground w-[104px] shrink-0">Modules</span>
        <span title="Paid / Integrated / In use">
          <span className={cn(!paid.length && "text-amber-700")}>
            {paid.length ? `${paid.length} paid` : "paid not set"}
          </span>
          <span className="text-muted-foreground"> · </span>
          {integrated.length} integrated
          <span className="text-muted-foreground"> · </span>
          {inUse.length} in use
        </span>
      </div>
      {groups.length > 0 && (
        <div
          className="flex flex-wrap gap-1 py-0.5"
          title={
            paid.length
              ? "Per group: modules integrated / modules paid"
              : "Per group: modules integrated (paid not ticked yet)"
          }
        >
          {groups.map((g) => (
            <span
              key={g.label}
              className={cn(
                "rounded border px-1 text-[11px] leading-4 tabular-nums",
                paid.length && g.done < g.total
                  ? "border-orange-200 bg-orange-50 text-orange-700"
                  : "border-border text-muted-foreground",
              )}
            >
              {g.label} {paid.length ? `${g.done}/${g.total}` : g.total}
            </span>
          ))}
        </div>
      )}
      {gaps.paid_not_integrated.length > 0 && (
        <div className="truncate text-orange-700" title="Paid for, not integrated">
          Not integrated: {gaps.paid_not_integrated.join(", ")}
        </div>
      )}
      {gaps.enabled_not_paid.length > 0 && (
        <div className="truncate text-violet-700" title="Switched on, not in contract">
          Enabled, unpaid: {gaps.enabled_not_paid.join(", ")}
        </div>
      )}
      {integ.tracked && (
        <>
          <div className="flex items-center gap-1.5 tabular-nums">
            <span className="text-muted-foreground w-[104px] shrink-0">Integration</span>
            <span title="Integration items done / needed">
              {integ.done_count}/{integ.needed.length} items done
            </span>
          </div>
          {integ.pending.length > 0 && (
            <div
              className="truncate text-orange-700"
              title={`Still to build: ${integ.pending.join(", ")}`}
            >
              Pending: {integ.pending.join(", ")}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function BoardCard({
  r,
  config,
  stageLabel,
  appCount,
  draggable,
  onDragStart,
  onOpen,
  onMove,
}: {
  r: OnboardingRecord;
  config: OnboardingConfig;
  stageLabel: Record<string, string>;
  appCount: number;
  draggable: boolean;
  onDragStart: (e: React.DragEvent) => void;
  onOpen: () => void;
  onMove: (stage: string) => void;
}) {
  const d = r.derived;
  const f = r.fields;
  const owner = formatValue(f.owner).split("@")[0];
  const daysToLive =
    d.days_to_live_net !== null
      ? `${d.days_to_live_net}d${d.days_to_live_gross !== d.days_to_live_net ? ` (gross ${d.days_to_live_gross}d)` : ""}`
      : "";
  const next = formatValue(f.next_step);
  const nextDue = formatValue(f.next_step_due);
  const cardFields = config.fields.filter(
    (fd) => fd.on_card && !CARD_SPECIAL.has(fd.key),
  );
  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onClick={onOpen}
      data-testid="onboarding-card"
      className={cn(
        "group rounded-md border border-l-[3px] bg-card p-2.5 shadow-xs cursor-pointer hover:border-foreground/20 hover:shadow-sm transition",
        d.flags.length ? FLAG_BORDER[d.flags[0]] : "border-l-border",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <Link
          href={`/onboarding/${r.record_id}`}
          onClick={(e) => e.stopPropagation()}
          className="text-[13px] font-medium leading-snug hover:underline"
        >
          {r.name}
        </Link>
        {owner && (
          <span
            title={formatValue(f.owner)}
            className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
          >
            {owner}
          </span>
        )}
      </div>
      {(appCount > 1 || r.account !== r.name) && (
        <div className="text-[11px] text-muted-foreground mt-0.5">
          {r.account}
          {appCount > 1 ? ` · ${appCount} apps` : ""}
        </div>
      )}
      <div className="mt-1.5">
        {cardFields.map((fd) => {
          let v = formatValue(f[fd.key]);
          if (fd.key === "blocker_owner" && v === "None") v = "";
          return (
            <KV key={fd.key} label={fd.label}>
              {v}
            </KV>
          );
        })}
        <KV label="Days to live">{daysToLive}</KV>
        <KV label="Pilot left">
          {d.pilot_days_left !== null ? `${d.pilot_days_left}d` : ""}
        </KV>
      </div>
      <ModulesLine r={r} config={config} />
      <AdoptionLine r={r} minUsers={config.rules.first_campaign_min_users} />
      {(next || nextDue) && (
        <div className="mt-1.5 rounded bg-muted/50 px-2 py-1 text-[12px] leading-snug">
          <span className="text-muted-foreground">Next: </span>
          {next || "-"}
          {nextDue && (
            <span className="text-muted-foreground"> · due {nextDue}</span>
          )}
        </div>
      )}
      {d.suggested_stage && config.can_edit && (
        <button
          type="button"
          data-testid="onboarding-suggest"
          onClick={(e) => {
            e.stopPropagation();
            onMove(d.suggested_stage as string);
          }}
          className="mt-1.5 flex w-full items-center gap-1 rounded border border-dashed border-primary/40 px-2 py-1 text-left text-[12px] text-primary hover:bg-primary/5"
          title="Product data supports this stage"
        >
          Move to {stageLabel[d.suggested_stage] ?? d.suggested_stage}
          <RiArrowRightLine size={12} />
        </button>
      )}
      {(d.flags.length > 0 || d.missing_required.length > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {d.flags.map((fl) => (
            <Badge
              key={fl}
              variant="outline"
              className={cn("rounded text-[10px] px-1.5 py-0", FLAG_COLORS[fl])}
            >
              {FLAG_LABELS[fl] ?? fl}
            </Badge>
          ))}
          {d.missing_required.length > 0 && (
            <Badge
              variant="outline"
              className="rounded text-[10px] px-1.5 py-0 text-muted-foreground"
              title={`Required by this stage: ${d.missing_required
                .map((k) => config.fields.find((x) => x.key === k)?.label ?? k)
                .join(", ")}`}
            >
              {d.missing_required.length} to fill
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

export default function OnboardingPage() {
  const router = useRouter();
  const [config, setConfig] = useState<OnboardingConfig | null>(null);
  const [records, setRecords] = useState<OnboardingRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>("active");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [layout, setLayout] = useState<Layout>("board");
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropStage, setDropStage] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchOnboardingConfig(), fetchOnboardingRecords()])
      .then(([c, r]) => {
        setConfig(c);
        setRecords(r);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const stageLabel = useMemo(
    () =>
      Object.fromEntries((config?.stages ?? []).map((s) => [s.key, s.label])),
    [config],
  );
  const groups = useMemo(() => stageGroups(config?.stages ?? []), [config]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter(
      (r) =>
        inView(r, view, groups) &&
        (!q ||
          r.name.toLowerCase().includes(q) ||
          r.account.toLowerCase().includes(q) ||
          formatValue(r.fields.owner).toLowerCase().includes(q)),
    );
  }, [records, view, query, groups]);

  const accountGroups = useMemo(() => {
    const map = new Map<string, OnboardingRecord[]>();
    for (const r of visible) {
      const list = map.get(r.account) ?? [];
      list.push(r);
      map.set(r.account, list);
    }
    return [...map.entries()];
  }, [visible]);

  const appCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of records) m[r.account] = (m[r.account] ?? 0) + 1;
    return m;
  }, [records]);

  const lanes = useMemo(() => {
    const allowed = stagesForView(view, groups);
    const stages = (config?.stages ?? []).filter(
      (s) => allowed === null || allowed.includes(s.key),
    );
    const built = stages.map((s) => ({
      ...s,
      // Keep a client's apps next to each other inside a lane.
      cards: visible
        .filter((r) => r.stage === s.key)
        .sort(
          (a, b) =>
            a.account.localeCompare(b.account) || a.name.localeCompare(b.name),
        ),
    }));
    // Filtered views (needs attention, idle, search) only show lanes with matches.
    const filtered = view === "attention" || view === "idle" || query.trim() !== "";
    return filtered ? built.filter((l) => l.cards.length > 0) : built;
  }, [view, config, visible, query, groups]);

  // A view with one lane (handed over, churned) spreads its cards in a grid.
  const single = !loading && lanes.length === 1;
  const boardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    boardRef.current?.scrollTo({ left: 0 });
  }, [view, query]);

  async function moveToStage(recordId: string, stage: string) {
    const rec = records.find((r) => r.record_id === recordId);
    if (!rec || rec.stage === stage) return;
    const prev = records;
    setRecords((rs) =>
      rs.map((r) => (r.record_id === recordId ? { ...r, stage } : r)),
    );
    try {
      const updated = await updateOnboardingRecord(recordId, { stage });
      setRecords((rs) =>
        rs.map((r) => (r.record_id === recordId ? updated : r)),
      );
    } catch (e) {
      setRecords(prev);
      setError((e as Error).message);
    }
  }

  const stats = useMemo(() => {
    const live = records.filter((r) => r.derived.days_to_live_net !== null);
    const toCampaign = records
      .map((r) => r.derived.days_live_to_first_campaign)
      .filter((n): n is number => n !== null);
    return {
      integrating: records.filter((r) => groups.integrating.includes(r.stage))
        .length,
      live: records.filter((r) => groups.live.includes(r.stage)).length,
      idle: records.filter((r) => r.derived.flags.includes("idle")).length,
      attention: records.filter((r) => r.derived.flags.length > 0).length,
      medianNet: median(live.map((r) => r.derived.days_to_live_net as number)),
      medianGross: median(
        live.map((r) => r.derived.days_to_live_gross as number),
      ),
      medianToCampaign: median(toCampaign),
      toCampaignCount: toCampaign.length,
    };
  }, [records, groups]);

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const rec = await createOnboardingRecord({ name: newName.trim() });
      router.push(`/onboarding/${rec.record_id}`);
    } catch (e) {
      setError((e as Error).message);
      setCreating(false);
    }
  }

  const minUsers = config?.rules.first_campaign_min_users ?? 100;

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-6 md:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
        <div>
          <h1 className="text-lg font-semibold">Onboarding</h1>
          <p className="text-[13px] text-muted-foreground">
            Every customer integration, from kickoff to first campaign to
            handover. One card per app.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {config?.can_edit && (
            <>
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder="New app, e.g. Careem - Pay"
                className="h-8 w-56 text-[13px]"
              />
              <Button
                size="sm"
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
              >
                <RiAddLine size={14} /> Add
              </Button>
            </>
          )}
          <Button asChild size="sm" variant="outline">
            <Link href="/onboarding/template">
              <RiLayoutGridLine size={14} /> Template
            </Link>
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6 mb-5">
        <Stat label="In integration" value={stats.integrating} />
        <Stat label="Live / Adopting" value={stats.live} />
        <Stat
          label="Live, no campaign"
          value={stats.idle}
          tone="red"
          hint={`No campaign with ${minUsers}+ users after ${config?.rules.idle_days ?? 14}d`}
        />
        <Stat
          label="Needs attention"
          value={stats.attention}
          hint="Any flag set"
        />
        <Stat
          label="Median days to live"
          value={stats.medianNet ?? "-"}
          hint={
            stats.medianGross !== null
              ? `Net of client delays. Gross ${stats.medianGross}`
              : undefined
          }
        />
        <Stat
          label="Median live to 1st campaign"
          value={stats.medianToCampaign !== null ? `${stats.medianToCampaign}d` : "-"}
          hint={`${stats.toCampaignCount} app${stats.toCampaignCount === 1 ? "" : "s"} with a known date`}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        {VIEWS.map((v) => (
          <Button
            key={v.key}
            size="sm"
            variant={view === v.key ? "secondary" : "ghost"}
            className="h-7 text-[13px]"
            onClick={() => setView(v.key)}
          >
            {v.label}
            <span className="ml-1 text-muted-foreground tabular-nums">
              {records.filter((r) => inView(r, v.key, groups)).length}
            </span>
          </Button>
        ))}
        <div className="ml-auto flex items-center rounded-md border p-0.5">
          <Button
            size="sm"
            variant={layout === "board" ? "secondary" : "ghost"}
            className="h-6 px-2 text-[12px]"
            onClick={() => setLayout("board")}
          >
            <RiKanbanView2 size={13} /> Board
          </Button>
          <Button
            size="sm"
            variant={layout === "table" ? "secondary" : "ghost"}
            className="h-6 px-2 text-[12px]"
            onClick={() => setLayout("table")}
          >
            <RiTableLine size={13} /> Table
          </Button>
        </div>
        <div className="relative">
          <RiSearchLine
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search client or owner"
            className="h-8 w-56 pl-8 text-[13px]"
          />
        </div>
      </div>

      {layout === "board" && (
        <div
          ref={boardRef}
          className="-mx-4 overflow-x-auto px-4 pb-2 md:-mx-8 md:px-8"
        >
          <div
            className={cn(
              "flex gap-3 items-start",
              single ? "w-full" : "min-w-max",
            )}
          >
            {(loading
              ? Array.from({ length: 5 }).map((_, i) => ({
                  key: `sk-${i}`,
                  label: "",
                  description: "",
                  milestone: undefined as string | undefined | null,
                  cards: [] as OnboardingRecord[],
                  terminal: false,
                }))
              : lanes
            ).map((lane) => {
              const flagged = lane.cards.filter(
                (c) => c.derived.flags.length > 0,
              ).length;
              const isDrop = dropStage === lane.key && dragId !== null;
              return (
                <div
                  key={lane.key}
                  data-testid="onboarding-lane"
                  onDragOver={(e) => {
                    if (!dragId) return;
                    e.preventDefault();
                    setDropStage(lane.key);
                  }}
                  onDragLeave={() =>
                    setDropStage((s) => (s === lane.key ? null : s))
                  }
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragId) moveToStage(dragId, lane.key);
                    setDragId(null);
                    setDropStage(null);
                  }}
                  className={cn(
                    "shrink-0 rounded-lg border bg-muted/30 flex flex-col max-h-[calc(100vh-290px)]",
                    single
                      ? "w-full"
                      : !loading && lane.cards.length === 0
                        ? "w-[170px]"
                        : "w-[290px]",
                    lane.milestone && "bg-emerald-50/40",
                    isDrop && "ring-2 ring-primary/40 bg-primary/5",
                  )}
                >
                  <div
                    className="px-3 py-2 border-b"
                    title={lane.description || undefined}
                  >
                    <div className="flex items-center justify-between">
                      {loading ? (
                        <Skeleton className="h-3 w-24" />
                      ) : (
                        <span className="text-[13px] font-medium">
                          {lane.label}
                        </span>
                      )}
                      {!loading && (
                        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground tabular-nums">
                          {flagged > 0 && (
                            <span
                              className="rounded bg-red-50 px-1.5 text-red-700"
                              title="Cards with flags"
                            >
                              {flagged} flagged
                            </span>
                          )}
                          {lane.cards.length}
                        </span>
                      )}
                    </div>
                    {!loading && lane.description && lane.cards.length > 0 && (
                      <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground line-clamp-1">
                        {lane.description}
                      </div>
                    )}
                  </div>
                  <div
                    className={cn(
                      "flex-1 overflow-y-auto p-2 min-h-[80px]",
                      single
                        ? "grid content-start gap-2 grid-cols-[repeat(auto-fill,minmax(270px,1fr))]"
                        : "space-y-2",
                    )}
                  >
                    {loading &&
                      Array.from({ length: 3 }).map((_, i) => (
                        <Skeleton key={i} className="h-24 w-full" />
                      ))}
                    {!loading && lane.cards.length === 0 && (
                      <div className="py-6 text-center text-[11px] text-muted-foreground">
                        No clients
                      </div>
                    )}
                    {config &&
                      lane.cards.map((r) => (
                        <BoardCard
                          key={r.record_id}
                          r={r}
                          config={config}
                          stageLabel={stageLabel}
                          appCount={appCounts[r.account] ?? 1}
                          draggable={!!config.can_edit}
                          onDragStart={(e) => {
                            e.dataTransfer.effectAllowed = "move";
                            e.dataTransfer.setData("text/plain", r.record_id);
                            setDragId(r.record_id);
                          }}
                          onOpen={() => router.push(`/onboarding/${r.record_id}`)}
                          onMove={(stage) => moveToStage(r.record_id, stage)}
                        />
                      ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {layout === "table" && (
        <Card className="p-0 gap-0 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Client / App</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Paid / Integr. / In use</TableHead>
                <TableHead>SDK live</TableHead>
                <TableHead>1st campaign</TableHead>
                <TableHead className="text-right">Days to live</TableHead>
                <TableHead>Billing</TableHead>
                <TableHead>Next step</TableHead>
                <TableHead>Flags</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading &&
                Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 10 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-3 w-16" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              {!loading &&
                accountGroups.map(([account, rows]) => {
                  const multi = rows.length > 1 || rows[0].name !== account;
                  const isCollapsed = collapsed[account];
                  return [
                    multi ? (
                      <TableRow
                        key={`acct-${account}`}
                        className="bg-muted/40 hover:bg-muted/60 cursor-pointer"
                        onClick={() =>
                          setCollapsed((c) => ({
                            ...c,
                            [account]: !c[account],
                          }))
                        }
                      >
                        <TableCell
                          colSpan={10}
                          className="py-1.5 text-[13px] font-medium"
                        >
                          <span className="inline-flex items-center gap-1">
                            {isCollapsed ? (
                              <RiArrowRightSLine size={14} />
                            ) : (
                              <RiArrowDownSLine size={14} />
                            )}
                            {account}
                            <span className="text-muted-foreground font-normal">
                              {" "}
                              · {rows.length} app{rows.length > 1 ? "s" : ""}
                            </span>
                          </span>
                        </TableCell>
                      </TableRow>
                    ) : null,
                    ...(isCollapsed
                      ? []
                      : rows.map((r) => {
                          const d = r.derived;
                          const n = (k: string) =>
                            Array.isArray(r.fields[k])
                              ? (r.fields[k] as string[]).length
                              : 0;
                          return (
                            <TableRow
                              key={r.record_id}
                              className="cursor-pointer text-[13px]"
                              onClick={() =>
                                router.push(`/onboarding/${r.record_id}`)
                              }
                            >
                              <TableCell
                                className={cn("font-medium", multi && "pl-8")}
                              >
                                <Link
                                  href={`/onboarding/${r.record_id}`}
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  {r.name}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Badge
                                  variant="outline"
                                  className="rounded font-normal whitespace-nowrap"
                                >
                                  {stageLabel[r.stage] ?? r.stage}
                                </Badge>
                              </TableCell>
                              <TableCell className="text-muted-foreground">
                                {formatValue(r.fields.owner).split("@")[0]}
                              </TableCell>
                              <TableCell className="tabular-nums">
                                {n("modules_paid") || "-"} / {n("modules_integrated")} /{" "}
                                {n("modules_in_use")}
                              </TableCell>
                              <TableCell className="whitespace-nowrap">
                                {formatValue(r.fields.first_live)}
                              </TableCell>
                              <TableCell className="whitespace-nowrap">
                                {d.first_campaign_qualified
                                  ? formatValue(r.fields.first_campaign_live)
                                  : d.days_live_without_campaign !== null
                                    ? (
                                      <span className="text-muted-foreground">
                                        none · {d.days_live_without_campaign}d
                                      </span>
                                    )
                                    : ""}
                              </TableCell>
                              <TableCell className="text-right tabular-nums whitespace-nowrap">
                                {d.days_to_live_net !== null ? (
                                  <span
                                    title={`Gross ${d.days_to_live_gross} days`}
                                  >
                                    {d.days_to_live_net}
                                    {d.days_to_live_gross !==
                                      d.days_to_live_net && (
                                      <span className="text-muted-foreground">
                                        {" "}
                                        / {d.days_to_live_gross}
                                      </span>
                                    )}
                                  </span>
                                ) : (
                                  ""
                                )}
                              </TableCell>
                              <TableCell>
                                {formatValue(r.fields.billing_status)}
                              </TableCell>
                              <TableCell
                                className="max-w-56 truncate"
                                title={formatValue(r.fields.next_step)}
                              >
                                {formatValue(r.fields.next_step)}
                                {r.fields.next_step_due ? (
                                  <span className="text-muted-foreground">
                                    {" "}
                                    · {formatValue(r.fields.next_step_due)}
                                  </span>
                                ) : null}
                              </TableCell>
                              <TableCell>
                                <div className="flex flex-wrap gap-1">
                                  {d.flags.map((f) => (
                                    <Badge
                                      key={f}
                                      variant="outline"
                                      className={cn(
                                        "rounded text-[11px]",
                                        FLAG_COLORS[f],
                                      )}
                                    >
                                      {FLAG_LABELS[f] ?? f}
                                    </Badge>
                                  ))}
                                </div>
                              </TableCell>
                            </TableRow>
                          );
                        })),
                  ];
                })}
            </TableBody>
          </Table>
          {!loading && accountGroups.length === 0 && (
            <EmptyState
              title="No clients in this view"
              description="Try another view or clear the search."
            />
          )}
        </Card>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">
        {config?.can_edit && layout === "board"
          ? "Drag a card to another lane to change its stage. "
          : ""}
        A first campaign counts once it reaches {minUsers}+ users. Days to
        live: net of client-blocked days / gross. Stages, fields, modules and
        thresholds are edited on the Template page.
      </p>
    </div>
  );
}
