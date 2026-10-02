"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { RiAddLine, RiArrowDownSLine, RiArrowRightSLine, RiSearchLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/EmptyState";
import {
  createOnboardingRecord,
  fetchOnboardingConfig,
  fetchOnboardingRecords,
  FLAG_COLORS,
  FLAG_LABELS,
  formatValue,
} from "@/lib/onboarding-api";
import type { OnboardingConfig, OnboardingRecord } from "@/lib/onboarding-api";

type ViewKey = "active" | "integrating" | "pilot" | "attention" | "handed_over" | "closed" | "all";

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

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card className="px-4 py-3 gap-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums mt-0.5">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground/80 mt-0.5">{hint}</div>}
    </Card>
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
    () => Object.fromEntries((config?.stages ?? []).map((s) => [s.key, s.label])),
    [config],
  );
  const terminal = useMemo(
    () => new Set((config?.stages ?? []).filter((s) => s.terminal).map((s) => s.key)),
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

  const stats = useMemo(() => {
    const active = records.filter((r) => !terminal.has(r.stage));
    const live = records.filter((r) => r.derived.days_to_live_net !== null);
    return {
      active: active.length,
      integrating: records.filter((r) => INTEGRATING.includes(r.stage)).length,
      pilots: records.filter((r) => r.stage === "pilot" || r.stage === "live").length,
      attention: records.filter((r) => r.derived.flags.length > 0).length,
      medianNet: median(live.map((r) => r.derived.days_to_live_net as number)),
      medianGross: median(live.map((r) => r.derived.days_to_live_gross as number)),
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
            Every customer integration, from kickoff to handover. One row per app.
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
            <Button size="sm" onClick={handleCreate} disabled={creating || !newName.trim()}>
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
        <Stat label="Needs attention" value={stats.attention} hint="Overdue, stale, blocked or late" />
        <Stat
          label="Median days to live"
          value={stats.medianNet ?? "-"}
          hint={stats.medianGross !== null ? `Net of client delays. Gross ${stats.medianGross}` : undefined}
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
        <div className="relative ml-auto">
          <RiSearchLine size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search client or owner"
            className="h-8 w-56 pl-8 text-[13px]"
          />
        </div>
      </div>

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
                      onClick={() => setCollapsed((c) => ({ ...c, [account]: !c[account] }))}
                    >
                      <TableCell colSpan={11} className="py-1.5 text-[13px] font-medium">
                        <span className="inline-flex items-center gap-1">
                          {isCollapsed ? <RiArrowRightSLine size={14} /> : <RiArrowDownSLine size={14} />}
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
                            onClick={() => router.push(`/onboarding/${r.record_id}`)}
                          >
                            <TableCell className={cn("font-medium", multi && "pl-8")}>
                              <Link href={`/onboarding/${r.record_id}`} onClick={(e) => e.stopPropagation()}>
                                {r.name}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline" className="rounded font-normal whitespace-nowrap">
                                {stageLabel[r.stage] ?? r.stage}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {formatValue(r.fields.owner).split("@")[0]}
                            </TableCell>
                            <TableCell className="max-w-40 truncate">{formatValue(r.fields.platforms)}</TableCell>
                            <TableCell className="max-w-40 truncate">
                              {formatValue(r.fields.modules_pending)}
                            </TableCell>
                            <TableCell className="whitespace-nowrap">{formatValue(r.fields.target_go_live)}</TableCell>
                            <TableCell className="whitespace-nowrap">{formatValue(r.fields.first_live)}</TableCell>
                            <TableCell className="text-right tabular-nums whitespace-nowrap">
                              {d.days_to_live_net !== null ? (
                                <span title={`Gross ${d.days_to_live_gross} days`}>
                                  {d.days_to_live_net}
                                  {d.days_to_live_gross !== d.days_to_live_net && (
                                    <span className="text-muted-foreground"> / {d.days_to_live_gross}</span>
                                  )}
                                </span>
                              ) : (
                                ""
                              )}
                            </TableCell>
                            <TableCell>{formatValue(r.fields.billing_status)}</TableCell>
                            <TableCell className="max-w-56 truncate" title={formatValue(r.fields.next_step)}>
                              {formatValue(r.fields.next_step)}
                              {r.fields.next_step_due ? (
                                <span className="text-muted-foreground"> · {formatValue(r.fields.next_step_due)}</span>
                              ) : null}
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-1">
                                {d.flags.map((f) => (
                                  <Badge key={f} variant="outline" className={cn("rounded text-[11px]", FLAG_COLORS[f])}>
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
          <EmptyState title="No clients in this view" description="Try another view or clear the search." />
        )}
      </Card>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Days to live: net of client-blocked days / gross. Rows are flagged when the next step is overdue, nothing
        changed for 7 days, a blocker is set, the target date passed, or the pilot ends within 14 days.
      </p>
    </div>
  );
}
