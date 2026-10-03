"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiArrowRightSLine,
  RiKanbanView2,
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
  FLAG_COLORS,
  FLAG_LABELS,
  formatValue,
} from "@/lib/onboarding-api";
import type { OnboardingConfig, OnboardingRecord } from "@/lib/onboarding-api";

type ViewKey =
  | "active"
  | "integrating"
  | "pilot"
  | "attention"
  | "handed_over"
  | "closed"
  | "all";

const VIEWS: { key: ViewKey; label: string }[] = [
  { key: "active", label: "All active" },
  { key: "integrating", label: "In integration" },
  { key: "pilot", label: "Live / Pilot" },
  { key: "attention", label: "Needs attention" },
  { key: "handed_over", label: "Handed over" },
  { key: "closed", label: "Churned / Lost" },
  { key: "all", label: "All" },
];

const INTEGRATING = ["signed", "kickoff", "sdk", "advanced", "qa"];

function inView(r: OnboardingRecord, view: ViewKey, terminal: Set<string>) {
  switch (view) {
    case "active":
      return !terminal.has(r.stage);
    case "integrating":
      return INTEGRATING.includes(r.stage);
    case "pilot":
      return r.stage === "live" || r.stage === "pilot";
    case "attention":
      return r.derived.flags.length > 0;
    case "handed_over":
      return r.stage === "handed_over";
    case "closed":
      return r.stage === "closed";
    default:
      return true;
  }
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
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <Card className="px-4 py-3 gap-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums mt-0.5">{value}</div>
      {hint && (
        <div className="text-[11px] text-muted-foreground/80 mt-0.5">
          {hint}
        </div>
      )}
    </Card>
  );
}

type Layout = "board" | "table";

/** Stages a view can contain, so empty lanes still render as drop targets. */
function lanesForView<T extends { key: string; terminal?: boolean }>(
  view: ViewKey,
  stages: T[],
): T[] {
  switch (view) {
    case "active":
      return stages.filter((s) => !s.terminal);
    case "integrating":
      return stages.filter((s) => INTEGRATING.includes(s.key));
    case "pilot":
      return stages.filter((s) => s.key === "live" || s.key === "pilot");
    case "handed_over":
      return stages.filter((s) => s.key === "handed_over");
    case "closed":
      return stages.filter((s) => s.key === "closed");
    default:
      return stages;
  }
}

const FLAG_BORDER: Record<string, string> = {
  overdue: "border-l-red-500",
  late: "border-l-red-500",
  blocked: "border-l-orange-500",
  stale: "border-l-amber-400",
  pilot_ending: "border-l-blue-500",
};

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  if (children === "" || children === null || children === undefined)
    return null;
  return (
    <div className="flex gap-1.5 text-[12px] leading-5">
      <span className="text-muted-foreground shrink-0 w-[68px]">{label}</span>
      <span className="min-w-0 truncate">{children}</span>
    </div>
  );
}

