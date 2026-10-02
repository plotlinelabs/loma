"use client";

import { useEffect, useState, useRef } from "react";
import { useSession, signOut } from "next-auth/react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { fetchPoolStatus } from "../lib/api";
import type { PoolStatus } from "../lib/api";
import { useUser } from "../lib/UserContext";
import { useNotifications } from "../lib/NotificationsContext";
import type { SystemRole } from "../lib/governance-api";
import PetCompanion, { usePetSettings } from "./PetCompanion";
import CrosscutIcon from "./CrosscutIcon";
import { NavBoards, NAV_ACTIVE, NAV_IDLE, NAV_ROW } from "./NavBoards";
import { useTheme } from "../lib/ThemeContext";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  RiRobot2Line,
  RiGridLine,
  RiPlug2Line,
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
  RiExpandUpDownLine,
  RiNotification3Line,
} from "@remixicon/react";

type NavItem = {
  name: string;
  href: string;
  icon: React.ReactNode;
  /** Minimum system role required to see this nav item */
  minRole?: SystemRole;
};

/** Below Tasks and its boards. The nav is kept to Tasks and Flows; everything
 * else lives in the account menu. */
const navigation: NavItem[] = [
  {
    name: "Flows",
    href: "/flows",
    minRole: "analyst",
    icon: <RiTimeLine size={16} />,
  },
];

const userMenuNav: NavItem[] = [
  { name: "Agents", href: "/agents", icon: <RiRobot2Line size={16} /> },
  { name: "Activity", href: "/conversations", icon: <RiGridLine size={16} /> },
  { name: "Integrations", href: "/integrations/manage", icon: <RiPlug2Line size={16} /> },
  { name: "Skills", href: "/skills", minRole: "analyst", icon: <RiBookOpenLine size={16} /> },
  { name: "Webhook Logs", href: "/webhook-logs", minRole: "analyst", icon: <RiDownloadLine size={16} /> },
  { name: "Analytics", href: "/analytics", minRole: "analyst", icon: <RiBarChartBoxLine size={16} /> },
  { name: "Admin", href: "/admin", minRole: "maintainer", icon: <RiShieldCheckLine size={16} /> },
];

