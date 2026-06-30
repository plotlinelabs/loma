"use client";

import { useEffect, useState, useRef } from "react";
import { fetchCostStats, fetchStats, fetchTokenUsage } from "../../lib/api";
import type { CostStatsResponse, StatsResponse, TokenUsageResponse } from "../../lib/api";
import {
  BarChart, Bar, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  RiChat1Line,
  RiCheckboxCircleLine,
  RiErrorWarningLine,
  RiAlertLine,
  RiMoneyDollarCircleLine,
  RiBarChartBoxLine,
  RiUploadLine,
  RiDownloadLine,
  RiComputerLine,
} from "@remixicon/react";
import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatHours(minutes: number): string {
  if (minutes >= 60) {
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  return `${Math.round(minutes)}m`;
}

function SkeletonStatCard() {
  return (
    <Card className="p-2 md:p-3 flex items-start gap-3">
      <Skeleton className="w-10 h-10 rounded-lg" />
      <div>
        <Skeleton className="h-3 w-24 mb-2" />
        <Skeleton className="h-7 w-16" />
      </div>
    </Card>
  );
}

function SkeletonChartCard() {
  return (
    <Card className="p-2 md:p-3">
      <Skeleton className="h-4 w-40 mb-2" />
      <Skeleton className="h-72 w-full rounded-lg" />
    </Card>
  );
}

function SkeletonTableRow() {
  return (
    <TableRow>
      <TableCell><Skeleton className="h-5 w-14 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-40 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded" /></TableCell>
      <TableCell><Skeleton className="h-5 w-12 rounded" /></TableCell>
    </TableRow>
  );
}

export default function AnalyticsPage() {
  const [costData, setCostData] = useState<CostStatsResponse | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "token-usage">("overview");

  // Token usage state
  const [tokenData, setTokenData] = useState<TokenUsageResponse | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenTypeFilter, setTokenTypeFilter] = useState<"" | "user" | "flow">("");
  const [tokenNameSearch, setTokenNameSearch] = useState("");
  const [tokenNameFilter, setTokenNameFilter] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => { loadData(); }, [days]);

  useEffect(() => {
    if (activeTab === "token-usage") loadTokenData();
  }, [activeTab, days, tokenTypeFilter, tokenNameFilter]);

  // Debounce name search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setTokenNameFilter(tokenNameSearch), 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [tokenNameSearch]);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [costStats, conversationStats] = await Promise.all([fetchCostStats(days), fetchStats()]);
      setCostData(costStats);
      setStats(conversationStats);
    } catch (e) { console.error("Failed to load analytics data:", e); setError("Failed to load analytics data"); }
    finally { setLoading(false); }
  }

  async function loadTokenData() {
    setTokenLoading(true);
    try {
      const data = await fetchTokenUsage({
        days,
        type: tokenTypeFilter || undefined,
        name: tokenNameFilter || undefined,
      });
      setTokenData(data);
    } catch (e) { console.error("Failed to load token usage:", e); }
    finally { setTokenLoading(false); }
  }

  if (loading && activeTab === "overview") {
    return (
      <div className="space-y-2 animate-fade-in-up">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Analytics</h1>
            <p className="text-sm text-muted-foreground mt-1">Loading analytics data...</p>
          </div>
          <div className="flex gap-2">
            {[7, 30, 90].map((d) => <Skeleton key={d} className="h-8 w-10 rounded-lg" />)}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard />
        </div>
        <Skeleton className="h-32 w-full rounded-xl" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard />
        </div>
        <SkeletonChartCard /><SkeletonChartCard />
      </div>
    );
  }

  return (
    <div className="space-y-2 animate-fade-in-up">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Analytics</h1>
          <p className="text-sm text-muted-foreground mt-1">Track conversations, API usage, spending, and savings vs. human labor</p>
        </div>
        <div className="flex gap-2">
          {[7, 30, 90].map((d) => (
            <Button key={d} onClick={() => setDays(d)}
              variant={days === d ? "secondary" : "outline"}
              size="sm"
              className={cn(
                "press-scale",
                days === d && "bg-brand-100 text-brand-700"
              )}>
              {d}d
            </Button>
          ))}
        </div>
      </div>

      {/* Tab bar */}
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "overview" | "token-usage")}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="token-usage">Token Usage</TabsTrigger>
        </TabsList>
      </Tabs>

      {activeTab === "overview" && (
        <>
          {!costData ? (
            <EmptyState icon={RiAlertLine} title="Failed to load analytics data" description="Try refreshing the page" />
          ) : (
            <>
              {stats && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 md:gap-4 stagger-children">
                  <ConversationStatCard label="Total Conversations" value={stats.total_conversations}
                    icon={<RiChat1Line size={20} />}
                    iconColor="text-brand-600 bg-brand-50" />
                  <ConversationStatCard label="Resolved" value={stats.by_category?.resolved || 0}
                    icon={<RiCheckboxCircleLine size={20} />}
                    iconColor="text-green-600 bg-green-50" />
                  <ConversationStatCard label="Unresolved" value={stats.by_category?.unresolved || 0}
                    icon={<RiErrorWarningLine size={20} />}
                    iconColor="text-red-600 bg-red-50" />
                  <ConversationStatCard label="Errors" value={stats.by_status?.error || 0}
                    icon={<RiAlertLine size={20} />}
                    iconColor="text-amber-600 bg-amber-50" />
                </div>
              )}

              {costData.total_estimated_human_cost_usd > 0 && (
                <div className="bg-gradient-to-r from-emerald-50 to-teal-50 rounded-xl border border-emerald-200 p-2 md:p-3 hover-lift">
                  <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium text-emerald-700 mb-1">Total Savings</div>
                      <div className="text-3xl md:text-4xl font-bold text-emerald-800 tabular-nums">${costData.total_savings_usd.toFixed(2)}</div>
                      <div className="text-sm text-emerald-600 mt-1">{costData.savings_percentage}% saved vs. human labor</div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="text-center sm:text-center">
                        <div className="text-xs text-emerald-600 font-medium">Human Cost</div>
                        <div className="text-lg md:text-xl font-semibold text-emerald-800 tabular-nums">${costData.total_estimated_human_cost_usd.toFixed(2)}</div>
                      </div>
                      <div className="text-center sm:text-center">
                        <div className="text-xs text-emerald-600 font-medium">API Cost</div>
                        <div className="text-lg md:text-xl font-semibold text-emerald-800 tabular-nums">${costData.total_cost_usd.toFixed(2)}</div>
                      </div>
                      <div className="text-center sm:text-center">
                        <div className="text-xs text-emerald-600 font-medium">Time Saved</div>
                        <div className="text-lg md:text-xl font-semibold text-emerald-800">{formatHours(costData.total_estimated_human_minutes)}</div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 md:gap-4 stagger-children">
                <CostStatCard label="Total Spend" value={`$${costData.total_cost_usd.toFixed(2)}`}
                  icon={<RiMoneyDollarCircleLine size={20} />}
                  iconColor="text-brand-600 bg-brand-50" />
                <CostStatCard label="Avg / Conversation" value={`$${costData.avg_cost_per_conversation.toFixed(4)}`}
                  icon={<RiBarChartBoxLine size={20} />}
                  iconColor="text-blue-600 bg-blue-50" />
                <CostStatCard label="Input Tokens" value={formatNumber(costData.total_input_tokens)}
                  icon={<RiUploadLine size={20} />}
                  iconColor="text-green-600 bg-green-50" />
                <CostStatCard label="Output Tokens" value={formatNumber(costData.total_output_tokens)}
                  icon={<RiDownloadLine size={20} />}
                  iconColor="text-amber-600 bg-amber-50" />
              </div>

              <Card className="p-2 md:p-3 hover-lift">
                <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Daily Cost: Human vs API</h2>
                {costData.daily.length === 0 ? (
                  <EmptyState icon={RiBarChartBoxLine} title="No data for this period" className="py-6" />
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Number(v).toFixed(0)}`} />
                        <Tooltip formatter={(value?: number, name?: string) => [`$${(value ?? 0).toFixed(2)}`, name ?? ""]} />
                        <Legend />
                        <Bar dataKey="estimated_human_cost_usd" name="Est. Human Cost" fill="var(--chart-1)" radius={[4, 4, 0, 0]} opacity={0.7} />
                        <Bar dataKey="total_cost_usd" name="API Cost" fill="var(--chart-2)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>

              <Card className="p-2 md:p-3 hover-lift">
                <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Daily API Cost Breakdown (USD)</h2>
                {costData.daily.length === 0 ? (
                  <EmptyState icon={RiBarChartBoxLine} title="No data for this period" className="py-6" />
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Number(v).toFixed(2)}`} />
                        <Tooltip formatter={(value) => `$${Number(value).toFixed(4)}`} />
                        <Legend />
                        <Bar dataKey="agent_cost_usd" name="Agent" fill="var(--chart-2)" stackId="cost" />
                        <Bar dataKey="confidence_cost_usd" name="Confidence" fill="var(--chart-1)" stackId="cost" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>

              <Card className="p-2 md:p-3 hover-lift">
                <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Daily Token Usage</h2>
                {costData.daily.length === 0 ? (
                  <EmptyState icon={RiBarChartBoxLine} title="No data for this period" className="py-6" />
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => { const n = Number(v); return n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n); }} />
                        <Tooltip formatter={(value) => formatNumber(Number(value))} />
                        <Legend />
                        <Area type="monotone" dataKey="input_tokens" name="Input Tokens" stroke="var(--chart-3)" fill="var(--chart-3)" fillOpacity={0.15} />
                        <Area type="monotone" dataKey="output_tokens" name="Output Tokens" stroke="var(--chart-4)" fill="var(--chart-4)" fillOpacity={0.15} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>

              <Card className="p-2 md:p-3 hover-lift">
                <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Conversations per Day</h2>
                {costData.daily.length === 0 ? (
                  <EmptyState icon={RiBarChartBoxLine} title="No data for this period" className="py-6" />
                ) : (
                  <div className="h-40 md:h-48">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                        <Tooltip />
                        <Bar dataKey="conversations" name="Conversations" fill="var(--chart-5)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}

      {activeTab === "token-usage" && (
        <>
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={tokenTypeFilter}
              onValueChange={(v) => setTokenTypeFilter(v as "" | "user" | "flow")}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="All Types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">All Types</SelectItem>
                <SelectItem value="user">Users</SelectItem>
                <SelectItem value="flow">Flows</SelectItem>
              </SelectContent>
            </Select>
            <Input
              type="text"
              placeholder="Search by name..."
              value={tokenNameSearch}
              onChange={(e) => setTokenNameSearch(e.target.value)}
              className="w-64"
            />
          </div>

          {/* Summary cards */}
          {tokenData && !tokenLoading && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 md:gap-4 stagger-children">
              <CostStatCard label="Total Tokens" value={formatNumber(tokenData.totals.total_tokens)}
                icon={<RiComputerLine size={20} />}
                iconColor="text-brand-600 bg-brand-50" />
              <CostStatCard label="Input Tokens" value={formatNumber(tokenData.totals.input_tokens)}
                icon={<RiUploadLine size={20} />}
                iconColor="text-green-600 bg-green-50" />
              <CostStatCard label="Output Tokens" value={formatNumber(tokenData.totals.output_tokens)}
                icon={<RiDownloadLine size={20} />}
                iconColor="text-amber-600 bg-amber-50" />
            </div>
          )}

          {/* Table */}
          <Card className="overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="border-b border-border bg-muted/50">
                  <TableHead className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Type</TableHead>
                  <TableHead className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Name</TableHead>
                  <TableHead className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wider">Input Tokens</TableHead>
                  <TableHead className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wider">Output Tokens</TableHead>
                  <TableHead className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wider">Total Tokens</TableHead>
                  <TableHead className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wider">Convos</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokenLoading ? (
                  <>
                    <SkeletonTableRow /><SkeletonTableRow /><SkeletonTableRow />
                    <SkeletonTableRow /><SkeletonTableRow />
                  </>
                ) : tokenData && tokenData.rows.length > 0 ? (
                  tokenData.rows.map((row, i) => (
                    <TableRow key={`${row.type}-${row.name}-${i}`}>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={cn(
                            "rounded-full",
                            row.type === "user"
                              ? "bg-blue-50 text-blue-700"
                              : "bg-purple-50 text-purple-700"
                          )}
                        >
                          {row.type === "user" ? "User" : "Flow"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-foreground font-medium truncate max-w-xs">{row.name}</TableCell>
                      <TableCell className="text-right text-muted-foreground tabular-nums">{formatNumber(row.input_tokens)}</TableCell>
                      <TableCell className="text-right text-muted-foreground tabular-nums">{formatNumber(row.output_tokens)}</TableCell>
                      <TableCell className="text-right text-foreground font-semibold tabular-nums">{formatNumber(row.total_tokens)}</TableCell>
                      <TableCell className="text-right text-muted-foreground tabular-nums">{row.conversations}</TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="py-6">
                      <EmptyState icon={RiBarChartBoxLine} title="No data for this period" className="py-6" />
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

function ConversationStatCard({ label, value, icon, iconColor }: { label: string; value: number; icon: React.ReactNode; iconColor: string }) {
  return (
    <Card className="p-2 md:p-3 flex items-start gap-2 md:gap-4 hover-lift">
      <div className={cn("w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0", iconColor)}>{icon}</div>
      <div>
        <div className="text-xs text-muted-foreground font-medium">{label}</div>
        <div className="text-lg md:text-xl font-semibold text-foreground mt-0.5">{value}</div>
      </div>
    </Card>
  );
}

function CostStatCard({ label, value, icon, iconColor }: { label: string; value: string; icon: React.ReactNode; iconColor: string }) {
  return (
    <Card className="p-2 md:p-3 flex items-start gap-2 md:gap-4 hover-lift">
      <div className={cn("w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0", iconColor)}>{icon}</div>
      <div>
        <div className="text-xs text-muted-foreground font-medium">{label}</div>
        <div className="text-lg md:text-xl font-semibold text-foreground mt-0.5 tabular-nums">{value}</div>
      </div>
    </Card>
  );
}
