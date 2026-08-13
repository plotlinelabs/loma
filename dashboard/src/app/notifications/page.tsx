"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  RiCheckDoubleLine,
  RiCloseLine,
  RiNotification3Line,
  RiRefreshLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import ClientTimestamp from "@/components/ClientTimestamp";
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

export default function NotificationsPage() {
  const router = useRouter();
  const { refresh: refreshUnreadCount } = useNotifications();
  const [notifications, setNotifications] = useState<LomaNotification[]>([]);
  const [loading, setLoading] = useState(true);

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

  const handleOpen = async (n: LomaNotification) => {
    if (!n.read) {
      setNotifications((prev) =>
        prev.map((x) => (x.notification_id === n.notification_id ? { ...x, read: true } : x)),
      );
      markNotificationRead(n.notification_id)
        .then(refreshUnreadCount)
        .catch(() => {});
    }
    if (n.conversation_id) {
      router.push(`${basePath}/chat?continue=${n.conversation_id}`);
    } else if (n.link) {
      window.open(n.link, "_blank", "noopener,noreferrer");
    }
  };

  const handleDismiss = async (n: LomaNotification) => {
    setNotifications((prev) => prev.filter((x) => x.notification_id !== n.notification_id));
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
          notifications.map((n, idx) => (
            <Card
              key={n.notification_id}
              className={cn(
                "p-3 active:bg-muted/50 transition-colors animate-fade-in-up",
                (n.conversation_id || n.link) && "cursor-pointer",
                !n.read && "bg-brand-50/40",
              )}
              style={{ animationDelay: `${Math.min(idx * 30, 300)}ms` }}
              onClick={() => handleOpen(n)}
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
                        "text-[13px] truncate",
                        n.read ? "font-normal text-foreground/80" : "font-medium text-foreground",
                      )}
                    >
                      {n.title}
                    </div>
                    {n.body && (
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                        {n.body}
                      </div>
                    )}
                    <div className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground">
                      <ClientTimestamp iso={n.created_at} variant="short" />
                      {n.conversation_id && <span>Open conversation →</span>}
                    </div>
                  </div>
                  <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => handleDismiss(n)}
                          className="text-muted-foreground hover:text-foreground"
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
          ))
        )}
      </div>
    </div>
  );
}
