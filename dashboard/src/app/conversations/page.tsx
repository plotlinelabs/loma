"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { fetchConversations, fetchStats, fetchPersons, basePath } from "../../lib/api";
import type { Conversation, StatsResponse } from "../../lib/api";
import { useUser } from "../../lib/UserContext";
import ChatContextMenu from "../../components/ChatContextMenu";
import ConfidenceBadge from "../../components/ConfidenceBadge";
import ClientTimestamp from "../../components/ClientTimestamp";

const sourceLabels: Record<string, string> = {
  slack_mention: "Slack Mention",
  slack_dm: "Slack DM",
  pylon_webhook: "Pylon",
  dashboard: "Dashboard",
  flow: "Flow",
  task_step: "Task",
};

const sourceStyles: Record<string, string> = {
  slack_mention: "bg-blue-50 text-blue-700",
  slack_dm: "bg-indigo-50 text-indigo-700",
  pylon_webhook: "bg-amber-50 text-amber-700",
  dashboard: "bg-brand-50 text-brand-700",
  flow: "bg-emerald-50 text-emerald-700",
  task_step: "bg-purple-50 text-purple-700",
};

const statusStyles: Record<string, string> = {
  running: "bg-blue-50 text-blue-700",
  completed: "bg-gray-100 text-gray-600",
  error: "bg-red-50 text-red-700",
};

const topicStyles: Record<string, string> = {
  debugging: "bg-red-50 text-red-700",
  integration: "bg-blue-50 text-blue-700",
  billing: "bg-green-50 text-green-700",
  "feature-request": "bg-purple-50 text-purple-700",
  campaign: "bg-orange-50 text-orange-700",
  sdk: "bg-cyan-50 text-cyan-700",
  data: "bg-teal-50 text-teal-700",
  security: "bg-rose-50 text-rose-700",
  documentation: "bg-yellow-50 text-yellow-700",
  other: "bg-gray-50 text-gray-600",
};

const topicLabels: Record<string, string> = {
  debugging: "Debugging",
  integration: "Integration",
  billing: "Billing",
  "feature-request": "Feature Request",
  campaign: "Campaign",
  sdk: "SDK",
  data: "Data",
  security: "Security",
  documentation: "Docs",
  other: "Other",
};

function SkeletonRow() {
  return (
    <tr>
      <td className="px-4 py-3"><div className="skeleton h-3 w-20" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded-full" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-48" /><div className="skeleton h-2 w-32 mt-1.5" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-14 rounded-full" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-6 mx-auto" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-10" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-14" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-12" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded-full" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-10 rounded-full" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3 w-14" /></td>
    </tr>
  );
}

function MobileSkeletonCard() {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-2">
        <div className="skeleton h-5 w-16 rounded-full" />
        <div className="skeleton h-5 w-14 rounded-full" />
      </div>
      <div className="skeleton h-4 w-3/4 mb-1" />
      <div className="skeleton h-3 w-1/2 mb-3" />
      <div className="flex items-center gap-3">
        <div className="skeleton h-3 w-20" />
        <div className="skeleton h-3 w-16" />
        <div className="skeleton h-3 w-12" />
      </div>
    </div>
  );
}

