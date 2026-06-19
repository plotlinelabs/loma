"use client";

import { useEffect, useState, useRef } from "react";
import { fetchCostStats, fetchStats, fetchTokenUsage } from "../../lib/api";
import type { CostStatsResponse, StatsResponse, TokenUsageResponse } from "../../lib/api";
import {
  BarChart, Bar, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";

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
    <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 flex items-start gap-4">
      <div className="skeleton w-10 h-10 rounded-lg" />
      <div>
        <div className="skeleton h-3 w-24 mb-2" />
        <div className="skeleton h-7 w-16" />
      </div>
    </div>
  );
}

function SkeletonChartCard() {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5">
      <div className="skeleton h-4 w-40 mb-4" />
      <div className="skeleton h-72 w-full rounded-lg" />
    </div>
  );
}

function SkeletonTableRow() {
  return (
    <tr>
      <td className="px-4 py-3"><div className="skeleton h-5 w-14 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-40 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-12 rounded" /></td>
    </tr>
  );
}

export default function AnalyticsPage() {
  const [costData, setCostData] = useState<CostStatsResponse | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
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
    try {
      const [costStats, conversationStats] = await Promise.all([fetchCostStats(days), fetchStats()]);
      setCostData(costStats);
      setStats(conversationStats);
    } catch (e) { console.error("Failed to load analytics data:", e); }
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
      <div className="space-y-4 md:space-y-6 animate-fade-in-up">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Analytics</h1>
            <p className="text-sm text-gray-500 mt-1">Loading analytics data...</p>
          </div>
          <div className="flex gap-2">
            {[7, 30, 90].map((d) => <div key={d} className="skeleton h-8 w-10 rounded-lg" />)}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard />
        </div>
        <div className="skeleton h-32 w-full rounded-xl" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard /><SkeletonStatCard />
        </div>
        <SkeletonChartCard /><SkeletonChartCard />
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Analytics</h1>
          <p className="text-sm text-gray-500 mt-1">Track conversations, API usage, spending, and savings vs. human labor</p>
        </div>
        <div className="flex gap-2">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={`px-3 py-1.5 text-sm rounded-lg font-medium transition-colors press-scale ${
                days === d ? "bg-brand-100 text-brand-700" : "bg-surface border border-gray-200 text-gray-600 hover:bg-gray-50"
              }`}>
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
        <button onClick={() => setActiveTab("overview")}
          className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
            activeTab === "overview" ? "bg-white shadow-sm text-gray-900" : "text-gray-500 hover:text-gray-700"
          }`}>Overview</button>
        <button onClick={() => setActiveTab("token-usage")}
          className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
            activeTab === "token-usage" ? "bg-white shadow-sm text-gray-900" : "text-gray-500 hover:text-gray-700"
          }`}>Token Usage</button>
      </div>

      {activeTab === "overview" && (
        <>
          {!costData ? (
            <div className="text-gray-400 text-center py-20">Failed to load analytics data.</div>
          ) : (
            <>
              {stats && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 stagger-children">
                  <ConversationStatCard label="Total Conversations" value={stats.total_conversations}
                    icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 0 1-.825-.242m9.345-8.334a2.126 2.126 0 0 0-.476-.095 48.64 48.64 0 0 0-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0 0 11.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" /></svg>}
                    iconColor="text-brand-600 bg-brand-50" />
                  <ConversationStatCard label="Resolved" value={stats.by_category?.resolved || 0}
                    icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>}
                    iconColor="text-green-600 bg-green-50" />
                  <ConversationStatCard label="Unresolved" value={stats.by_category?.unresolved || 0}
                    icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" /></svg>}
                    iconColor="text-red-600 bg-red-50" />
                  <ConversationStatCard label="Errors" value={stats.by_status?.error || 0}
                    icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" /></svg>}
                    iconColor="text-amber-600 bg-amber-50" />
                </div>
              )}

              {costData.total_estimated_human_cost_usd > 0 && (
                <div className="bg-gradient-to-r from-emerald-50 to-teal-50 rounded-xl border border-emerald-200 p-4 md:p-6 hover-lift">
                  <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-4 sm:gap-6">
                    <div>
                      <div className="text-sm font-medium text-emerald-700 mb-1">Total Savings</div>
                      <div className="text-3xl md:text-4xl font-bold text-emerald-800 tabular-nums">${costData.total_savings_usd.toFixed(2)}</div>
                      <div className="text-sm text-emerald-600 mt-1">{costData.savings_percentage}% saved vs. human labor</div>
                    </div>
                    <div className="grid grid-cols-3 gap-4 sm:gap-6">
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

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 stagger-children">
                <CostStatCard label="Total Spend" value={`$${costData.total_cost_usd.toFixed(2)}`}
                  icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>}
                  iconColor="text-brand-600 bg-brand-50" />
                <CostStatCard label="Avg / Conversation" value={`$${costData.avg_cost_per_conversation.toFixed(4)}`}
                  icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" /></svg>}
                  iconColor="text-blue-600 bg-blue-50" />
                <CostStatCard label="Input Tokens" value={formatNumber(costData.total_input_tokens)}
                  icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" /></svg>}
                  iconColor="text-green-600 bg-green-50" />
                <CostStatCard label="Output Tokens" value={formatNumber(costData.total_output_tokens)}
                  icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>}
                  iconColor="text-amber-600 bg-amber-50" />
              </div>

              <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 hover-lift">
                <h2 className="text-sm font-semibold text-gray-900 mb-4">Daily Cost: Human vs API</h2>
                {costData.daily.length === 0 ? (
                  <div className="h-72 flex items-center justify-center text-gray-400 text-sm">No cost data for this period.</div>
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Number(v).toFixed(0)}`} />
                        <Tooltip formatter={(value?: number, name?: string) => [`$${(value ?? 0).toFixed(2)}`, name ?? ""]} />
                        <Legend />
                        <Bar dataKey="estimated_human_cost_usd" name="Est. Human Cost" fill="#f38b1e" radius={[4, 4, 0, 0]} opacity={0.7} />
                        <Bar dataKey="total_cost_usd" name="API Cost" fill="#de101f" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>

              <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 hover-lift">
                <h2 className="text-sm font-semibold text-gray-900 mb-4">Daily API Cost Breakdown (USD)</h2>
                {costData.daily.length === 0 ? (
                  <div className="h-72 flex items-center justify-center text-gray-400 text-sm">No cost data for this period.</div>
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${Number(v).toFixed(2)}`} />
                        <Tooltip formatter={(value) => `$${Number(value).toFixed(4)}`} />
                        <Legend />
                        <Bar dataKey="agent_cost_usd" name="Agent" fill="#de101f" stackId="cost" />
                        <Bar dataKey="confidence_cost_usd" name="Confidence" fill="#f38b1e" stackId="cost" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>

              <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 hover-lift">
                <h2 className="text-sm font-semibold text-gray-900 mb-4">Daily Token Usage</h2>
                {costData.daily.length === 0 ? (
                  <div className="h-72 flex items-center justify-center text-gray-400 text-sm">No token data for this period.</div>
                ) : (
                  <div className="h-56 md:h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => { const n = Number(v); return n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n); }} />
                        <Tooltip formatter={(value) => formatNumber(Number(value))} />
                        <Legend />
                        <Area type="monotone" dataKey="input_tokens" name="Input Tokens" stroke="#3b82f6" fill="#dbeafe" />
                        <Area type="monotone" dataKey="output_tokens" name="Output Tokens" stroke="#10b981" fill="#d1fae5" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>

              <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 hover-lift">
                <h2 className="text-sm font-semibold text-gray-900 mb-4">Conversations per Day</h2>
                {costData.daily.length === 0 ? (
                  <div className="h-48 flex items-center justify-center text-gray-400 text-sm">No data for this period.</div>
                ) : (
                  <div className="h-40 md:h-48">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={costData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
                        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                        <Tooltip />
                        <Bar dataKey="conversations" name="Conversations" fill="#de101f" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}

      {activeTab === "token-usage" && (
        <>
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3">
            <select
              value={tokenTypeFilter}
              onChange={(e) => setTokenTypeFilter(e.target.value as "" | "user" | "flow")}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-surface text-gray-700 font-medium focus:outline-none focus:ring-2 focus:ring-brand-200"
            >
              <option value="">All Types</option>
              <option value="user">Users</option>
              <option value="flow">Flows</option>
            </select>
            <input
              type="text"
              placeholder="Search by name..."
              value={tokenNameSearch}
              onChange={(e) => setTokenNameSearch(e.target.value)}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-surface text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-brand-200 w-64"
            />
          </div>

          {/* Summary cards */}
          {tokenData && !tokenLoading && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 md:gap-4 stagger-children">
              <CostStatCard label="Total Tokens" value={formatNumber(tokenData.totals.total_tokens)}
                icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5" /></svg>}
                iconColor="text-brand-600 bg-brand-50" />
              <CostStatCard label="Input Tokens" value={formatNumber(tokenData.totals.input_tokens)}
                icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" /></svg>}
                iconColor="text-green-600 bg-green-50" />
              <CostStatCard label="Output Tokens" value={formatNumber(tokenData.totals.output_tokens)}
                icon={<svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>}
                iconColor="text-amber-600 bg-amber-50" />
            </div>
          )}

          {/* Table */}
          <div className="bg-surface rounded-xl border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50">
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Name</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Input Tokens</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Output Tokens</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Total Tokens</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Convos</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {tokenLoading ? (
                    <>
                      <SkeletonTableRow /><SkeletonTableRow /><SkeletonTableRow />
                      <SkeletonTableRow /><SkeletonTableRow />
                    </>
                  ) : tokenData && tokenData.rows.length > 0 ? (
                    tokenData.rows.map((row, i) => (
                      <tr key={`${row.type}-${row.name}-${i}`} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                            row.type === "user"
                              ? "bg-blue-50 text-blue-700"
                              : "bg-purple-50 text-purple-700"
                          }`}>
                            {row.type === "user" ? "User" : "Flow"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-gray-900 font-medium truncate max-w-xs">{row.name}</td>
                        <td className="px-4 py-3 text-right text-gray-600 tabular-nums">{formatNumber(row.input_tokens)}</td>
                        <td className="px-4 py-3 text-right text-gray-600 tabular-nums">{formatNumber(row.output_tokens)}</td>
                        <td className="px-4 py-3 text-right text-gray-900 font-semibold tabular-nums">{formatNumber(row.total_tokens)}</td>
                        <td className="px-4 py-3 text-right text-gray-600 tabular-nums">{row.conversations}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="px-4 py-12 text-center text-gray-400">
                        No token usage data for this period.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ConversationStatCard({ label, value, icon, iconColor }: { label: string; value: number; icon: React.ReactNode; iconColor: string }) {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 flex items-start gap-3 md:gap-4 hover-lift">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${iconColor}`}>{icon}</div>
      <div>
        <div className="text-xs text-gray-500 font-medium">{label}</div>
        <div className="text-xl md:text-2xl font-semibold text-gray-900 mt-0.5">{value}</div>
      </div>
    </div>
  );
}

function CostStatCard({ label, value, icon, iconColor }: { label: string; value: string; icon: React.ReactNode; iconColor: string }) {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-4 md:p-5 flex items-start gap-3 md:gap-4 hover-lift">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${iconColor}`}>{icon}</div>
      <div>
        <div className="text-xs text-gray-500 font-medium">{label}</div>
        <div className="text-xl md:text-2xl font-semibold text-gray-900 mt-0.5 tabular-nums">{value}</div>
      </div>
    </div>
  );
}
