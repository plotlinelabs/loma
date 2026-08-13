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

const POLL_INTERVAL_MS = 5000;

interface NotificationsValue {
  /** Number of the user's notifications that are unread and not dismissed. */
  unreadCount: number;
  refresh: () => void;
}

const NotificationsContext = createContext<NotificationsValue>({
  unreadCount: 0,
  refresh: () => {},
});

export function useNotifications() {
  return useContext(NotificationsContext);
}

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const [unreadCount, setUnreadCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setUnreadCount(await fetchUnreadNotificationCount());
    } catch {
      // Transient poll failures keep the last known count.
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [status, refresh]);

  return (
    <NotificationsContext.Provider value={{ unreadCount, refresh }}>
      {children}
    </NotificationsContext.Provider>
  );
}
