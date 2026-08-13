"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  RiArrowDownSLine,
  RiChat1Line,
  RiCheckDoubleLine,
  RiCloseLine,
  RiExternalLinkLine,
  RiNotification3Line,
  RiRefreshLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import ClientTimestamp from "@/components/ClientTimestamp";
import MarkdownContent from "@/components/MarkdownContent";
import { cn } from "@/lib/utils";
import { basePath } from "@/lib/api";
import {
  LomaNotification,
  dismissNotification,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/notifications-api";
import { useNotifications } from "@/lib/NotificationsContext";


// ── Body rendering helpers ─────────────────────────────────────────────────

/**
 * Wrap bare URLs in <url> (Slack auto-link form, supported by MarkdownContent)
 * so plain-text notifications from older flows still get clickable links.
 * URLs already inside markdown links [label](url) or <url> are left alone.
 */
function autolinkBareUrls(text: string): string {
  return text.replace(
    /(^|[^("'<\]])(https?:\/\/[^\s<>)"']+)/g,
    (_m, prefix, url) => `${prefix}<${url}>`,
  );
}

/**
 * Strip markdown syntax down to plain text for the collapsed 2-line preview.
 */
function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ") // fenced code blocks
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1") // images -> alt
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links -> label
    .replace(/<(https?:\/\/[^|>]+)\|([^>]+)>/g, "$2") // slack links -> label
    .replace(/\*+/g, "") // bold/italic markers
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$)/g, "$1$2") // _emphasis_ (word-internal _ kept)
    .replace(/[~`#>]+/g, "") // strikethrough, code ticks, headings, quotes
    .replace(/^[-•]\s+/gm, "") // bullet markers
    .replace(/\n{2,}/g, " · ")
    .replace(/\s+/g, " ")
    .trim();
}

export default function NotificationsPage() {
  const router = useRouter();
  const { refresh: refreshUnreadCount } = useNotifications();
  const [notifications, setNotifications] = useState<LomaNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setNotifications(await fetchNotifications({ limit: 100 }));
    } catch (e) {
      console.error("Failed to load notifications:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const markRead = (n: LomaNotification) => {
    if (n.read) return;
    setNotifications((prev) =>
      prev.map((x) => (x.notification_id === n.notification_id ? { ...x, read: true } : x)),
    );
    markNotificationRead(n.notification_id)
      .then(refreshUnreadCount)
      .catch(() => {});
  };

  // Clicking a card expands it in place (and marks it read) — it never
  // navigates. Opening the conversation/link is an explicit button action.
  const handleToggle = (n: LomaNotification) => {
    markRead(n);
    setExpandedId((prev) => (prev === n.notification_id ? null : n.notification_id));
  };

  const handleOpenConversation = (n: LomaNotification) => {
    markRead(n);
    router.push(`${basePath}/chat?continue=${n.conversation_id}`);
  };

  const handleOpenLink = (n: LomaNotification) => {
    markRead(n);
    window.open(n.link!, "_blank", "noopener,noreferrer");
  };

  const handleDismiss = async (n: LomaNotification) => {
    setNotifications((prev) => prev.filter((x) => x.notification_id !== n.notification_id));
    if (expandedId === n.notification_id) setExpandedId(null);
    try {
      await dismissNotification(n.notification_id);
      refreshUnreadCount();
    } catch (e) {
      console.error("Failed to dismiss notification:", e);
      loadData();
    }
  };

  const handleMarkAllRead = async () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    try {
      await markAllNotificationsRead();
      refreshUnreadCount();
    } catch (e) {
      console.error("Failed to mark all read:", e);
      loadData();
    }
  };

  const hasUnread = notifications.some((n) => !n.read);

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">
      {/* Header */}
      <div className="pwa-header-offset flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">Notifications</h1>
        <div className="flex items-center gap-1">
          {hasUnread && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleMarkAllRead}
                  className="text-muted-foreground hover:text-foreground press-scale h-8 w-8 p-0"
                >
                  <RiCheckDoubleLine size={16} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Mark all as read</TooltipContent>
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

      {/* List */}
      <div className="space-y-2">
        {loading ? (
          <>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </>
        ) : notifications.length === 0 ? (
          <EmptyState
            icon={RiNotification3Line}
            title="No notifications"
            description="Flows and long-running tasks will leave their results here"
          />
        ) : (
          notifications.map((n, idx) => {
            const expanded = expandedId === n.notification_id;
            const hasActions = Boolean(n.conversation_id || n.link);
            return (
              <Card
                key={n.notification_id}
                className={cn(
                  "p-3 active:bg-muted/50 transition-colors animate-fade-in-up cursor-pointer",
                  !n.read && "bg-brand-50/40",
                )}
                style={{ animationDelay: `${Math.min(idx * 30, 300)}ms` }}
                onClick={() => handleToggle(n)}
                aria-expanded={expanded}
              >
                <CardContent className="p-0">
                  <div className="flex items-start gap-2.5">
                    <div
                      className={cn(
                        "h-2 w-2 rounded-full mt-1.5 shrink-0",
                        n.read ? "bg-transparent" : "bg-brand-500",
                      )}
                    />
                    <div className="flex-1 min-w-0">
                      <div
                        className={cn(
                          "text-[13px]",
                          expanded ? "break-words" : "truncate",
                          n.read ? "font-normal text-foreground/80" : "font-medium text-foreground",
                        )}
                      >
                        {n.title}
                      </div>
                      {n.body &&
                        (expanded ? (
                          <div className="mt-1 text-foreground/90 break-words">
                            <MarkdownContent content={autolinkBareUrls(n.body)} className="text-[13px]" />
                          </div>
                        ) : (
                          <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                            {stripMarkdown(n.body)}
                          </div>
                        ))}
                      <div className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground">
                        <ClientTimestamp iso={n.created_at} variant="short" />
                      </div>
                      {expanded && hasActions && (
                        <div
                          className="flex items-center gap-2 mt-2.5"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {n.conversation_id && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleOpenConversation(n)}
                              className="h-8 text-xs press-scale"
                            >
                              <RiChat1Line size={14} className="mr-1.5" />
                              Open conversation
                            </Button>
                          )}
                          {n.link && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleOpenLink(n)}
                              className="h-8 text-xs press-scale"
                            >
                              <RiExternalLinkLine size={14} className="mr-1.5" />
                              Open link
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="shrink-0 flex items-center" onClick={(e) => e.stopPropagation()}>
                      <RiArrowDownSLine
                        size={16}
                        className={cn(
                          "text-muted-foreground/60 transition-transform mt-1.5 mr-0.5",
                          expanded && "rotate-180",
                        )}
                        onClick={() => handleToggle(n)}
                      />
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            onClick={() => handleDismiss(n)}
                            className="text-muted-foreground hover:text-foreground h-8 w-8 md:h-6 md:w-6"
                            aria-label="Dismiss notification"
                          >
                            <RiCloseLine size={14} />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Dismiss</TooltipContent>
                      </Tooltip>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </div>
  );
}
