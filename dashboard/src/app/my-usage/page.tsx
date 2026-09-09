"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  RiChat1Line,
  RiDownloadLine,
  RiUploadLine,
} from "@remixicon/react";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ClientTimestamp from "@/components/ClientTimestamp";
import { formatUsd } from "@/components/CostChip";
import { fetchMyUsage, type MyUsageResponse } from "@/lib/api";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

type Range = "today" | "7" | "30" | "90";

/** "My usage" — the signed-in user's own AI spend. Org-wide numbers live on
 * Analytics (and Claude-subscription limits on /usage); this page is
 * deliberately me-only so it needs no special role. */
export default function MyUsagePage() {
  const [range, setRange] = useState<Range>("today");
  const [data, setData] = useState<MyUsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Stale data stays visible while a new range loads — no skeleton flash.
  useEffect(() => {
    let cancelled = false;
    // Daily buckets (and "today") follow the browser's timezone.
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    let opts: Parameters<typeof fetchMyUsage>[0];
    if (range === "today") {
      const midnight = new Date();
      midnight.setHours(0, 0, 0, 0);
      opts = { since: midnight.toISOString(), tz };
    } else {
      opts = { days: Number(range), tz };
    }
    fetchMyUsage(opts)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [range]);

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

      {!data && !error && (
        <div className="mt-6">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="mt-2 h-11 w-44" />
          <Skeleton className="mt-4 h-3 w-72" />
        </div>
      )}

      {data && (
        <>
          {/* Impact comes from scale + whitespace, not a surfaced card */}
          <div className="mt-6">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">Spent</div>
            <div className="mt-1 font-heading font-semibold tabular-nums leading-none text-foreground text-[40px] md:text-[46px]">
              {formatUsd(data.totals.total_cost_usd)}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1.5 text-[13px] text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <RiChat1Line size={14} />
                {data.totals.conversations} chats
              </span>
              <span className="inline-flex items-center gap-1.5">
                <RiUploadLine size={14} />
                {formatTokens(data.totals.input_tokens)} in
                {data.totals.cache_read_tokens + data.totals.cache_creation_tokens > 0 && (
                  <span className="text-muted-foreground/70">
                    {" "}+{formatTokens(data.totals.cache_read_tokens + data.totals.cache_creation_tokens)} cached
                  </span>
                )}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <RiDownloadLine size={14} />
                {formatTokens(data.totals.output_tokens)} out
              </span>
            </div>
          </div>

          {/* A one-bar chart says nothing — Today skips it */}
          {data.daily.length > 1 && (
            <div className="mt-6 border-t border-border/60 pt-4">
              <div className="mb-2 text-xs uppercase tracking-wider text-muted-foreground">Daily spend</div>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.daily} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
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
            </div>
          )}

          <div className="mt-6 mb-4 border-t border-border/60 pt-4">
            <div className="mb-1 text-xs uppercase tracking-wider text-muted-foreground">Costliest chats</div>
            {data.top_chats.length === 0 ? (
              <p className="text-[13px] text-muted-foreground">Nothing yet in this window.</p>
            ) : (
              <ul className="divide-y divide-border/60">
                {data.top_chats.map((chat) => (
                  <li key={chat.conversation_id}>
                    <Link
                      href={`/chat?continue=${chat.conversation_id}`}
                      className="flex items-center gap-3 rounded-lg px-1 py-2.5 hover:bg-muted/50"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px]">{chat.title || chat.prompt || "Untitled"}</div>
                        <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                          {chat.started_at && <ClientTimestamp iso={chat.started_at} variant="short" placeholder="—" />}
                          <span>{formatTokens(chat.input_tokens)} in · {formatTokens(chat.output_tokens)} out</span>
                        </div>
                      </div>
                      <span className="shrink-0 text-[13px] font-medium tabular-nums">
                        {formatUsd(chat.total_cost_usd)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
