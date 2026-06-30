"use client";

import { useEffect, useState, useCallback } from "react";
import { useSession, signOut } from "next-auth/react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { fetchConversations, fetchPinnedConversations, fetchPoolStatus } from "../lib/api";
import type { Conversation, PoolStatus } from "../lib/api";
import { useUser } from "../lib/UserContext";
import type { SystemRole } from "../lib/governance-api";
import CrosscutIcon from "./CrosscutIcon";
import ChatContextMenu from "./ChatContextMenu";
import { useTheme } from "../lib/ThemeContext";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  RiChat1Line,
  RiGridLine,
  RiTimeLine,
  RiBookOpenLine,
  RiDownloadLine,
  RiBarChartBoxLine,
  RiSettings3Line,
  RiShieldCheckLine,
  RiCloseLine,
  RiArrowDownSLine,
  RiSunLine,
  RiComputerLine,
  RiMoonLine,
  RiMenuFoldLine,
  RiMenuUnfoldLine,
} from "@remixicon/react";

type NavItem = {
  name: string;
  href: string;
  icon: React.ReactNode;
  badgeKey?: never;
  /** Minimum system role required to see this nav item */
  minRole?: SystemRole;
};

const navigation: NavItem[] = [
  {
    name: "Home",
    href: "/",
    icon: <RiChat1Line size={16} />,
  },
  {
    name: "Activity",
    href: "/conversations",
    icon: <RiGridLine size={16} />,
  },
  {
    name: "Flows",
    href: "/flows",
    minRole: "analyst",
    icon: <RiTimeLine size={16} />,
  },
  {
    name: "Skills",
    href: "/skills",
    minRole: "analyst",
    icon: <RiBookOpenLine size={16} />,
  },
  {
    name: "Webhook Logs",
    href: "/webhook-logs",
    minRole: "analyst",
    icon: <RiDownloadLine size={16} />,
  },
  {
    name: "Analytics",
    href: "/analytics",
    minRole: "analyst",
    icon: <RiBarChartBoxLine size={16} />,
  },
  {
    name: "Manage Integrations",
    href: "/integrations/manage",
    minRole: "maintainer",
    icon: <RiSettings3Line size={16} />,
  },
  {
    name: "Admin",
    href: "/admin",
    minRole: "maintainer",
    icon: <RiShieldCheckLine size={16} />,
  },
];

function SidebarSkeleton() {
  const widths = [72, 80, 56, 64, 96, 72, 96, 88];
  return (
    <>
      <div className="px-3 space-y-0.5">
        {widths.map((w, i) => (
          <div key={i} className="flex items-center gap-2 px-2 py-1">
            <Skeleton className="w-[16px] h-[16px] rounded" />
            <Skeleton className="h-3.5 rounded" style={{ width: `${w}px` }} />
          </div>
        ))}
      </div>
      <div className="mt-2 px-2.5">
        <Skeleton className="h-2.5 w-12 rounded mb-3" />
        <div className="space-y-2 px-1">
          <Skeleton className="h-3.5 w-36 rounded" />
          <Skeleton className="h-3.5 w-28 rounded" />
        </div>
      </div>
      <div className="mt-auto px-2.5 py-2">
        <Separator className="mb-3" />
        <div className="flex items-center gap-2 px-1">
          <Skeleton className="w-8 h-8 rounded-full" />
          <div className="space-y-1.5 flex-1">
            <Skeleton className="h-3.5 w-28 rounded" />
            <Skeleton className="h-2.5 w-16 rounded" />
          </div>
        </div>
      </div>
    </>
  );
}

