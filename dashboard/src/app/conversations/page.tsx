"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { fetchConversations, fetchStats, fetchPersons } from "../../lib/api";
import type { Conversation, StatsResponse } from "../../lib/api";
import { useUser } from "../../lib/UserContext";
import ChatContextMenu from "../../components/ChatContextMenu";
import ClientTimestamp from "../../components/ClientTimestamp";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  RiSearchLine, RiCloseLine, RiRefreshLine, RiChat1Line,
  RiAtLine, RiChat3Line, RiLinksLine, RiComputerLine, RiGitBranchLine, RiTaskLine
} from "@remixicon/react";
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

const sourceIconStyles: Record<string, string> = {
  slack_mention: "text-blue-500",
  slack_dm: "text-indigo-500",
  pylon_webhook: "text-amber-500",
  dashboard: "text-brand-500",
  flow: "text-emerald-500",
  task_step: "text-purple-500",
};

const statusDotStyles: Record<string, string> = {
  running: "bg-blue-500 animate-pulse",
  completed: "bg-emerald-500",
  error: "bg-red-500",
};

const statusLabels: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  error: "Error",
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

function SourceIcon({ source, size = 15 }: { source: string; size?: number }) {
  const icons: Record<string, React.ElementType> = {
    slack_mention: RiAtLine,
    slack_dm: RiChat3Line,
    pylon_webhook: RiLinksLine,
    dashboard: RiComputerLine,
    flow: RiGitBranchLine,
    task_step: RiTaskLine,
  };
  const Icon = icons[source] || RiChat1Line;
  return <Icon size={size} />;
}

function SkeletonRow() {
  return (
    <TableRow>
      <TableCell className="w-4 pr-0"><Skeleton className="h-2 w-2 rounded-full" /></TableCell>
      <TableCell>
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-3 w-24 mt-1" />
      </TableCell>
      <TableCell className="w-8"><Skeleton className="h-4 w-4" /></TableCell>
      <TableCell><Skeleton className="h-5 w-14 rounded-full" /></TableCell>
      <TableCell><Skeleton className="h-3 w-14" /></TableCell>
      <TableCell><Skeleton className="h-3 w-20" /></TableCell>
      <TableCell className="w-16" />
    </TableRow>
  );
}

