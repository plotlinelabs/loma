"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis,
} from "recharts";
import { RiArrowDownSLine, RiArrowRightSLine } from "@remixicon/react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ClientTimestamp from "@/components/ClientTimestamp";
import { formatUsd } from "@/components/CostChip";
import {
  fetchAgentModels, fetchMyChatRuns, fetchMyUsage,
  type MyUsageChat, type MyUsageDay, type MyUsageResponse, type MyUsageRun,
  type MyUsageSort, type MyUsageSums,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
const UNPRICED_TEXT = "text-amber-700 dark:text-amber-500";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

const totalTokens = (s: Pick<MyUsageSums, "input_tokens" | "output_tokens" | "cache_read_tokens" | "cache_creation_tokens">) =>
  s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_creation_tokens;

/** Every run in it had no price (e.g. Codex on a ChatGPT plan). */
const fullyUnpriced = (s: MyUsageSums) => s.runs > 0 && s.unpriced_runs === s.runs;

type Range = "today" | "7" | "30" | "90";

const RANGE_LABEL: Record<Range, string> = {
  today: "Today", "7": "Last 7 days", "30": "Last 30 days", "90": "Last 90 days",
};

const SORT_LABEL: Record<MyUsageSort, string> = {
  cost_desc: "Cost, high to low",
  cost_asc: "Cost, low to high",
  recent: "Most recent",
};

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

const EMPTY_SUMS: MyUsageSums = {
  total_cost_usd: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0,
  cache_creation_tokens: 0, runs: 0, unpriced_runs: 0, unpriced_tokens: 0,
};

/** One bar per calendar day in the window — days without spend show as 0
 * instead of silently disappearing from the chart. */
function fillDays(daily: MyUsageDay[], start: Date, end: Date = new Date()): MyUsageDay[] {
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const out: MyUsageDay[] = [];
  const cursor = new Date(start);
  const last = localDateKey(end);
  for (let i = 0; i < 400; i++) {
    const key = localDateKey(cursor);
    out.push(byDate.get(key) ?? { date: key, ...EMPTY_SUMS, conversations: 0 });
    if (key === last) break;
    cursor.setDate(cursor.getDate() + 1);
  }
  return out;
}

function shortDay(key: string, todayKey: string): string {
  if (key === todayKey) return "Today";
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "claude-opus-5-5" -> "Claude Opus 5.5", "gpt-5.6-sol" -> "GPT-5.6 Sol".
 * Claude and Codex catalog labels are just the raw id, so prettify those. */
function prettyModelId(raw: string): string {
  const id = raw.split("/").pop() || raw;
  const parts: string[] = [];
  for (const p of id.split("-")) {
    const prev = parts[parts.length - 1];
    // Version digits split by dashes ("5-5"); skip date stamps ("20250514").
    if (prev && /^\d{1,2}$/.test(p) && /^\d{1,2}(\.\d{1,2})?$/.test(prev)) {
      parts[parts.length - 1] = `${prev}.${p}`;
    } else {
      parts.push(p);
    }
  }
  const words = parts.map((w) => (w.toLowerCase() === "gpt" ? "GPT" : w.charAt(0).toUpperCase() + w.slice(1)));
  return words.join(" ").replace(/^GPT (\d)/, "GPT-$1");
}

/** provider/model id -> short display name. Uses the catalog's display name
 * when it has a real one (OpenCode), otherwise prettifies the id. */
function useModelLabels() {
  const [catalog, setCatalog] = useState<Record<string, string>>({});
  useEffect(() => {
    fetchAgentModels()
      .then((res) => {
        const map: Record<string, string> = {};
        for (const m of res.models || []) {
          const name = m.label.split("·").pop()?.trim();
          if (name && name !== m.model_id) map[m.id] = name;
        }
        setCatalog(map);
      })
      .catch(() => {});
  }, []);
  return useCallback((id: string) => {
    if (!id || id === "unknown") return "Unknown model";
    return catalog[id] || prettyModelId(id);
  }, [catalog]);
}

function Cost({ sums, className }: { sums: MyUsageSums; className?: string }) {
  if (fullyUnpriced(sums)) {
    return <span className={cn(UNPRICED_TEXT, className)}>No price</span>;
  }
  return <span className={cn("tabular-nums", className)}>{formatUsd(sums.total_cost_usd)}</span>;
}

function ModelBadges({ models, label }: { models: string[]; label: (id: string) => string }) {
  if (models.length === 0) return null;
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={models.map(label).join(", ")}>
      <Badge variant="secondary" className="max-w-[160px] truncate">{label(models[0])}</Badge>
      {models.length > 1 && (
        <span className="shrink-0 text-xs text-muted-foreground">+{models.length - 1}</span>
      )}
    </span>
  );
}

/** Runs of one chat inside the window, loaded when the row is expanded. */
function ChatRuns({ chat, since, label }: {
  chat: MyUsageChat;
  since: string;
  label: (id: string) => string;
}) {
  const [runs, setRuns] = useState<MyUsageRun[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchMyChatRuns(chat.conversation_id, since)
      .then((r) => { if (!cancelled) setRuns(r.runs); })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [chat.conversation_id, since]);

  if (error) return <p className="py-2 text-xs text-destructive">Couldn&apos;t load runs</p>;
  if (!runs) return <Skeleton className="my-2 h-12 rounded-md" />;
  return (
    <ul className="space-y-1 py-1.5 text-xs text-muted-foreground">
      {runs.map((run, i) => (
        <li key={`${run.at}-${i}`} className="flex items-center gap-3">
          <span className="w-28 shrink-0 tabular-nums">
            <ClientTimestamp iso={run.at} variant="short" placeholder="—" />
          </span>
          <span className="min-w-0 flex-1 truncate">
            {label(run.model)}
            {run.approx && " · before per-run tracking"}
          </span>
          <span className="w-14 shrink-0 text-right tabular-nums">{formatTokens(totalTokens(run))}</span>
          <span className={cn("w-16 shrink-0 text-right tabular-nums", !run.cost_known && UNPRICED_TEXT)}>
            {run.cost_known ? formatUsd(run.cost_usd) : "No price"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function chatTitle(chat: MyUsageChat) {
  return chat.title || chat.prompt || "Untitled";
}

/** "My usage" — the signed-in user's own AI spend. Org-wide numbers live on
 * Analytics (and Claude-subscription limits on /usage); this page is
 * deliberately me-only so it needs no special role. */
export default function MyUsagePage() {
  const [range, setRange] = useState<Range>("7");
  const [sort, setSort] = useState<MyUsageSort>("cost_desc");
  const [data, setData] = useState<{ usage: MyUsageResponse; start: Date } | null>(null);
  const [chats, setChats] = useState<MyUsageChat[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped when the tab regains focus and at local midnight, so a page left
  // open (the mobile PWA stays alive for days) never shows a stale window.
  const [refreshKey, setRefreshKey] = useState(0);
  const label = useModelLabels();
  // Resolved after mount: the server renders in its own timezone, so reading
  // it during render would cause a hydration mismatch.
  const [tz, setTz] = useState("");
  useEffect(() => { setTz(Intl.DateTimeFormat().resolvedOptions().timeZone); }, []);

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
    if (!tz) return;
    let cancelled = false;
    // The window start is recomputed on every fetch.
    const start = windowStart(range);
    fetchMyUsage({ since: start.toISOString(), tz, sort, limit: PAGE_SIZE })
      .then((d) => {
        if (cancelled) return;
        setError(null);
        setData({ usage: d, start });
        setChats(d.chats ?? d.top_chats ?? []);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [range, sort, tz, refreshKey]);

  const loadMore = async () => {
    if (!data) return;
    setLoadingMore(true);
    try {
      const next = await fetchMyUsage({
        since: data.start.toISOString(), tz, sort, limit: PAGE_SIZE, offset: chats.length,
      });
      setChats((prev) => {
        const seen = new Set(prev.map((c) => c.conversation_id));
        return [...prev, ...(next.chats ?? []).filter((c) => !seen.has(c.conversation_id))];
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoadingMore(false);
    }
  };

  const usage = data?.usage;
  const since = data?.start.toISOString() ?? "";
  const todayKey = localDateKey(new Date());
  const days = data && range !== "today" ? fillDays(data.usage.daily, data.start) : [];
  const peakDay = days.reduce((best, d, i) => (d.total_cost_usd > days[best].total_cost_usd ? i : best), 0);
  const models = usage?.by_model ?? [];
  const maxModelCost = Math.max(...models.map((m) => m.total_cost_usd), 0.000001);
  const chatsTotal = usage?.chats_total ?? chats.length;
  const hasUnpriced = (usage?.totals.unpriced_runs ?? 0) > 0;
  const toggle = (id: string) => setExpanded((cur) => (cur === id ? null : id));

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="pwa-header-offset flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">My usage</h1>
          <p className="text-[13px] text-muted-foreground">
            Counted on the day it was used{tz && ` · ${tz}`}
          </p>
        </div>
        <Tabs value={range} onValueChange={(v) => { setRange(v as Range); setExpanded(null); }}>
          <TabsList className="w-full md:w-auto">
            <TabsTrigger value="today">Today</TabsTrigger>
            <TabsTrigger value="7">7d</TabsTrigger>
            <TabsTrigger value="30">30d</TabsTrigger>
            <TabsTrigger value="90">90d</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {error && <p className="mt-4 text-[13px] text-destructive">{error}</p>}

      {!usage && !error && (
        <div className="mt-6 grid gap-6 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
        </div>
      )}

      {usage && (
        <>
          <section className="mt-6 grid gap-6 border-b border-border pb-6 md:grid-cols-[1fr_1.2fr_1.4fr] md:gap-10">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Spent · {RANGE_LABEL[range]}
              </div>
              <div className="mt-1 text-4xl md:text-5xl font-heading font-semibold tabular-nums">
                {formatUsd(usage.totals.total_cost_usd)}
              </div>
              <div className="mt-2 text-[13px] text-muted-foreground">
                {usage.totals.conversations} chats · {usage.totals.runs} runs · {formatTokens(totalTokens(usage.totals))} tokens
              </div>
              {hasUnpriced && (
                <div className={cn("mt-1 text-xs", UNPRICED_TEXT)}>
                  + {usage.totals.unpriced_runs} runs ({formatTokens(usage.totals.unpriced_tokens)} tokens) with no price data
                </div>
              )}
            </div>

            {models.length > 0 && (
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">By model</div>
                <ul className="mt-2 space-y-1.5 text-[13px]">
                  {models.slice(0, 6).map((m) => (
                    <li key={m.model} className="flex items-center gap-3">
                      <span className={cn("w-32 shrink-0 truncate", fullyUnpriced(m) && UNPRICED_TEXT)} title={m.model}>
                        {label(m.model)}
                      </span>
                      {fullyUnpriced(m) ? (
                        <span className={cn("flex-1 text-xs", UNPRICED_TEXT)}>
                          {formatTokens(totalTokens(m))} tokens
                        </span>
                      ) : (
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                          <span
                            className="block h-full rounded-full bg-primary"
                            style={{ width: `${Math.max(2, (m.total_cost_usd / maxModelCost) * 100)}%` }}
                          />
                        </span>
                      )}
                      <Cost sums={m} className="w-16 shrink-0 text-right" />
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* A one-bar chart says nothing — Today skips it */}
            {days.length > 1 && (
              <div className="min-w-0">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  Daily spend · by day used
                </div>
                <div className="mt-2 h-32">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={days} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10 }}
                        tickFormatter={(d: string) => shortDay(d, todayKey)}
                        stroke="var(--muted-foreground)"
                        tickLine={false}
                        axisLine={false}
                        interval="preserveStartEnd"
                      />
                      <Tooltip
                        cursor={{ fill: "var(--muted)" }}
                        formatter={(value) => [formatUsd(Number(value)), "Spend"]}
                        labelFormatter={(d) => shortDay(String(d), todayKey)}
                        contentStyle={{ fontSize: 12, borderRadius: 8 }}
                      />
                      <Bar dataKey="total_cost_usd" radius={[3, 3, 0, 0]}>
                        {days.map((d, i) => (
                          <Cell
                            key={d.date}
                            fill="var(--primary)"
                            fillOpacity={i === peakDay && d.total_cost_usd > 0 ? 1 : 0.45}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </section>

          {usage.includes_approximate && (
            <p className="mt-3 text-xs text-muted-foreground">
              Includes older chats recorded before per-day tracking; their spend is
              counted on the day each chat started.
            </p>
          )}

          <section className="mt-6 mb-6">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-baseline gap-2">
                <h2 className="text-[15px] font-semibold">Chats</h2>
                <span className="text-xs text-muted-foreground">{chatsTotal} in this period</span>
              </div>
              <Select value={sort} onValueChange={(v) => { setSort(v as MyUsageSort); setExpanded(null); }}>
                <SelectTrigger size="sm" className="w-auto" aria-label="Sort chats">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  {(Object.keys(SORT_LABEL) as MyUsageSort[]).map((k) => (
                    <SelectItem key={k} value={k}>{SORT_LABEL[k]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {chats.length === 0 ? (
              <p className="mt-4 text-[13px] text-muted-foreground">Nothing yet in this window.</p>
            ) : (
              <>
                {/* Desktop: table */}
                <table className="mt-3 hidden w-full table-fixed text-[13px] md:table">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                      <th className="w-8 py-2 font-normal" />
                      <th className="py-2 font-normal">Chat</th>
                      <th className="w-48 py-2 font-normal">Model</th>
                      <th className="w-14 py-2 text-right font-normal">Runs</th>
                      <th className="w-20 py-2 text-right font-normal">Tokens</th>
                      <th className="w-36 py-2 pl-6 font-normal">Last used</th>
                      <th className="w-28 py-2 text-right font-normal">Cost in period</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chats.map((chat) => {
                      const open = expanded === chat.conversation_id;
                      return (
                        <Fragment key={chat.conversation_id}>
                          <tr
                            className={cn("cursor-pointer align-top hover:bg-muted/50", open && "bg-muted/40")}
                            onClick={() => toggle(chat.conversation_id)}
                          >
                            <td className="py-2.5 pl-1 text-muted-foreground">
                              <button
                                type="button"
                                aria-label={open ? "Hide runs" : "Show runs"}
                                aria-expanded={open}
                                onClick={(e) => { e.stopPropagation(); toggle(chat.conversation_id); }}
                              >
                                {open ? <RiArrowDownSLine size={16} /> : <RiArrowRightSLine size={16} />}
                              </button>
                            </td>
                            <td className="py-2.5 pr-4">
                              <Link
                                href={`/chat?continue=${chat.conversation_id}`}
                                className="block truncate hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {chatTitle(chat)}
                              </Link>
                              {chat.started_at && (
                                <div className="mt-0.5 text-xs text-muted-foreground">
                                  Started <ClientTimestamp iso={chat.started_at} variant="short" placeholder="—" />
                                </div>
                              )}
                            </td>
                            <td className="py-2.5"><ModelBadges models={chat.models} label={label} /></td>
                            <td className="py-2.5 text-right tabular-nums">{chat.runs}</td>
                            <td className="py-2.5 text-right tabular-nums">{formatTokens(totalTokens(chat))}</td>
                            <td className="py-2.5 pl-6 text-muted-foreground">
                              {chat.last_used_at && <ClientTimestamp iso={chat.last_used_at} variant="short" placeholder="—" />}
                            </td>
                            <td className="py-2.5 text-right font-medium"><Cost sums={chat} /></td>
                          </tr>
                          {open && (
                            <tr className="bg-muted/40">
                              <td />
                              <td colSpan={6} className="pb-2 pr-1">
                                <div className="border-l-2 border-primary/40 pl-3">
                                  <ChatRuns chat={chat} since={since} label={label} />
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>

                {/* Phones: stacked list, same data */}
                <ul className="mt-2 md:hidden">
                  {chats.map((chat) => {
                    const open = expanded === chat.conversation_id;
                    return (
                      <li key={chat.conversation_id} className="py-3">
                        <button
                          type="button"
                          className="flex w-full items-start gap-3 text-left"
                          aria-expanded={open}
                          onClick={() => toggle(chat.conversation_id)}
                        >
                          <span className="min-w-0 flex-1">
                            <span className="line-clamp-2 text-[14px]">{chatTitle(chat)}</span>
                            <span className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                              <ModelBadges models={chat.models} label={label} />
                              <span className="shrink-0">
                                {chat.runs} runs
                                {chat.last_used_at && (
                                  <> · <ClientTimestamp iso={chat.last_used_at} variant="short" placeholder="—" /></>
                                )}
                              </span>
                            </span>
                          </span>
                          <Cost sums={chat} className="shrink-0 text-[14px] font-semibold" />
                        </button>
                        {open && (
                          <div className="mt-2 border-l-2 border-primary/40 pl-3">
                            <ChatRuns chat={chat} since={since} label={label} />
                            <Link
                              href={`/chat?continue=${chat.conversation_id}`}
                              className="text-xs text-primary hover:underline"
                            >
                              Open chat
                            </Link>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>

                {hasUnpriced && (
                  <p className={cn("mt-2 text-xs", UNPRICED_TEXT)}>
                    &ldquo;No price&rdquo; means the model reports tokens but no cost (for example Codex on a ChatGPT plan).
                  </p>
                )}

                {chats.length < chatsTotal && (
                  <Button variant="ghost" size="sm" className="mt-2" onClick={loadMore} disabled={loadingMore}>
                    {loadingMore ? "Loading…" : `Show ${Math.min(PAGE_SIZE, chatsTotal - chats.length)} more`}
                  </Button>
                )}
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
}