function PoolStatusWidget({ poolStatus, collapsed }: { poolStatus: PoolStatus; collapsed: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const accountCount = poolStatus.accounts?.length || 0;
  const cooldownCount = poolStatus.accounts_on_cooldown?.length || 0;
  const distribution = poolStatus.account_distribution || {};
  const opencode = poolStatus.opencode;
  const opencodeColor = !opencode?.enabled
    ? "bg-gray-300"
    : opencode.total_available > 0
      ? "bg-emerald-500"
      : opencode.total_warming > 0
        ? "bg-amber-400 animate-pulse"
        : "bg-red-500";

  const statusColor = poolStatus.queue_depth > 0
    ? "bg-red-500"
    : poolStatus.available > 0
      ? "bg-emerald-500"
      : poolStatus.warming > 0
        ? "bg-amber-400 animate-pulse"
        : "bg-red-500";

  if (collapsed) {
    return (
      <div className="px-2.5 py-1 flex flex-col items-center gap-1">
        <span className={cn("w-2 h-2 rounded-full flex-shrink-0", statusColor)} />
        {opencode && (
          <span className={cn("w-2 h-2 rounded-full flex-shrink-0", opencodeColor)} />
        )}
      </div>
    );
  }

  return (
    <div className="px-2.5 py-1">
      <Button
        variant="ghost"
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left group space-y-1.5 h-auto px-0 rounded-none"
      >
        <div className="flex items-center gap-2">
          <span className={cn("w-2 h-2 rounded-full flex-shrink-0", statusColor)} />
          <span className="text-[11px] text-muted-foreground flex-1">
            {accountCount === 0
              ? "Claude · no accounts"
              : poolStatus.queue_depth > 0
                ? `Claude · queued (${poolStatus.queue_depth})`
                : `Claude · ${poolStatus.available}/${poolStatus.pool_size} available`}
          </span>
          <RiArrowDownSLine
            size={12}
            className={cn(
              "text-muted-foreground transition-transform",
              expanded && "rotate-180"
            )}
          />
        </div>
        {opencode && (
          <div className="flex items-center gap-2">
            <span className={cn("w-2 h-2 rounded-full flex-shrink-0", opencodeColor)} />
            <span className="text-[11px] text-muted-foreground flex-1">
              {opencode.enabled
                ? `OpenCode · ${opencode.total_available}/${opencode.pool_size} warm${opencode.total_warming ? ` · ${opencode.total_warming} warming` : ""}`
                : "OpenCode · warm pool off"}
            </span>
          </div>
        )}
      </Button>
      {expanded && (
        <div className="mt-2 pl-4 space-y-2">
          {accountCount > 0 && (
            <div className="space-y-0.5">
              <div className="text-[10px] text-muted-foreground mb-1">
                Claude · {accountCount} account{accountCount !== 1 ? "s" : ""} · {poolStatus.in_use} busy{poolStatus.warming > 0 ? ` · ${poolStatus.warming} warming` : ""}
              </div>
              {poolStatus.accounts.map((email) => {
                const count = distribution[email] || 0;
                const onCooldown = poolStatus.accounts_on_cooldown?.includes(email);
                const displayEmail = email;
                return (
                  <div key={email} className="flex items-center gap-1.5 text-[10px]">
                    {onCooldown ? (
                      <span className="text-amber-500" title="Rate limited — cooldown">!</span>
                    ) : (
                      <span className="text-muted-foreground">
                        {Array.from({ length: count }, (_, i) => (
                          <span key={i} className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 mr-0.5" />
                        ))}
                      </span>
                    )}
                    <span className={onCooldown ? "text-amber-600 line-through" : "text-muted-foreground"}>
                      {displayEmail}
                    </span>
                    {count > 0 && (
                      <span className="text-muted-foreground ml-auto">{count}</span>
                    )}
                    {onCooldown && (
                      <span className="text-[9px] text-amber-500 ml-auto">cooldown</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {opencode?.models?.length ? (
            <div className="space-y-0.5">
              <div className="text-[10px] text-muted-foreground mb-1">
                OpenCode · {opencode.active_sessions} active session{opencode.active_sessions === 1 ? "" : "s"}
              </div>
              {opencode.models.map((model) => (
                <div key={model.model} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  <span className={cn("w-1.5 h-1.5 rounded-full", model.available > 0 ? "bg-emerald-400" : model.warming > 0 ? "bg-amber-400 animate-pulse" : "bg-gray-300")} />
                  <span className="truncate">{model.model}</span>
                  <span className="text-muted-foreground ml-auto">
                    {model.available}/{model.pool_size} warm{model.warming ? ` · ${model.warming} warming` : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  isOpen,
  onClose,
  collapsed,
  onToggleCollapse,
}: {
  isOpen: boolean;
  onClose: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const { data: session, status } = useSession();
  const { loading: userLoading, hasRole, pinnedIds, isPinned, togglePin, projects, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject, refreshProjects } = useUser();
  const { theme, setTheme } = useTheme();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeContinueId = pathname === "/chat" ? searchParams.get("continue") : null;
  const [myConversations, setMyConversations] = useState<Conversation[]>([]);
  const [pinnedConversations, setPinnedConversations] = useState<Conversation[]>([]);
  const [badgeCounts] = useState<Record<string, number>>({});
  const [poolStatus, setPoolStatus] = useState<PoolStatus | null>(null);

  // Filter nav items by role
  const visibleNav = navigation.filter((item) => !item.minRole || hasRole(item.minRole));

  // Reload conversations after a mutation (rename, delete, project change)
  const reloadConversations = useCallback(() => {
    if (status === "authenticated" && session?.user?.email) {
      fetchConversations({ person: session.user.email, page: 1 })
        .then((data) => setMyConversations(data.conversations.slice(0, 8)))
        .catch(() => {});
    }
    if (pinnedIds.size > 0) {
      fetchPinnedConversations()
        .then((data) => setPinnedConversations(data.conversations))
        .catch(() => {});
    }
  }, [status, session?.user?.email, pinnedIds.size]);

  // Close sidebar on route change (mobile)
  useEffect(() => {
    onClose();
  }, [pathname]);

  // Load user's recent conversations (wait for user to load so isPinned works correctly)
  useEffect(() => {
    if (!userLoading && status === "authenticated" && session?.user?.email) {
      fetchConversations({ person: session.user.email, page: 1 })
        .then((data) => setMyConversations(data.conversations.slice(0, 8)))
        .catch((e) => console.error("Failed to load my conversations:", e));
    }
  }, [userLoading, status, session?.user?.email]);

  // Load pinned conversations
  useEffect(() => {
    if (pinnedIds.size > 0) {
      fetchPinnedConversations()
        .then((data) => setPinnedConversations(data.conversations))
        .catch((e) => console.error("Failed to load pinned conversations:", e));
    } else {
      setPinnedConversations([]);
    }
  }, [pinnedIds]);

  // Poll pool status every 5 seconds
  useEffect(() => {
    const poll = () => fetchPoolStatus().then(setPoolStatus).catch(() => {});
    poll();
    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
  }, []);

  const sidebarContent = (
    <>
      {/* Logo + collapse toggle + close button */}
      <div className={cn("flex items-center justify-between", collapsed ? "flex-col gap-1 px-2 pt-2 pb-1" : "px-3 pt-3 pb-2")}>
        <Link href="/" prefetch onClick={onClose} className={cn("flex items-center gap-2", collapsed && "justify-center")}>
          <CrosscutIcon size={collapsed ? 22 : 20} />
          {!collapsed && (
            <span className="font-[family-name:var(--font-logo)] text-base font-bold tracking-[0.5px] text-foreground/80">
              Loma
            </span>
          )}
        </Link>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onToggleCollapse}
            className="hidden md:flex text-muted-foreground hover:text-foreground"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <RiMenuUnfoldLine size={14} /> : <RiMenuFoldLine size={14} />}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            className="md:hidden text-muted-foreground hover:text-foreground"
            aria-label="Close menu"
          >
            <RiCloseLine size={16} />
          </Button>
        </div>
      </div>

      {userLoading ? (
        <SidebarSkeleton />
      ) : (
        <>
          {/* Navigation */}
          <nav className="px-3 space-y-0.5">
            {visibleNav.map((item) => {
              const isActive = item.href === "/"
                ? pathname === "/" || pathname === ""
                : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  prefetch
                  onClick={onClose}
                  className={cn(
                    "flex items-center rounded-lg text-[12px] font-medium transition-all duration-150",
                    collapsed
                      ? "justify-center px-0 py-1.5 mx-auto w-10"
                      : "px-2 py-1 gap-2",
                    isActive
                      ? "bg-brand-100/80 text-brand-700"
                      : "text-muted-foreground hover:bg-accent-200/15 hover:text-foreground hover:translate-x-0.5"
                  )}
                  title={collapsed ? item.name : undefined}
                >
                  <span className={cn("transition-colors flex-shrink-0", isActive ? "text-brand-600" : "text-muted-foreground")}>{item.icon}</span>
                  {!collapsed && <span>{item.name}</span>}
                  {!collapsed && item.badgeKey && badgeCounts[item.badgeKey] > 0 ? (
                    <span className="ml-auto min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-md bg-brand-50 text-brand-600 text-[10px] font-semibold ring-1 ring-brand-200/60">
                      {badgeCounts[item.badgeKey]}
                    </span>
                  ) : !collapsed && isActive ? (
                    <span className="ml-auto w-0.5 h-4 bg-brand-500 rounded-full" />
                  ) : null}
                </Link>
              );
            })}
          </nav>

          {/* Projects */}
          {!collapsed && projects.length > 0 && (
            <div className="mt-2 flex flex-col">
              <div className="px-2.5 pb-1">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  Projects
                </span>
              </div>
              <div className="px-2 space-y-px">
                {projects.map((p) => (
                  <Link
                    key={p.project_id}
                    href={`/conversations?project=${p.project_id}`}
                    prefetch
                    onClick={onClose}
                    className="group flex items-center gap-1 px-2 py-1 text-[12px] rounded-lg transition-all duration-150 text-muted-foreground hover:text-foreground hover:bg-muted"
                  >
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color || '#94a3b8' }} />
                    <span className="truncate flex-1 min-w-0">{p.name}</span>
                    <span className="text-[10px] text-muted-foreground tabular-nums">{p.conversation_count || 0}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Pinned */}
          {!collapsed && pinnedConversations.length > 0 && (
            <div className="mt-2 flex flex-col">
              <div className="px-2.5 pb-1">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  Pinned
                </span>
              </div>
              <div className="px-2 space-y-px">
                {pinnedConversations.map((c) => {
                  const title = c.title || c.prompt?.slice(0, 50) || "Untitled";
                  const displayTitle = title.length > 32 ? title.slice(0, 32) + "..." : title;
                  const isConvoActive = activeContinueId === c.conversation_id;
                  return (
                    <div
                      key={c.conversation_id}
                      className={cn(
                        "group flex items-center gap-1 px-2 py-1 text-[12px] rounded-lg transition-all duration-150",
                        isConvoActive
                          ? "text-brand-700 bg-brand-100/80 font-medium"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted"
                      )}
                    >
                      <Link
                        href={`/chat?continue=${c.conversation_id}`}
                        prefetch
                        onClick={onClose}
                        className="truncate flex-1 min-w-0"
                        title={title}
                      >
                        {displayTitle}
                      </Link>
                      <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        <ChatContextMenu
                          conversationId={c.conversation_id}
                          conversationTitle={title}
                          isPinned={true}
                          projectId={c.project_id}
                          projects={projects}
                          onRename={async (id, newTitle) => { await renameConversation(id, newTitle); reloadConversations(); }}
                          onDelete={async (id) => { await removeConversation(id); reloadConversations(); }}
                          onTogglePin={togglePin}
                          onAssignProject={async (id, pid) => { await assignToProject(id, pid); reloadConversations(); }}
                          onRemoveProject={async (id) => { await unassignFromProject(id); reloadConversations(); }}
                          onCreateProject={async (name) => { await addProject(name); }}
                          triggerClassName="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Recents (excluding pinned) */}
          {!collapsed && myConversations.filter((c) => !isPinned(c.conversation_id)).length > 0 && (
            <div className="mt-2 flex-1 min-h-0 flex flex-col">
              <div className="px-2.5 pb-1">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  Recents
                </span>
              </div>
              <ScrollArea className="flex-1 px-2">
                <div className="space-y-px">
                  {myConversations.filter((c) => !isPinned(c.conversation_id)).map((c) => {
                    const title = c.title || c.prompt?.slice(0, 50) || "Untitled";
                    const displayTitle = title.length > 36 ? title.slice(0, 36) + "..." : title;
                    const isConvoActive = activeContinueId === c.conversation_id;
                    return (
                      <div
                        key={c.conversation_id}
                        className={cn(
                          "group flex items-center gap-1 px-2 py-1 text-[12px] rounded-lg transition-all duration-150",
                          isConvoActive
                            ? "text-brand-700 bg-brand-100/80 font-medium"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted"
                        )}
                      >
                        <Link
                          href={`/chat?continue=${c.conversation_id}`}
                          prefetch
                          onClick={onClose}
                          className="truncate flex-1 min-w-0"
                          title={title}
                        >
                          {displayTitle}
                        </Link>
                        <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          <ChatContextMenu
                            conversationId={c.conversation_id}
                            conversationTitle={title}
                            isPinned={false}
                            projectId={c.project_id}
                            projects={projects}
                            onRename={async (id, newTitle) => { await renameConversation(id, newTitle); reloadConversations(); }}
                            onDelete={async (id) => { await removeConversation(id); reloadConversations(); }}
                            onTogglePin={togglePin}
                            onAssignProject={async (id, pid) => { await assignToProject(id, pid); reloadConversations(); }}
                            onRemoveProject={async (id) => { await unassignFromProject(id); reloadConversations(); }}
                            onCreateProject={async (name) => { await addProject(name); }}
                            triggerClassName="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            </div>
          )}

          {/* Bottom section: pool status, theme toggle, user */}
          <div className="mt-auto">
            {/* Pool status — expandable */}
            {poolStatus && <PoolStatusWidget poolStatus={poolStatus} collapsed={collapsed} />}

            {/* Theme toggle */}
            <div className="px-2.5 py-1">
              {collapsed ? (
                <div className="flex flex-col items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => {
                      const modes: Array<"light" | "system" | "dark"> = ["light", "system", "dark"];
                      const idx = modes.indexOf(theme);
                      setTheme(modes[(idx + 1) % modes.length]);
                    }}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="Toggle theme"
                  >
                    {theme === "light" ? <RiSunLine size={14} /> : theme === "dark" ? <RiMoonLine size={14} /> : <RiComputerLine size={14} />}
                  </Button>
                </div>
              ) : (
                <ToggleGroup
                  type="single"
                  value={theme}
                  onValueChange={(value) => {
                    if (value) setTheme(value as "light" | "system" | "dark");
                  }}
                  className="w-full bg-muted/50 rounded-lg p-0.5"
                  size="sm"
                >
                  <ToggleGroupItem
                    value="light"
                    aria-label="Light mode"
                    className="flex-1 flex items-center justify-center gap-1 text-[11px] font-medium data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow-sm"
                  >
                    <RiSunLine size={14} />
                    <span className="hidden sm:inline">Light</span>
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    value="system"
                    aria-label="System mode"
                    className="flex-1 flex items-center justify-center gap-1 text-[11px] font-medium data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow-sm"
                  >
                    <RiComputerLine size={14} />
                    <span className="hidden sm:inline">System</span>
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    value="dark"
                    aria-label="Dark mode"
                    className="flex-1 flex items-center justify-center gap-1 text-[11px] font-medium data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow-sm"
                  >
                    <RiMoonLine size={14} />
                    <span className="hidden sm:inline">Dark</span>
                  </ToggleGroupItem>
                </ToggleGroup>
              )}
            </div>

            {/* User section */}
            {status === "authenticated" && session?.user && (
              <>
                <Separator />
                <div className={cn("px-2.5 py-2", collapsed && "flex justify-center")}>
                  {collapsed ? (
                    <Avatar size="sm" className="w-8 h-8">
                      <AvatarFallback className="bg-brand-100 text-brand-700 text-sm font-medium">
                        {session.user.email?.charAt(0).toUpperCase() || "U"}
                      </AvatarFallback>
                    </Avatar>
                  ) : (
                    <div className="flex items-center gap-2 px-1">
                      <Avatar size="sm" className="w-8 h-8">
                        <AvatarFallback className="bg-brand-100 text-brand-700 text-sm font-medium">
                          {session.user.email?.charAt(0).toUpperCase() || "U"}
                        </AvatarFallback>
                      </Avatar>
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] font-medium text-foreground truncate">
                          {session.user.name || session.user.email?.split("@")[0]}
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            onClick={() => signOut({ callbackUrl: "/login" })}
                            className="text-[11px] text-muted-foreground hover:text-red-500 transition-colors h-auto p-0"
                          >
                            Sign out
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </>
  );

  return (
    <>
      {/* Mobile backdrop overlay */}
      {isOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/30 z-40 animate-fade-in"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "fixed top-0 left-0 h-screen flex flex-col z-50 transition-all duration-200 ease-out bg-muted",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0",
          collapsed ? "w-[56px]" : "w-[220px]"
        )}
      >
        {sidebarContent}
      </aside>
    </>
  );
}