function BoardCard({
  r,
  appCount,
  draggable,
  onDragStart,
  onOpen,
}: {
  r: OnboardingRecord;
  appCount: number;
  draggable: boolean;
  onDragStart: (e: React.DragEvent) => void;
  onOpen: () => void;
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
      <div className="mt-1.5 space-y-0">
        <KV label="Platforms">{formatValue(f.platforms)}</KV>
        <KV label="Pending">{formatValue(f.modules_pending)}</KV>
        <KV label="Target">{formatValue(f.target_go_live)}</KV>
        <KV label="First live">{formatValue(f.first_live)}</KV>
        <KV label="Days to live">{daysToLive}</KV>
        <KV label="Pilot left">
          {d.pilot_days_left !== null ? `${d.pilot_days_left}d` : ""}
        </KV>
        <KV label="Billing">{formatValue(f.billing_status)}</KV>
        <KV label="Blocker">
          {formatValue(f.blocker_owner) === "None"
            ? ""
            : formatValue(f.blocker_owner)}
        </KV>
      </div>
      {(next || nextDue) && (
        <div className="mt-1.5 rounded bg-muted/50 px-2 py-1 text-[12px] leading-snug">
          <span className="text-muted-foreground">Next: </span>
          {next || "-"}
          {nextDue && (
            <span className="text-muted-foreground"> · due {nextDue}</span>
          )}
        </div>
      )}
      {d.flags.length > 0 && (
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
  const terminal = useMemo(
    () =>
      new Set(
        (config?.stages ?? []).filter((s) => s.terminal).map((s) => s.key),
      ),
    [config],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter(
      (r) =>
        inView(r, view, terminal) &&
        (!q ||
          r.name.toLowerCase().includes(q) ||
          r.account.toLowerCase().includes(q) ||
          formatValue(r.fields.owner).toLowerCase().includes(q)),
    );
  }, [records, view, query, terminal]);

  const groups = useMemo(() => {
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
    const stages = lanesForView(view, config?.stages ?? []);
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
    // Filtered views (needs attention, search) only show lanes that have matches.
    const filtered = view === "attention" || query.trim() !== "";
    return filtered ? built.filter((l) => l.cards.length > 0) : built;
  }, [view, config, visible, query]);

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
    const active = records.filter((r) => !terminal.has(r.stage));
    const live = records.filter((r) => r.derived.days_to_live_net !== null);
    return {
      active: active.length,
      integrating: records.filter((r) => INTEGRATING.includes(r.stage)).length,
      pilots: records.filter((r) => r.stage === "pilot" || r.stage === "live")
        .length,
      attention: records.filter((r) => r.derived.flags.length > 0).length,
      medianNet: median(live.map((r) => r.derived.days_to_live_net as number)),
      medianGross: median(
        live.map((r) => r.derived.days_to_live_gross as number),
      ),
    };
  }, [records, terminal]);

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

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-6 md:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
        <div>
          <h1 className="text-lg font-semibold">Onboarding</h1>
          <p className="text-[13px] text-muted-foreground">
            Every customer integration, from kickoff to handover. One card per
            app.
          </p>
        </div>
        {config?.can_edit && (
          <div className="flex items-center gap-2">
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
          </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5 mb-5">
        <Stat label="Active" value={stats.active} />
        <Stat label="In integration" value={stats.integrating} />
        <Stat label="Live / Pilot" value={stats.pilots} />
        <Stat
          label="Needs attention"
          value={stats.attention}
          hint="Overdue, stale, blocked or late"
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
              {records.filter((r) => inView(r, v.key, terminal)).length}
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
                        : "w-[280px]",
                    isDrop && "ring-2 ring-primary/40 bg-primary/5",
                  )}
                >
                  <div className="flex items-center justify-between px-3 py-2 border-b">
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
                  <div
                    className={cn(
                      "flex-1 overflow-y-auto p-2 min-h-[80px]",
                      single
                        ? "grid content-start gap-2 grid-cols-[repeat(auto-fill,minmax(260px,1fr))]"
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
                    {lane.cards.map((r) => (
                      <BoardCard
                        key={r.record_id}
                        r={r}
                        appCount={appCounts[r.account] ?? 1}
                        draggable={!!config?.can_edit}
                        onDragStart={(e) => {
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", r.record_id);
                          setDragId(r.record_id);
                        }}
                        onOpen={() => router.push(`/onboarding/${r.record_id}`)}
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
                <TableHead>Platforms</TableHead>
                <TableHead>Pending</TableHead>
                <TableHead>Target go-live</TableHead>
                <TableHead>First live</TableHead>
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
                    {Array.from({ length: 11 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-3 w-16" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              {!loading &&
                groups.map(([account, rows]) => {
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
                          colSpan={11}
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
                              <TableCell className="max-w-40 truncate">
                                {formatValue(r.fields.platforms)}
                              </TableCell>
                              <TableCell className="max-w-40 truncate">
                                {formatValue(r.fields.modules_pending)}
                              </TableCell>
                              <TableCell className="whitespace-nowrap">
                                {formatValue(r.fields.target_go_live)}
                              </TableCell>
                              <TableCell className="whitespace-nowrap">
                                {formatValue(r.fields.first_live)}
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
          {!loading && groups.length === 0 && (
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
        Days to live: net of client-blocked days / gross. Cards are flagged when
        the next step is overdue, nothing changed for 7 days, a blocker is set,
        the target date passed, or the pilot ends within 14 days.
      </p>
    </div>
  );
}
