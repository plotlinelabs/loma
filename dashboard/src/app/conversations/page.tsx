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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { RiSearchLine, RiCloseLine, RiRefreshLine, RiChat1Line } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/EmptyState";

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
    <TableRow>
      <TableCell><Skeleton className="h-3 w-20" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
      <TableCell><Skeleton className="h-3 w-48" /><Skeleton className="h-2 w-32 mt-1.5" /></TableCell>
      <TableCell><Skeleton className="h-5 w-14 rounded-full" /></TableCell>
      <TableCell><Skeleton className="h-3 w-6 mx-auto" /></TableCell>
      <TableCell><Skeleton className="h-3 w-10" /></TableCell>
      <TableCell><Skeleton className="h-3 w-14" /></TableCell>
      <TableCell><Skeleton className="h-3 w-12" /></TableCell>
      <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
      <TableCell><Skeleton className="h-5 w-10 rounded-full" /></TableCell>
      <TableCell><Skeleton className="h-3 w-14" /></TableCell>
    </TableRow>
  );
}

function MobileSkeletonCard() {
  return (
    <Card className="p-4">
      <CardContent className="p-0">
        <div className="flex items-center gap-2 mb-2">
          <Skeleton className="h-5 w-16 rounded-full" />
          <Skeleton className="h-5 w-14 rounded-full" />
        </div>
        <Skeleton className="h-4 w-3/4 mb-1" />
        <Skeleton className="h-3 w-1/2 mb-2" />
        <div className="flex items-center gap-2">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-12" />
        </div>
      </CardContent>
    </Card>
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
    <div className="space-y-2 animate-fade-in-up">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Conversations</h1>
          <p className="text-sm text-muted-foreground mt-1">Browse and manage all agent conversations</p>
        </div>
        {isAdmin && (
          <div className="flex items-center bg-muted border border-border rounded-lg p-0.5">
            <Button
              variant={viewMode === "mine" ? "default" : "ghost"}
              size="sm"
              onClick={() => setViewMode("mine")}
              className={cn(
                viewMode === "mine"
                  ? "bg-accent-200 text-accent-on"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              My Conversations
            </Button>
            <Button
              variant={viewMode === "all" ? "default" : "ghost"}
              size="sm"
              onClick={() => setViewMode("all")}
              className={cn(
                viewMode === "all"
                  ? "bg-accent-200 text-accent-on"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              All
            </Button>
          </div>
        )}
      </div>

      {/* Search bar */}
      <div className="bg-card rounded-xl border border-border px-4 py-3">
        <div className="relative">
          <RiSearchLine className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
          <Input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 text-sm bg-muted/50 border-border focus-visible:ring-accent-200 focus-visible:border-accent-200"
          />
        </div>
      </div>

      {/* Filters bar */}
      <div className="bg-card rounded-xl border border-border px-4 py-3">
        <div className="flex flex-col md:flex-row md:flex-wrap gap-2 md:items-center md:justify-between">
          <div className="grid grid-cols-2 md:flex md:flex-wrap gap-2 md:gap-3">
            <Select
              value={sourceFilter || "__all__"}
              onValueChange={(val) => { setSourceFilter(val === "__all__" ? "" : val); setPage(1); }}
            >
              <SelectTrigger className="bg-card border-border text-sm text-foreground">
                <SelectValue placeholder="All Sources" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All Sources</SelectItem>
                <SelectItem value="slack_mention">Slack Mention</SelectItem>
                <SelectItem value="slack_dm">Slack DM</SelectItem>
                <SelectItem value="pylon_webhook">Pylon</SelectItem>
                <SelectItem value="dashboard">Dashboard</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={categoryFilter || "__all__"}
              onValueChange={(val) => { setCategoryFilter(val === "__all__" ? "" : val); setPage(1); }}
            >
              <SelectTrigger className="bg-card border-border text-sm text-foreground">
                <SelectValue placeholder="All Categories" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All Categories</SelectItem>
                <SelectItem value="resolved">Resolved</SelectItem>
                <SelectItem value="partial">Partial</SelectItem>
                <SelectItem value="unresolved">Unresolved</SelectItem>
                <SelectItem value="escalation_needed">Escalation Needed</SelectItem>
              </SelectContent>
            </Select>
            {viewMode === "all" && (
              <Select
                value={personFilter || "__all__"}
                onValueChange={(val) => { setPersonFilter(val === "__all__" ? "" : val); setPage(1); }}
              >
                <SelectTrigger className="bg-card border-border text-sm text-foreground">
                  <SelectValue placeholder="All Persons" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All Persons</SelectItem>
                  {persons.map((p) => (
                    <SelectItem key={p} value={p}>{p}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Select
              value={topicFilter || "__all__"}
              onValueChange={(val) => { setTopicFilter(val === "__all__" ? "" : val); setPage(1); }}
            >
              <SelectTrigger className="bg-card border-border text-sm text-foreground">
                <SelectValue placeholder="All Topics" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All Topics</SelectItem>
                <SelectItem value="debugging">Debugging</SelectItem>
                <SelectItem value="integration">Integration</SelectItem>
                <SelectItem value="billing">Billing</SelectItem>
                <SelectItem value="feature-request">Feature Request</SelectItem>
                <SelectItem value="campaign">Campaign</SelectItem>
                <SelectItem value="sdk">SDK</SelectItem>
                <SelectItem value="data">Data</SelectItem>
                <SelectItem value="security">Security</SelectItem>
                <SelectItem value="documentation">Docs</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            {hasActiveFilters && (
              <Button
                variant="ghost"
                size="sm"
                onClick={clearFilters}
                className="text-muted-foreground hover:text-foreground press-scale"
              >
                <RiCloseLine size={14} />
                Clear
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadData()}
              className="text-brand-600 hover:text-brand-800 press-scale"
            >
              <RiRefreshLine size={16} />
              Refresh
            </Button>
          </div>
        </div>
      </div>

      {/* Desktop table */}
      <div className="desktop-table bg-card rounded-xl border border-border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="border-b border-muted text-left">
              <TableHead>Time</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Account</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Topic</TableHead>
              <TableHead>Turns</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Cost</TableHead>
              <TableHead>Savings</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead className="w-10"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="divide-y divide-muted/50">
            {loading ? (
              <>
                <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
                <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
              </>
            ) : conversations.length === 0 ? (
              <TableRow>
                <TableCell colSpan={12} className="py-6">
                  {hasActiveFilters ? (
                    <EmptyState
                      icon={RiChat1Line}
                      title="No conversations match your filters"
                      action="Clear all filters"
                      onAction={clearFilters}
                    />
                  ) : (
                    <EmptyState icon={RiChat1Line} title="No conversations found" description="Start a new conversation from the chat page" />
                  )}
                </TableCell>
              </TableRow>
            ) : (
              conversations.map((c, idx) => (
                <TableRow
                  key={c.conversation_id}
                  className="hover:bg-muted/50 cursor-pointer transition-colors animate-fade-in-up"
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
                  <TableCell className="text-muted-foreground whitespace-nowrap text-xs">
                    <ClientTimestamp iso={c.started_at} variant="short" />
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className={cn("text-xs", sourceStyles[c.source] || "bg-gray-100 text-gray-600")}>
                      {sourceLabels[c.source] || c.source}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {c.claude_account ? c.claude_account : <span className="text-muted-foreground/50">&mdash;</span>}
                  </TableCell>
                  <TableCell className="text-foreground max-w-xs">
                    <div className="truncate font-medium" title={c.title || c.prompt?.slice(0, 80)}>
                      {c.title || c.prompt?.slice(0, 60) + (c.prompt?.length > 60 ? "..." : "")}
                    </div>
                  </TableCell>
                  <TableCell>
                    {c.topic ? (
                      <Badge variant="secondary" className={cn("text-xs", topicStyles[c.topic] || "bg-gray-100 text-gray-600")}>
                        {topicLabels[c.topic] || c.topic}
                      </Badge>
                    ) : <span className="text-xs text-muted-foreground/50">&mdash;</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-center">{c.total_turns}</TableCell>
                  <TableCell className="text-muted-foreground whitespace-nowrap">
                    {c.duration_ms ? (c.duration_ms > 60000 ? `${Math.round(c.duration_ms / 60000)}m` : `${Math.round(c.duration_ms / 1000)}s`) : "-"}
                  </TableCell>
                  <TableCell className="text-muted-foreground whitespace-nowrap text-xs tabular-nums">
                    {c.cost?.total_cost_usd != null ? `$${c.cost.total_cost_usd.toFixed(4)}` : "-"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs tabular-nums">
                    {c.savings?.savings_usd != null ? (
                      <span className="text-emerald-600 font-medium">+${c.savings.savings_usd.toFixed(2)}</span>
                    ) : <span className="text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className={cn("text-xs", statusStyles[c.status] || "bg-gray-100 text-gray-600")}>{c.status}</Badge>
                  </TableCell>
                  <TableCell><ConfidenceBadge confidence={c.confidence} /></TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {c.status !== "running" && (
                        <Button variant="ghost" size="xs" asChild className="text-brand-600 hover:bg-brand-50 press-scale">
                          <Link
                            href={`/chat?continue=${c.conversation_id}`}
                            onClick={(e) => e.stopPropagation()}
                            title="Continue this conversation"
                          >
                            <RiChat1Line size={14} />
                            Continue
                          </Link>
                        </Button>
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
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Mobile card list */}
      <div className="mobile-cards space-y-2">
        {loading ? (
          <>
            <MobileSkeletonCard /><MobileSkeletonCard /><MobileSkeletonCard />
            <MobileSkeletonCard /><MobileSkeletonCard />
          </>
        ) : conversations.length === 0 ? (
          hasActiveFilters ? (
            <EmptyState
              icon={RiChat1Line}
              title="No conversations match your filters"
              action="Clear all filters"
              onAction={clearFilters}
            />
          ) : (
            <EmptyState icon={RiChat1Line} title="No conversations found" description="Start a new conversation from the chat page" />
          )
        ) : (
          conversations.map((c, idx) => (
            <Card
              key={c.conversation_id}
              className="p-3 active:bg-muted/50 transition-colors animate-fade-in-up cursor-pointer"
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
              <CardContent className="p-0">
                <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                  <Badge variant="secondary" className={cn("text-xs", sourceStyles[c.source] || "bg-gray-100 text-gray-600")}>
                    {sourceLabels[c.source] || c.source}
                  </Badge>
                  {c.topic && (
                    <Badge variant="secondary" className={cn("text-xs", topicStyles[c.topic] || "bg-gray-100 text-gray-600")}>
                      {topicLabels[c.topic] || c.topic}
                    </Badge>
                  )}
                  <Badge variant="secondary" className={cn("text-xs", statusStyles[c.status] || "bg-gray-100 text-gray-600")}>{c.status}</Badge>
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
                <div className="text-sm font-medium text-foreground truncate">
                  {c.title || c.prompt?.slice(0, 60) + (c.prompt?.length > 60 ? "..." : "")}
                </div>
                {c.title && (
                  <div className="text-xs text-muted-foreground truncate mt-0.5">
                    {c.prompt?.slice(0, 60)}{c.prompt?.length > 60 ? "..." : ""}
                  </div>
                )}
                <div className="flex items-center gap-2 mt-2 text-xs text-muted-foreground">
                  <ClientTimestamp iso={c.started_at} variant="short" />
                  <span>{c.total_turns} turns</span>
                  {c.cost?.total_cost_usd != null && (
                    <span className="tabular-nums">${c.cost.total_cost_usd.toFixed(4)}</span>
                  )}
                  {c.savings?.savings_usd != null && (
                    <span className="text-emerald-600 font-medium tabular-nums">+${c.savings.savings_usd.toFixed(2)}</span>
                  )}
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="press-scale"
          >
            Previous
          </Button>
          <span className="px-3 py-1.5 text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="press-scale"
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