export default function ConversationsPage() {
  const router = useRouter();
  const { data: session } = useSession();
  const { isAdmin, isPinned, togglePin, projects, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject, refreshProjects } = useUser();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [sourceFilter, setSourceFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [personFilter, setPersonFilter] = useState("");
  const [topicFilter, setTopicFilter] = useState("");
  const [persons, setPersons] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<"mine" | "all">("mine");

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    fetchPersons()
      .then((data) => setPersons(data.persons))
      .catch((e) => console.error("Failed to load persons:", e));
  }, []);

  // When viewMode changes to "mine", set personFilter to user email; when "all", clear it
  useEffect(() => {
    if (viewMode === "mine" && session?.user?.email) {
      setPersonFilter(session.user.email);
    } else if (viewMode === "all") {
      setPersonFilter("");
    }
    setPage(1);
  }, [viewMode, session?.user?.email]);

  useEffect(() => {
    // Don't fetch until personFilter is set when in "mine" mode — prevents
    // loading all conversations first and then flickering to "my" data.
    if (viewMode === "mine" && !personFilter) return;
    loadData();
  }, [page, sourceFilter, categoryFilter, debouncedSearch, personFilter, topicFilter]);

  async function loadData() {
    setLoading(true);
    try {
      const [convos, statsData] = await Promise.all([
        fetchConversations({
          page,
          source: sourceFilter || undefined,
          category: categoryFilter || undefined,
          search: debouncedSearch || undefined,
          person: personFilter || undefined,
          topic: topicFilter || undefined,
        }),
        page === 1 ? fetchStats() : Promise.resolve(null),
      ]);
      setConversations(convos.conversations);
      setTotalPages(convos.total_pages);
      if (statsData) setStats(statsData);
    } catch (e) {
      console.error("Failed to load data:", e);
    } finally {
      setLoading(false);
    }
  }

  const hasActiveFilters = sourceFilter || categoryFilter || debouncedSearch || (viewMode === "all" && personFilter) || topicFilter;

  const clearFilters = useCallback(() => {
    setSourceFilter("");
    setCategoryFilter("");
    setSearchQuery("");
    setDebouncedSearch("");
    if (viewMode === "all") {
      setPersonFilter("");
    }
    setTopicFilter("");
    setPage(1);
  }, [viewMode]);

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-semibold text-gray-900">Conversations</h1>
          <p className="text-sm text-gray-500 mt-1">Browse and manage all agent conversations</p>
        </div>
        {isAdmin && (
          <div className="flex items-center bg-surface border border-gray-200 rounded-lg p-0.5">
            <button
              onClick={() => setViewMode("mine")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                viewMode === "mine"
                  ? "bg-accent-200 text-accent-on"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              My Conversations
            </button>
            <button
              onClick={() => setViewMode("all")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                viewMode === "all"
                  ? "bg-accent-200 text-accent-on"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              All
            </button>
          </div>
        )}
      </div>

      {/* Search bar */}
      <div className="bg-surface rounded-xl border border-gray-200 px-4 py-3">
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
            />
          </svg>
          <input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 text-sm text-gray-700 bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200 focus:bg-surface transition-colors"
          />
        </div>
      </div>

      {/* Filters bar */}
      <div className="bg-surface rounded-xl border border-gray-200 px-4 py-3">
        <div className="flex flex-col md:flex-row md:flex-wrap gap-3 md:items-center md:justify-between">
          <div className="grid grid-cols-2 md:flex md:flex-wrap gap-2 md:gap-3">
            <select
              value={sourceFilter}
              onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
              className="bg-surface border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
            >
              <option value="">All Sources</option>
              <option value="slack_mention">Slack Mention</option>
              <option value="slack_dm">Slack DM</option>
              <option value="pylon_webhook">Pylon</option>
              <option value="dashboard">Dashboard</option>
            </select>
            <select
              value={categoryFilter}
              onChange={(e) => { setCategoryFilter(e.target.value); setPage(1); }}
              className="bg-surface border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
            >
              <option value="">All Categories</option>
              <option value="resolved">Resolved</option>
              <option value="partial">Partial</option>
              <option value="unresolved">Unresolved</option>
              <option value="escalation_needed">Escalation Needed</option>
            </select>
            {viewMode === "all" && (
              <select
                value={personFilter}
                onChange={(e) => { setPersonFilter(e.target.value); setPage(1); }}
                className="bg-surface border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
              >
                <option value="">All Persons</option>
                {persons.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            )}
            <select
              value={topicFilter}
              onChange={(e) => { setTopicFilter(e.target.value); setPage(1); }}
              className="bg-surface border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
            >
              <option value="">All Topics</option>
              <option value="debugging">Debugging</option>
              <option value="integration">Integration</option>
              <option value="billing">Billing</option>
              <option value="feature-request">Feature Request</option>
              <option value="campaign">Campaign</option>
              <option value="sdk">SDK</option>
              <option value="data">Data</option>
              <option value="security">Security</option>
              <option value="documentation">Docs</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div className="flex items-center gap-3">
            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="text-sm text-gray-500 hover:text-gray-700 font-medium flex items-center gap-1 transition-colors press-scale"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
                Clear
              </button>
            )}
            <button
              onClick={() => loadData()}
              className="text-sm text-brand-600 hover:text-brand-800 font-medium flex items-center gap-1.5 transition-colors press-scale"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" />
              </svg>
              Refresh
            </button>
          </div>
        </div>
      </div>

      {/* Desktop table */}
      <div className="desktop-table bg-surface rounded-xl border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left">
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Time</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Source</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Account</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Title</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Topic</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Turns</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Duration</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Cost</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Savings</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Status</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Confidence</th>
              <th className="px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider w-10"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loading ? (
              <>
                <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
                <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
              </>
            ) : conversations.length === 0 ? (
              <tr>
                <td colSpan={12} className="px-4 py-12 text-center text-gray-400">
                  {hasActiveFilters ? (
                    <div>
                      <p>No conversations match your filters.</p>
                      <button onClick={clearFilters} className="mt-2 text-sm text-brand-600 hover:text-brand-800 font-medium">Clear all filters</button>
                    </div>
                  ) : "No conversations found."}
                </td>
              </tr>
            ) : (
              conversations.map((c, idx) => (
                <tr
                  key={c.conversation_id}
                  className="hover:bg-gray-50/80 cursor-pointer transition-colors animate-fade-in-up"
                  style={{ animationDelay: `${Math.min(idx * 30, 300)}ms` }}
                  onClick={(e) => {
                    const url = `/conversations/${c.conversation_id}`;
                    if (e.metaKey || e.ctrlKey) {
                      window.open(url, "_blank");
                    } else {
                      router.push(url);
                    }
                  }}
                >
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap text-xs">
                    <ClientTimestamp iso={c.started_at} variant="short" />
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${sourceStyles[c.source] || "bg-gray-100 text-gray-600"}`}>
                      {sourceLabels[c.source] || c.source}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {c.claude_account ? c.claude_account : <span className="text-gray-300">&mdash;</span>}
                  </td>
                  <td className="px-4 py-3 text-gray-700 max-w-xs">
                    <div className="truncate font-medium" title={c.title || c.prompt?.slice(0, 80)}>
                      {c.title || c.prompt?.slice(0, 60) + (c.prompt?.length > 60 ? "..." : "")}
                    </div>
                    {c.title && (
                      <div className="truncate text-xs text-gray-400 mt-0.5" title={c.prompt}>
                        {c.prompt?.slice(0, 60)}{c.prompt?.length > 60 ? "..." : ""}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {c.topic ? (
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${topicStyles[c.topic] || "bg-gray-100 text-gray-600"}`}>
                        {topicLabels[c.topic] || c.topic}
                      </span>
                    ) : <span className="text-xs text-gray-300">&mdash;</span>}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-center">{c.total_turns}</td>
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                    {c.duration_ms ? (c.duration_ms > 60000 ? `${Math.round(c.duration_ms / 60000)}m` : `${Math.round(c.duration_ms / 1000)}s`) : "-"}
                  </td>
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap text-xs tabular-nums">
                    {c.cost?.total_cost_usd != null ? `$${c.cost.total_cost_usd.toFixed(4)}` : "-"}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-xs tabular-nums">
                    {c.savings?.savings_usd != null ? (
                      <span className="text-emerald-600 font-medium">+${c.savings.savings_usd.toFixed(2)}</span>
                    ) : <span className="text-gray-400">-</span>}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusStyles[c.status] || "bg-gray-100 text-gray-600"}`}>{c.status}</span>
                  </td>
                  <td className="px-4 py-3"><ConfidenceBadge confidence={c.confidence} /></td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {c.status !== "running" && (
                        <Link
                          href={`/chat?continue=${c.conversation_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-brand-600 hover:bg-brand-50 rounded-md transition-colors press-scale"
                          title="Continue this conversation"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 0 1 .865-.501 48.172 48.172 0 0 0 3.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
                          </svg>
                          Continue
                        </Link>
                      )}
                      <ChatContextMenu
                        conversationId={c.conversation_id}
                        conversationTitle={c.title || c.prompt?.slice(0, 50) || "Untitled"}
                        isPinned={isPinned(c.conversation_id)}
                        projectId={c.project_id}
                        projects={projects}
                        onRename={async (id, newTitle) => { await renameConversation(id, newTitle); loadData(); }}
                        onDelete={async (id) => { await removeConversation(id); loadData(); }}
                        onTogglePin={togglePin}
                        onAssignProject={async (id, pid) => { await assignToProject(id, pid); loadData(); }}
                        onRemoveProject={async (id) => { await unassignFromProject(id); loadData(); }}
                        onCreateProject={async (name) => { await addProject(name); }}
                      />
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile card list */}
      <div className="mobile-cards space-y-3">
        {loading ? (
          <>
            <MobileSkeletonCard /><MobileSkeletonCard /><MobileSkeletonCard />
            <MobileSkeletonCard /><MobileSkeletonCard />
          </>
        ) : conversations.length === 0 ? (
          <div className="bg-surface rounded-xl border border-gray-200 p-8 text-center text-gray-400 text-sm">
            {hasActiveFilters ? (
              <div>
                <p>No conversations match your filters.</p>
                <button onClick={clearFilters} className="mt-2 text-sm text-brand-600 hover:text-brand-800 font-medium">Clear all filters</button>
              </div>
            ) : "No conversations found."}
          </div>
        ) : (
          conversations.map((c, idx) => (
            <div
              key={c.conversation_id}
              className="bg-surface rounded-xl border border-gray-200 p-4 active:bg-gray-50 transition-colors animate-fade-in-up cursor-pointer"
              style={{ animationDelay: `${Math.min(idx * 30, 300)}ms` }}
              onClick={(e) => {
                const url = `/conversations/${c.conversation_id}`;
                if (e.metaKey || e.ctrlKey) {
                  window.open(url, "_blank");
                } else {
                  router.push(url);
                }
              }}
            >
              <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${sourceStyles[c.source] || "bg-gray-100 text-gray-600"}`}>
                  {sourceLabels[c.source] || c.source}
                </span>
                {c.topic && (
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${topicStyles[c.topic] || "bg-gray-100 text-gray-600"}`}>
                    {topicLabels[c.topic] || c.topic}
                  </span>
                )}
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusStyles[c.status] || "bg-gray-100 text-gray-600"}`}>{c.status}</span>
                <div className="ml-auto" onClick={(e) => e.stopPropagation()}>
                  <ChatContextMenu
                    conversationId={c.conversation_id}
                    conversationTitle={c.title || c.prompt?.slice(0, 50) || "Untitled"}
                    isPinned={isPinned(c.conversation_id)}
                    projectId={c.project_id}
                    projects={projects}
                    onRename={async (id, newTitle) => { await renameConversation(id, newTitle); loadData(); }}
                    onDelete={async (id) => { await removeConversation(id); loadData(); }}
                    onTogglePin={togglePin}
                    onAssignProject={async (id, pid) => { await assignToProject(id, pid); loadData(); }}
                    onRemoveProject={async (id) => { await unassignFromProject(id); loadData(); }}
                    onCreateProject={async (name) => { await addProject(name); }}
                  />
                </div>
              </div>
              <div className="text-sm font-medium text-gray-900 truncate">
                {c.title || c.prompt?.slice(0, 60) + (c.prompt?.length > 60 ? "..." : "")}
              </div>
              {c.title && (
                <div className="text-xs text-gray-400 truncate mt-0.5">
                  {c.prompt?.slice(0, 60)}{c.prompt?.length > 60 ? "..." : ""}
                </div>
              )}
              <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                <ClientTimestamp iso={c.started_at} variant="short" />
                <span>{c.total_turns} turns</span>
                {c.cost?.total_cost_usd != null && (
                  <span className="tabular-nums">${c.cost.total_cost_usd.toFixed(4)}</span>
                )}
                {c.savings?.savings_usd != null && (
                  <span className="text-emerald-600 font-medium tabular-nums">+${c.savings.savings_usd.toFixed(2)}</span>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 text-sm bg-surface border border-gray-200 rounded-lg disabled:opacity-40 hover:bg-gray-50 text-gray-700 font-medium transition-colors press-scale"
          >
            Previous
          </button>
          <span className="px-3 py-1.5 text-sm text-gray-500">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 text-sm bg-surface border border-gray-200 rounded-lg disabled:opacity-40 hover:bg-gray-50 text-gray-700 font-medium transition-colors press-scale"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
