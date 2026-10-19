"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { useSession } from "next-auth/react";
import { fetchUnreadNotificationCount } from "./notifications-api";

const POLL_INTERVAL_MS = 15000;

interface NotificationsValue {
  /** Number of the user's notifications that are unread and not dismissed. */
  unreadCount: number;
  /** The first count has arrived (until then 0 means "unknown", not "none"). */
  loaded: boolean;
  refresh: () => void;
}

const NotificationsContext = createContext<NotificationsValue>({
  unreadCount: 0,
  loaded: false,
  refresh: () => {},
});

export function useNotifications() {
  return useContext(NotificationsContext);
}

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const [unreadCount, setUnreadCount] = useState(0);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUnreadCount(await fetchUnreadNotificationCount());
      setLoaded(true);
    } catch {
      // Transient poll failures keep the last known count.
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    refresh();
    const interval = setInterval(() => { if (!document.hidden) void refresh(); }, POLL_INTERVAL_MS);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [status, refresh]);

  return (
    <NotificationsContext.Provider value={{ unreadCount, loaded, refresh }}>
      {children}
    </NotificationsContext.Provider>
  );
}
