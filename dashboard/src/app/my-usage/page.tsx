"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  RiChat1Line,
  RiDownloadLine,
  RiMoneyDollarCircleLine,
  RiUploadLine,
} from "@remixicon/react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ClientTimestamp from "@/components/ClientTimestamp";
import { formatUsd } from "@/components/CostChip";
import { fetchMyUsage, type MyUsageDay, type MyUsageResponse } from "@/lib/api";

/** Fields added by the usage-ledger backend; optional so an older server
 * still renders. */
type UsageData = MyUsageResponse & {
  includes_approximate?: boolean;
  top_chats: (MyUsageResponse["top_chats"][number] & { last_used_at?: string | null })[];
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function StatCard({ icon, label, value, sub }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card className="p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-heading font-semibold tabular-nums">
        {value}
        {sub && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{sub}</span>}
      </div>
    </Card>
  );
}

type Range = "today" | "7" | "30" | "90";

/** Local-midnight start of the window: "today" is since midnight, "7" is
 * today plus the 6 previous calendar days, and so on — so the window lines
 * up exactly with the daily bars. */
function windowStart(range: Range, now: Date = new Date()): Date {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  if (range !== "today") start.setDate(start.getDate() - (Number(range) - 1));
  return start;
}

function localDateKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** One bar per calendar day in the window — days without spend show as 0
 * instead of silently disappearing from the chart. */
function fillDays(daily: MyUsageDay[], start: Date, end: Date = new Date()): MyUsageDay[] {
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const out: MyUsageDay[] = [];
  const cursor = new Date(start);
  const last = localDateKey(end);
  for (let i = 0; i < 400; i++) {
    const key = localDateKey(cursor);
    out.push(byDate.get(key) ?? {
      date: key, total_cost_usd: 0, input_tokens: 0, output_tokens: 0, conversations: 0,
    });
    if (key === last) break;
    cursor.setDate(cursor.getDate() + 1);
  }
  return out;
}

/** "My usage" — the signed-in user's own AI spend. Org-wide numbers live on
 * Analytics (and Claude-subscription limits on /usage); this page is
 * deliberately me-only so it needs no special role. */
export default function MyUsagePage() {
  const [range, setRange] = useState<Range>("today");
  const [data, setData] = useState<{ usage: UsageData; start: Date } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped when the tab regains focus and at local midnight, so a page left
  // open (the mobile PWA stays alive for days) never shows a stale window.
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const bump = () => { if (!document.hidden) setRefreshKey((k) => k + 1); };
    document.addEventListener("visibilitychange", bump);
    window.addEventListener("focus", bump);
    return () => {
      document.removeEventListener("visibilitychange", bump);
      window.removeEventListener("focus", bump);
    };
  }, []);

  useEffect(() => {
    const next = new Date();
    next.setHours(24, 0, 1, 0);
    const timer = setTimeout(() => setRefreshKey((k) => k + 1), next.getTime() - Date.now());
    return () => clearTimeout(timer);
  }, [refreshKey]);

  // Stale data stays visible while a new range loads — no skeleton flash.
  useEffect(() => {
    let cancelled = false;
    // Daily buckets follow the browser's timezone; the window start is
    // recomputed on every fetch.
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const start = windowStart(range);
    fetchMyUsage({ since: start.toISOString(), tz })
      .then((d) => {
        if (cancelled) return;
        setError(null);
        setData({ usage: d as UsageData, start });
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [range, refreshKey]);

  const usage = data?.usage;
  const days = data && range !== "today" ? fillDays(data.usage.daily, data.start) : [];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="pwa-header-offset flex items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">My usage</h1>
          <p className="text-[13px] text-muted-foreground">What your chats and tasks have spent</p>
        </div>
        <Tabs value={range} onValueChange={(v) => setRange(v as Range)}>
          <TabsList>
            <TabsTrigger value="today">Today</TabsTrigger>
            <TabsTrigger value="7">7d</TabsTrigger>
            <TabsTrigger value="30">30d</TabsTrigger>
            <TabsTrigger value="90">90d</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {error && <p className="mt-4 text-[13px] text-destructive">{error}</p>}

      {!usage && !error && (
        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[74px] rounded-xl" />)}
        </div>
      )}

      {usage && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
            <StatCard
              icon={<RiMoneyDollarCircleLine size={14} />}
              label="Spent"
              value={formatUsd(usage.totals.total_cost_usd)}
            />
            <StatCard
              icon={<RiChat1Line size={14} />}
              label="Chats used"
              value={String(usage.totals.conversations)}
            />
            <StatCard
              icon={<RiUploadLine size={14} />}
              label="Tokens in"
              value={formatTokens(usage.totals.input_tokens)}
              // Cache reads/writes are billed too — the $ figure doesn't add
              // up from fresh tokens alone on long agentic runs.
              sub={
                usage.totals.cache_read_tokens + usage.totals.cache_creation_tokens > 0
                  ? `+${formatTokens(usage.totals.cache_read_tokens + usage.totals.cache_creation_tokens)} cached`
                  : undefined
              }
            />
            <StatCard
              icon={<RiDownloadLine size={14} />}
              label="Tokens out"
              value={formatTokens(usage.totals.output_tokens)}
            />
          </div>

          {usage.includes_approximate && (
            <p className="mt-2 text-xs text-muted-foreground">
              Includes older chats recorded before per-day tracking; their spend is
              counted on the day each chat started.
            </p>
          )}

          {/* A one-bar chart says nothing — Today skips it */}
          {days.length > 1 && (
            <Card className="mt-3 p-3">
              <div className="mb-2 text-xs text-muted-foreground">Daily spend</div>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={days} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10 }}
                      tickFormatter={(d: string) => d.slice(5)}
                      stroke="var(--muted-foreground)"
                    />
                    <YAxis
                      tick={{ fontSize: 10 }}
                      tickFormatter={(v: number) => `$${v}`}
                      stroke="var(--muted-foreground)"
                    />
                    <Tooltip
                      formatter={(value) => [formatUsd(Number(value)), "Spend"]}
                      contentStyle={{ fontSize: 12, borderRadius: 8 }}
                    />
                    <Bar dataKey="total_cost_usd" fill="var(--color-brand-500, #8a7350)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}

          <Card className="mt-3 mb-4 p-0 overflow-hidden">
            <div className="px-3 pt-3 pb-2 text-xs text-muted-foreground">Costliest chats in this window</div>
            {usage.top_chats.length === 0 ? (
              <p className="px-3 pb-3 text-[13px] text-muted-foreground">Nothing yet in this window.</p>
            ) : (
              <ul className="divide-y divide-border">
                {usage.top_chats.map((chat) => {
                  const when = chat.last_used_at || chat.started_at;
                  return (
                    <li key={chat.conversation_id}>
                      <Link
                        href={`/chat?continue=${chat.conversation_id}`}
                        className="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/50"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13px]">{chat.title || chat.prompt || "Untitled"}</div>
                          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                            {when && <ClientTimestamp iso={when} variant="short" placeholder="—" />}
                            <span>{formatTokens(chat.input_tokens)} in · {formatTokens(chat.output_tokens)} out</span>
                          </div>
                        </div>
                        <span className="shrink-0 text-[13px] font-medium tabular-nums">
                          {formatUsd(chat.total_cost_usd)}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
