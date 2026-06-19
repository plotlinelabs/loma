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
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 0 1 .865-.501 48.172 48.172 0 0 0 3.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
      </svg>
    ),
  },
  {
    name: "Activity",
    href: "/conversations",
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25a2.25 2.25 0 0 1-2.25-2.25v-2.25Z" />
      </svg>
    ),
  },
  {
    name: "Flows",
    href: "/flows",
    minRole: "analyst",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
      </svg>
    ),
  },
  {
    name: "Skills",
    href: "/skills",
    minRole: "analyst",
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25A8.966 8.966 0 0 1 18 3.75c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
      </svg>
    ),
  },
  {
    name: "Webhook Logs",
    href: "/webhook-logs",
    minRole: "analyst",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 7.5h-.75A2.25 2.25 0 0 0 4.5 9.75v7.5a2.25 2.25 0 0 0 2.25 2.25h7.5a2.25 2.25 0 0 0 2.25-2.25v-7.5a2.25 2.25 0 0 0-2.25-2.25h-.75m-6 3.75 3 3m0 0 3-3m-3 3V1.5m6 9h.75a2.25 2.25 0 0 1 2.25 2.25v7.5a2.25 2.25 0 0 1-2.25 2.25h-7.5a2.25 2.25 0 0 1-2.25-2.25v-7.5a2.25 2.25 0 0 1 2.25-2.25H9" />
      </svg>
    ),
  },
  {
    name: "Analytics",
    href: "/analytics",
    minRole: "analyst",
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
      </svg>
    ),
  },
  {
    name: "Manage Integrations",
    href: "/integrations/manage",
    minRole: "maintainer",
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.343 3.94c.09-.542.56-.94 1.11-.94h1.093c.55 0 1.02.398 1.11.94l.149.894c.07.424.384.764.78.93.398.164.855.142 1.205-.108l.737-.527a1.125 1.125 0 0 1 1.45.12l.773.774c.39.389.44 1.002.12 1.45l-.527.737c-.25.35-.272.806-.107 1.204.165.397.505.71.93.78l.893.15c.543.09.94.56.94 1.109v1.094c0 .55-.397 1.02-.94 1.11l-.893.149c-.425.07-.765.383-.93.78-.165.398-.143.854.107 1.204l.527.738c.32.447.269 1.06-.12 1.45l-.774.773a1.125 1.125 0 0 1-1.449.12l-.738-.527c-.35-.25-.806-.272-1.203-.107-.397.165-.71.505-.781.929l-.149.894c-.09.542-.56.94-1.11.94h-1.094c-.55 0-1.019-.398-1.11-.94l-.148-.894c-.071-.424-.384-.764-.781-.93-.398-.164-.854-.142-1.204.108l-.738.527c-.447.32-1.06.269-1.45-.12l-.773-.774a1.125 1.125 0 0 1-.12-1.45l.527-.737c.25-.35.273-.806.108-1.204-.165-.397-.505-.71-.93-.78l-.894-.15c-.542-.09-.94-.56-.94-1.109v-1.094c0-.55.398-1.02.94-1.11l.894-.149c.424-.07.765-.383.93-.78.165-.398.143-.854-.108-1.204l-.526-.738a1.125 1.125 0 0 1 .12-1.45l.773-.773a1.125 1.125 0 0 1 1.45-.12l.737.527c.35.25.807.272 1.204.107.397-.165.71-.505.78-.929l.15-.894Z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
      </svg>
    ),
  },
  {
    name: "Admin",
    href: "/admin",
    minRole: "maintainer",
    icon: (
      <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
      </svg>
    ),
  },
];

function SidebarSkeleton() {
  const widths = [72, 80, 56, 64, 96, 72, 96, 88];
  return (
    <>
      <div className="px-3 space-y-0.5">
        {widths.map((w, i) => (
          <div key={i} className="flex items-center gap-2.5 px-3 py-2">
            <div className="w-[18px] h-[18px] rounded bg-gray-200/80 animate-pulse" />
            <div className="h-3.5 rounded bg-gray-200/80 animate-pulse" style={{ width: `${w}px` }} />
          </div>
        ))}
      </div>
      <div className="mt-5 px-4">
        <div className="h-2.5 w-12 rounded bg-gray-200/60 animate-pulse mb-3" />
        <div className="space-y-2 px-1">
          <div className="h-3.5 w-36 rounded bg-gray-200/60 animate-pulse" />
          <div className="h-3.5 w-28 rounded bg-gray-200/60 animate-pulse" />
        </div>
      </div>
      <div className="mt-auto border-t border-gray-200/60 px-3 py-3">
        <div className="flex items-center gap-2.5 px-1">
          <div className="w-8 h-8 rounded-full bg-gray-200/80 animate-pulse" />
          <div className="space-y-1.5 flex-1">
            <div className="h-3.5 w-28 rounded bg-gray-200/80 animate-pulse" />
            <div className="h-2.5 w-16 rounded bg-gray-200/60 animate-pulse" />
          </div>
        </div>
      </div>
    </>
  );
}

