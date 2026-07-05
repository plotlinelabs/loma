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
import { fetchMyUsage, type MyUsageResponse } from "@/lib/api";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <Card className="p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-heading font-semibold tabular-nums">{value}</div>
    </Card>
  );
}

/** "My usage" — the signed-in user's own AI spend. Org-wide numbers live on
 * Analytics (and Claude-subscription limits on /usage); this page is
 * deliberately me-only so it needs no special role. */
export default function MyUsagePage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<MyUsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Stale data stays visible while a new range loads — no skeleton flash.
  useEffect(() => {
    let cancelled = false;
    fetchMyUsage(days)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [days]);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="pwa-header-offset flex items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">My usage</h1>
          <p className="text-[13px] text-muted-foreground">What your chats and tasks have spent</p>
        </div>
        <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <TabsList>
            <TabsTrigger value="7">7d</TabsTrigger>
            <TabsTrigger value="30">30d</TabsTrigger>
            <TabsTrigger value="90">90d</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {error && <p className="mt-4 text-[13px] text-destructive">{error}</p>}

      {!data && !error && (
        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[74px] rounded-xl" />)}
        </div>
      )}

      {data && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
            <StatCard
              icon={<RiMoneyDollarCircleLine size={14} />}
              label="Spent"
              value={formatUsd(data.totals.total_cost_usd)}
            />
            <StatCard
              icon={<RiChat1Line size={14} />}
              label="Chats"
              value={String(data.totals.conversations)}
            />
            <StatCard
              icon={<RiUploadLine size={14} />}
              label="Tokens in"
              value={formatTokens(data.totals.input_tokens)}
            />
            <StatCard
              icon={<RiDownloadLine size={14} />}
              label="Tokens out"
              value={formatTokens(data.totals.output_tokens)}
            />
          </div>

          {data.daily.length > 0 && (
            <Card className="mt-3 p-3">
              <div className="mb-2 text-xs text-muted-foreground">Daily spend</div>
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
            </Card>
          )}

          <Card className="mt-3 mb-4 p-0 overflow-hidden">
            <div className="px-3 pt-3 pb-2 text-xs text-muted-foreground">Costliest chats</div>
            {data.top_chats.length === 0 ? (
              <p className="px-3 pb-3 text-[13px] text-muted-foreground">Nothing yet in this window.</p>
            ) : (
              <ul className="divide-y divide-border">
                {data.top_chats.map((chat) => (
                  <li key={chat.conversation_id}>
                    <Link
                      href={`/chat?continue=${chat.conversation_id}`}
                      className="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/50"
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
          </Card>
        </>
      )}
    </div>
  );
}