function MobileSkeletonCard() {
  return (
    <Card className="p-3">
      <CardContent className="p-0">
        <div className="flex items-start gap-2">
          <Skeleton className="h-2 w-2 rounded-full mt-1.5" />
          <div className="flex-1">
            <Skeleton className="h-4 w-3/4 mb-1.5" />
            <div className="flex items-center gap-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-3 w-12" />
            </div>
          </div>
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

  useEffect(() => {
    if (viewMode === "mine" && session?.user?.email) {
      setPersonFilter(session.user.email);
    } else if (viewMode === "all") {
      setPersonFilter("");
    }
    setPage(1);
  }, [viewMode, session?.user?.email]);

  useEffect(() => {
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

  function formatDuration(ms: number | null | undefined) {
    if (!ms) return null;
    return ms > 60000 ? `${Math.round(ms / 60000)}m` : `${Math.round(ms / 1000)}s`;
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="space-y-2 animate-fade-in-up">
        {/* Page header */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Conversations</h1>
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

        {/* Search + Filters — single row, no card borders */}
        <div className="flex flex-col md:flex-row gap-2 md:items-center">
          <div className="relative md:w-60 shrink-0">
            <RiSearchLine className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
            <Input
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 text-[13px] border-border focus-visible:ring-accent-200 focus-visible:border-accent-200"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 flex-1">
            <Select
              value={sourceFilter || "__all__"}
              onValueChange={(val) => { setSourceFilter(val === "__all__" ? "" : val); setPage(1); }}
            >
              <SelectTrigger className="w-auto min-w-[120px] bg-card border-border text-[13px] text-foreground">
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
              <SelectTrigger className="w-auto min-w-[130px] bg-card border-border text-[13px] text-foreground">
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
                <SelectTrigger className="w-auto min-w-[120px] bg-card border-border text-[13px] text-foreground">
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
              <SelectTrigger className="w-auto min-w-[110px] bg-card border-border text-[13px] text-foreground">
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
            <div className="ml-auto flex items-center gap-0.5">
              {hasActiveFilters && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={clearFilters}
                      className="text-muted-foreground hover:text-foreground press-scale h-8 w-8 p-0"
                    >
                      <RiCloseLine size={16} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Clear filters</TooltipContent>
                </Tooltip>
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => loadData()}
                    className="text-muted-foreground hover:text-foreground press-scale h-8 w-8 p-0"
                  >
                    <RiRefreshLine size={16} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </div>

        {/* Desktop table */}
        <div className="desktop-table bg-card rounded-xl overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="text-left">
                <TableHead className="w-4 pr-0"></TableHead>
                <TableHead>Conversation</TableHead>
                <TableHead className="w-8"></TableHead>
                <TableHead>Topic</TableHead>
                <TableHead>Cost</TableHead>
                <TableHead>Time</TableHead>
                <TableHead className="w-16"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <>
                  <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
                  <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
                </>
              ) : conversations.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-6">
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
                    className="group hover:bg-muted/50 cursor-pointer transition-colors animate-fade-in-up"
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
                    {/* Status dot */}
                    <TableCell className="w-4 pr-0">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className={cn("h-2 w-2 rounded-full", statusDotStyles[c.status] || "bg-gray-400")} />
                        </TooltipTrigger>
                        <TooltipContent>{statusLabels[c.status] || c.status}</TooltipContent>
                      </Tooltip>
                    </TableCell>

                    {/* Title + Account */}
                    <TableCell>
                      <div className="truncate font-medium text-foreground max-w-md" title={c.title || c.prompt?.slice(0, 80)}>
                        {c.title || (c.prompt ? c.prompt.slice(0, 60) + (c.prompt.length > 60 ? "..." : "") : "Untitled")}
                      </div>
                      {c.claude_account && (
                        <div className="text-xs text-muted-foreground/60 truncate mt-0.5">{c.claude_account}</div>
                      )}
                    </TableCell>

                    {/* Source icon */}
                    <TableCell className="w-8">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className={cn("inline-flex", sourceIconStyles[c.source] || "text-gray-400")}>
                            <SourceIcon source={c.source} />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{sourceLabels[c.source] || c.source}</TooltipContent>
                      </Tooltip>
                    </TableCell>

                    {/* Topic */}
                    <TableCell>
                      {c.topic ? (
                        <Badge variant="secondary" className={cn("text-xs", topicStyles[c.topic] || "bg-gray-100 text-gray-600")}>
                          {topicLabels[c.topic] || c.topic}
                        </Badge>
                      ) : <span className="text-xs text-muted-foreground/40">&mdash;</span>}
                    </TableCell>

                    {/* Cost — hover shows full details */}
                    <TableCell className="tabular-nums text-xs">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-muted-foreground cursor-default">
                            {c.cost?.total_cost_usd != null ? `$${c.cost.total_cost_usd.toFixed(2)}` : "-"}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" className="text-xs">
                          <div className="space-y-0.5 tabular-nums">
                            {c.cost?.total_cost_usd != null && <div>Cost: ${c.cost.total_cost_usd.toFixed(4)}</div>}
                            {c.savings?.savings_usd != null && (
                              <div className="text-emerald-400">Saved: +${c.savings.savings_usd.toFixed(2)}</div>
                            )}
                            <div>{c.total_turns} turns{formatDuration(c.duration_ms) ? ` · ${formatDuration(c.duration_ms)}` : ""}</div>
                            {c.confidence?.category && (
                              <div className="capitalize">{c.confidence.category} confidence</div>
                            )}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>

                    {/* Time */}
                    <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                      <ClientTimestamp iso={c.started_at} variant="short" />
                    </TableCell>

                    {/* Actions — hover reveal */}
                    <TableCell className="w-16">
                      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                        {c.status !== "running" && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button variant="ghost" size="xs" asChild className="text-brand-600 hover:bg-brand-50 press-scale">
                                <Link
                                  href={`/chat?continue=${c.conversation_id}`}
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <RiChat1Line size={14} />
                                </Link>
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Continue</TooltipContent>
                          </Tooltip>
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
                  <div className="flex items-start gap-2.5">
                    <div className={cn("h-2 w-2 rounded-full mt-1.5 shrink-0", statusDotStyles[c.status] || "bg-gray-400")} />
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium text-foreground truncate">
                        {c.title || (c.prompt ? c.prompt.slice(0, 60) + (c.prompt.length > 60 ? "..." : "") : "Untitled")}
                      </div>
                      <div className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground flex-wrap">
                        <span className={cn("inline-flex", sourceIconStyles[c.source] || "text-gray-400")}>
                          <SourceIcon source={c.source} size={12} />
                        </span>
                        <ClientTimestamp iso={c.started_at} variant="short" />
                        {c.cost?.total_cost_usd != null && (
                          <span className="tabular-nums">${c.cost.total_cost_usd.toFixed(2)}</span>
                        )}
                        {c.topic && (
                          <Badge variant="secondary" className={cn("text-[10px] py-0 px-1.5", topicStyles[c.topic] || "bg-gray-100 text-gray-600")}>
                            {topicLabels[c.topic] || c.topic}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
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
            <span className="px-3 py-1.5 text-[13px] text-muted-foreground">
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
    </TooltipProvider>
  );
}