function PoolStatusWidget({ poolStatus }: { poolStatus: PoolStatus }) {
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

  return (
    <div className="px-4 py-2 mt-auto">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left group space-y-1.5"
      >
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
          <span className="text-[11px] text-gray-500 flex-1">
            {accountCount === 0
              ? "Claude · no accounts"
              : poolStatus.queue_depth > 0
                ? `Claude · queued (${poolStatus.queue_depth})`
                : `Claude · ${poolStatus.available}/${poolStatus.pool_size} available`}
          </span>
          <svg
            className={`w-3 h-3 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </div>
        {opencode && (
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${opencodeColor}`} />
            <span className="text-[11px] text-gray-500 flex-1">
              {opencode.enabled
                ? `OpenCode · ${opencode.total_available}/${opencode.pool_size} warm${opencode.total_warming ? ` · ${opencode.total_warming} warming` : ""}`
                : "OpenCode · warm pool off"}
            </span>
          </div>
        )}
      </button>
      {expanded && (
        <div className="mt-2 pl-4 space-y-2">
          {accountCount > 0 && (
            <div className="space-y-0.5">
              <div className="text-[10px] text-gray-400 mb-1">
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
                      <span className="text-gray-300">
                        {Array.from({ length: count }, (_, i) => (
                          <span key={i} className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 mr-0.5" />
                        ))}
                      </span>
                    )}
                    <span className={onCooldown ? "text-amber-600 line-through" : "text-gray-500"}>
                      {displayEmail}
                    </span>
                    {count > 0 && (
                      <span className="text-gray-400 ml-auto">{count}</span>
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
              <div className="text-[10px] text-gray-400 mb-1">
                OpenCode · {opencode.active_sessions} active session{opencode.active_sessions === 1 ? "" : "s"}
              </div>
              {opencode.models.map((model) => (
                <div key={model.model} className="flex items-center gap-1.5 text-[10px] text-gray-500">
                  <span className={`w-1.5 h-1.5 rounded-full ${model.available > 0 ? "bg-emerald-400" : model.warming > 0 ? "bg-amber-400 animate-pulse" : "bg-gray-300"}`} />
                  <span className="truncate">{model.model}</span>
                  <span className="text-gray-400 ml-auto">
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

export default function Sidebar({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
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
        className={`fixed top-0 left-0 h-screen w-[260px] bg-gray-100 flex flex-col z-50 transition-transform duration-200 ease-out ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0`}
      >
        {/* Logo + close button */}
        <div className="px-5 pt-5 pb-4 flex items-center justify-between">
          <Link href="/" prefetch onClick={onClose} className="flex items-center gap-2.5">
            <CrosscutIcon size={28} />
            <span className="font-[family-name:var(--font-logo)] text-2xl font-black tracking-[1px] text-gray-700">
              Loma
            </span>
          </Link>
          <button
            onClick={onClose}
            className="md:hidden p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-200/60 transition-colors"
            aria-label="Close menu"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
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
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150 ${
                      isActive
                        ? "bg-brand-100/80 text-brand-700"
                        : "text-gray-600 hover:bg-accent-200/15 hover:text-gray-900 hover:translate-x-0.5"
                    }`}
                  >
                    <span className={`transition-colors ${isActive ? "text-brand-600" : "text-gray-400"}`}>{item.icon}</span>
                    {item.name}
                    {item.badgeKey && badgeCounts[item.badgeKey] > 0 ? (
                      <span className="ml-auto min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-md bg-brand-50 text-brand-600 text-[10px] font-semibold ring-1 ring-brand-200/60">
                        {badgeCounts[item.badgeKey]}
                      </span>
                    ) : isActive ? (
                      <span className="ml-auto w-0.5 h-4 bg-brand-500 rounded-full" />
                    ) : null}
                  </Link>
                );
              })}
            </nav>

            {/* Projects */}
            {projects.length > 0 && (
              <div className="mt-5 flex flex-col">
                <div className="px-4 pb-2">
                  <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
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
                      className="group flex items-center gap-1.5 px-3 py-1.5 text-[13px] rounded-lg transition-all duration-150 text-gray-500 hover:text-gray-800 hover:bg-gray-200/50"
                    >
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color || '#94a3b8' }} />
                      <span className="truncate flex-1 min-w-0">{p.name}</span>
                      <span className="text-[10px] text-gray-400 tabular-nums">{p.conversation_count || 0}</span>
                    </Link>
                  ))}
                </div>
              </div>
            )}

            {/* Pinned */}
            {pinnedConversations.length > 0 && (
              <div className="mt-5 flex flex-col">
                <div className="px-4 pb-2">
                  <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
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
                        className={`group flex items-center gap-1.5 px-3 py-1.5 text-[13px] rounded-lg transition-all duration-150 ${
                          isConvoActive
                            ? "text-brand-700 bg-brand-100/80 font-medium"
                            : "text-gray-500 hover:text-gray-800 hover:bg-gray-200/50"
                        }`}
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
                            triggerClassName="p-0.5 rounded text-gray-400 hover:text-gray-600 transition-colors"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Recents (excluding pinned) */}
            {myConversations.filter((c) => !isPinned(c.conversation_id)).length > 0 && (
              <div className="mt-5 flex-1 min-h-0 flex flex-col">
                <div className="px-4 pb-2">
                  <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                    Recents
                  </span>
                </div>
                <div className="flex-1 overflow-y-auto px-2 space-y-px">
                  {myConversations.filter((c) => !isPinned(c.conversation_id)).map((c) => {
                    const title = c.title || c.prompt?.slice(0, 50) || "Untitled";
                    const displayTitle = title.length > 36 ? title.slice(0, 36) + "..." : title;
                    const isConvoActive = activeContinueId === c.conversation_id;
                    return (
                      <div
                        key={c.conversation_id}
                        className={`group flex items-center gap-1.5 px-3 py-1.5 text-[13px] rounded-lg transition-all duration-150 ${
                          isConvoActive
                            ? "text-brand-700 bg-brand-100/80 font-medium"
                            : "text-gray-500 hover:text-gray-800 hover:bg-gray-200/50"
                        }`}
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
                            triggerClassName="p-0.5 rounded text-gray-400 hover:text-gray-600 transition-colors"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Pool status — expandable */}
            {poolStatus && <PoolStatusWidget poolStatus={poolStatus} />}

            {/* Theme toggle */}
            <div className="px-4 py-2 mt-auto">
              <div className="flex items-center gap-1 bg-gray-200/50 rounded-lg p-0.5">
                {([
                  { key: "light" as const, label: "Light", icon: (
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1.5M18.364 5.636l-1.06 1.06M21 12h-1.5M18.364 18.364l-1.06-1.06M12 19.5V21M7.757 17.303l-1.06 1.06M4.5 12H3M7.757 6.697l-1.06-1.06" />
                      <circle cx="12" cy="12" r="4" />
                    </svg>
                  )},
                  { key: "system" as const, label: "System", icon: (
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 0 1-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0 1 15 18.257V17.25m6-12V15a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 15V5.25A2.25 2.25 0 0 1 5.25 3h13.5A2.25 2.25 0 0 1 21 5.25Z" />
                    </svg>
                  )},
                  { key: "dark" as const, label: "Dark", icon: (
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
                    </svg>
                  )},
                ] as const).map(({ key, label, icon }) => (
                  <button
                    key={key}
                    onClick={() => setTheme(key)}
                    className={`flex-1 flex items-center justify-center gap-1 py-1 rounded-md text-[11px] font-medium transition-all duration-150 ${
                      theme === key
                        ? "bg-surface text-gray-700 shadow-sm"
                        : "text-gray-400 hover:text-gray-600"
                    }`}
                    title={label}
                    aria-label={`${label} mode`}
                  >
                    {icon}
                    <span className="hidden sm:inline">{label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* User section */}
            {status === "authenticated" && session?.user && (
              <div className="border-t border-gray-200/60 px-3 py-3">
                <div className="flex items-center gap-2.5 px-1">
                  <div className="w-8 h-8 bg-brand-100 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-sm font-medium text-brand-700">
                      {session.user.email?.charAt(0).toUpperCase() || "U"}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-gray-800 truncate">
                      {session.user.name || session.user.email?.split("@")[0]}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => signOut({ callbackUrl: "/login" })}
                        className="text-[11px] text-gray-400 hover:text-red-500 transition-colors"
                      >
                        Sign out
                      </button>
                      <span className="text-gray-300">·</span>
                      <a
                        href="/brand-guide.html"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-gray-400 hover:text-gray-600 transition-colors"
                      >
                        Brand
                      </a>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </aside>
    </>
  );
}