/** Notifications bell beside the logo, with the unread count. */
function NotificationBell({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  const { unreadCount } = useNotifications();
  const active = pathname.startsWith("/notifications");
  const label = unreadCount > 0 ? `Notifications (${unreadCount} unread)` : "Notifications";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href="/notifications"
          prefetch
          onClick={onNavigate}
          aria-label={label}
          aria-current={active ? "page" : undefined}
          className={cn(
            "relative flex h-7 w-7 items-center justify-center rounded-md outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring max-md:h-9 max-md:w-9",
            active ? "bg-sidebar-accent text-sidebar-primary" : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
          )}
        >
          <RiNotification3Line size={16} className="max-md:h-5 max-md:w-5" />
          {unreadCount > 0 && (collapsed ? (
            <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-sidebar" />
          ) : (
            <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-none text-white tabular-nums">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          ))}
        </Link>
      </TooltipTrigger>
      <TooltipContent side={collapsed ? "right" : "bottom"}>{label}</TooltipContent>
    </Tooltip>
  );
}

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
  const codex = poolStatus.codex;
  const codexAccountCount = codex?.accounts?.length || 0;
  const codexColor = !codex?.enabled || codexAccountCount === 0
    ? "bg-gray-300"
    : (codex.queue_depth || 0) > 0
      ? "bg-red-500"
      : (codex.available || 0) > 0
        ? "bg-brand-400/80"
        : (codex.warming || 0) > 0
          ? "bg-amber-400 animate-pulse"
          : "bg-red-500";
  const opencodeColor = !opencode?.enabled
    ? "bg-gray-300"
    : opencode.total_available > 0
      ? "bg-brand-400/80"
      : opencode.total_warming > 0
        ? "bg-amber-400 animate-pulse"
        : "bg-red-500";

  const statusColor = poolStatus.queue_depth > 0
    ? "bg-red-500"
    : poolStatus.available > 0
      ? "bg-brand-400/80"
      : poolStatus.warming > 0
        ? "bg-amber-400 animate-pulse"
        : "bg-red-500";

  if (collapsed) {
    return (
      <div className="px-2.5 py-1 flex flex-col items-center gap-1 opacity-70">
        <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", statusColor)} />
        {opencode && (
          <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", opencodeColor)} />
        )}
        {codex?.enabled && (
          <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", codexColor)} />
        )}
      </div>
    );
  }

  return (
    <div className="px-3 py-1 overflow-hidden opacity-75 transition-opacity hover:opacity-100">
      <Button
        variant="ghost"
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left group flex-col items-stretch gap-1.5 h-auto px-0 rounded-none overflow-hidden"
      >
        <div className="flex items-center gap-2">
          <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", statusColor)} />
          <span className="text-[10px] text-sidebar-foreground/70 flex-1 truncate">
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
            <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", opencodeColor)} />
            <span className="text-[10px] text-sidebar-foreground/70 flex-1 truncate">
              {opencode.enabled
                ? `OpenCode · ${opencode.total_available}/${opencode.pool_size} warm${opencode.total_warming ? ` · ${opencode.total_warming} warming` : ""}`
                : "OpenCode · warm pool off"}
            </span>
          </div>
        )}
        {codex?.enabled && (
          <div className="flex items-center gap-2">
            <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", codexColor)} />
            <span className="text-[10px] text-sidebar-foreground/70 flex-1 truncate">
              {codexAccountCount === 0
                ? "Codex · no accounts"
                : (codex.queue_depth || 0) > 0
                  ? `Codex · queued (${codex.queue_depth})`
                  : `Codex · ${codex.available}/${codex.pool_size} available`}
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
          {codex?.enabled && codexAccountCount > 0 && (
            <div className="space-y-0.5">
              <div className="text-[10px] text-muted-foreground mb-1">
                Codex · {codexAccountCount} account{codexAccountCount !== 1 ? "s" : ""} · {codex.in_use || 0} busy{(codex.warming || 0) > 0 ? ` · ${codex.warming} warming` : ""}
              </div>
              {(codex.accounts || []).map((email) => {
                const count = (codex.account_distribution || {})[email] || 0;
                const onCooldown = codex.accounts_on_cooldown?.includes(email);
                return (
                  <div key={email} className="flex items-center gap-1.5 text-[10px]">
                    {onCooldown ? (
                      <span className="text-amber-500" title="Usage limit — cooldown">!</span>
                    ) : (
                      <span className="text-muted-foreground">
                        {Array.from({ length: count }, (_, i) => (
                          <span key={i} className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 mr-0.5" />
                        ))}
                      </span>
                    )}
                    <span className={onCooldown ? "text-amber-600 line-through" : "text-muted-foreground"}>
                      {email}
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
  const { loading: userLoading, hasRole } = useUser();
  const { theme, setTheme } = useTheme();
  const pathname = usePathname();
  const [poolStatus, setPoolStatus] = useState<PoolStatus | null>(null);

  // Filter nav items by role
  const visibleNav = navigation.filter((item) => !item.minRole || hasRole(item.minRole));

  // Close sidebar on route change (mobile)
  useEffect(() => {
    onClose();
  }, [pathname]);

  // Poll pool status every 5 seconds
  useEffect(() => {
    const poll = () => fetchPoolStatus().then(setPoolStatus).catch(() => {});
    poll();
    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
  }, []);

  const openPetSettings = usePetSettings();

  // Phone drawer: lock the page behind it, close on Escape and on a leftward swipe.
  useBodyScrollLock(isOpen);
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      // A dialog or menu opened from the drawer (rename, pet settings,
      // conversation actions, account menu) owns Escape. Radix dismisses in
      // the capture phase and calls preventDefault(); by the time this
      // bubble-phase listener runs the layer is already `data-state=closed`,
      // so a DOM query alone cannot tell. `defaultPrevented` can.
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector('[role="dialog"][data-state="open"], [role="menu"][data-state="open"]')) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0];
    swipeStart.current = { x: t.clientX, y: t.clientY };
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    const start = swipeStart.current;
    swipeStart.current = null;
    if (!start || !isOpen) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (dx < -60 && Math.abs(dx) > Math.abs(dy) * 1.5) onClose();
  };

  const sidebarContent = (
    <>
      {/* Logo, notifications bell, collapse toggle and (phones) close button */}
      <div className={cn("flex shrink-0 items-center justify-between", collapsed ? "flex-col gap-1 px-2 pt-2 pb-2" : "px-5 pt-6 pb-4")}>
        <div className={cn("flex items-center gap-2", collapsed && "justify-center")}>
          <PetCompanion size={32} onOpen={onClose} fallback={<Link href="/tasks" prefetch onClick={onClose} aria-label="Loma home"><CrosscutIcon size={collapsed ? 22 : 20} /></Link>} />
          {!collapsed && (
            <Link href="/tasks" prefetch onClick={onClose} className="font-[family-name:var(--font-logo)] text-base font-bold tracking-[0.5px] text-foreground/80">
              Loma
            </Link>
          )}
        </div>
        <div className={cn("flex items-center gap-0.5", collapsed && "flex-col")}>
          <NotificationBell collapsed={collapsed} onNavigate={onClose} />
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
          {/* Tasks with its boards, then Flows. `min-h-0` lets the region
              shrink and scroll on short screens (and long board lists)
              instead of pushing the account row off-screen. */}
          <ScrollArea className="flex-1 min-h-0">
          <nav aria-label="Main" className="px-3 space-y-0.5 pb-2">
            <NavBoards collapsed={collapsed} onNavigate={onClose} />
            {visibleNav.map((item) => {
              const isActive = pathname.startsWith(item.href);
              const link = (
                <Link
                  key={item.name}
                  href={item.href}
                  prefetch
                  onClick={onClose}
                  className={cn(
                    NAV_ROW,
                    collapsed
                      ? "justify-center px-0 py-1.5 mx-auto w-10"
                      : "px-3 py-2 gap-2.5 max-md:py-2.5 max-md:gap-3",
                    isActive ? NAV_ACTIVE : NAV_IDLE,
                  )}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={collapsed ? item.name : undefined}
                >
                  <span className={cn("relative flex-shrink-0 transition-colors", isActive ? "text-sidebar-primary" : "text-current")}>
                    {item.icon}
                  </span>
                  {!collapsed && <span>{item.name}</span>}
                </Link>
              );
              return collapsed ? (
                <Tooltip key={item.name}>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side="right">{item.name}</TooltipContent>
                </Tooltip>
              ) : link;
            })}
          </nav>
          </ScrollArea>

          {/* Bottom section: pool status, theme toggle, user */}
          <div className="mt-auto shrink-0">
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

            {/* User section with dropdown */}
            {status === "authenticated" && session?.user && (
              <>
                <Separator />
                <div className={cn("px-2.5 py-2", collapsed && "flex justify-center")}>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button aria-label="Account menu" className={cn(
                        "flex items-center w-full rounded-lg transition-colors hover:bg-muted",
                        collapsed ? "justify-center p-1" : "gap-2 px-1 py-1"
                      )}>
                        <Avatar size="sm" className="w-7 h-7 shrink-0">
                          <AvatarFallback className="bg-sidebar-primary/90 text-sidebar-primary-foreground text-xs font-medium">
                            {session.user.email?.charAt(0).toUpperCase() || "U"}
                          </AvatarFallback>
                        </Avatar>
                        {!collapsed && (
                          <>
                            <div className="min-w-0 flex-1 text-left">
                              <div className="text-xs font-medium text-foreground truncate">
                                {session.user.name || session.user.email?.split("@")[0]}
                              </div>
                              <div className="text-[10px] text-muted-foreground truncate">
                                {session.user.email}
                              </div>
                            </div>
                            <RiExpandUpDownLine size={14} className="text-muted-foreground shrink-0" />
                          </>
                        )}
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent side="top" align="start" className="w-[200px]">
                      {userMenuNav.filter((item) => !item.minRole || hasRole(item.minRole)).map((item) => (
                        <DropdownMenuItem key={item.href} asChild>
                          <Link href={item.href} onClick={onClose} className="flex items-center gap-2 text-[13px]">
                            {item.icon}
                            {item.name}
                          </Link>
                        </DropdownMenuItem>
                      ))}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => { openPetSettings(); onClose(); }} className="text-[13px]">
                        <RiSettings3Line size={16} />Pet settings
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onClick={() => signOut({ callbackUrl: "/login" })}
                        className="text-red-600 focus:text-red-600 text-[13px]"
                      >
                        Sign out
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
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
        aria-label="Navigation"
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
        className={cn(
          // Safe-area padding on both ends so the brand row clears the notch
          // and the account row clears the iPhone home indicator.
          "loma-sidebar fixed top-0 left-0 h-dvh pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] flex flex-col z-50 transition-all duration-200 ease-out bg-sidebar",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0",
          collapsed ? "w-[56px]" : "w-[300px] md:w-[220px]"
        )}
      >
        {sidebarContent}
      </aside>
    </>
  );
}
