/**
 * Notifications API client — typed fetch wrappers for the notification inbox.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

// ── Types ─────────────────────────────────────────────────────────────────

export interface LomaNotification {
  notification_id: string;
  user_email: string;
  title: string;
  body: string;
  conversation_id?: string | null;
  flow_id?: string | null;
  link?: string | null;
  source: string;
  read: boolean;
  dismissed: boolean;
  created_at: string;
  read_at?: string;
  dismissed_at?: string;
}

// ── Fetchers ──────────────────────────────────────────────────────────────

export async function fetchNotifications(
  options: { includeDismissed?: boolean; limit?: number } = {},
): Promise<LomaNotification[]> {
  const params = new URLSearchParams();
  if (options.includeDismissed) params.set("include_dismissed", "1");
  if (options.limit) params.set("limit", String(options.limit));
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/notifications${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Failed to fetch notifications: ${res.status}`);
  const data = await res.json();
  return data.notifications ?? [];
}

export async function fetchUnreadNotificationCount(): Promise<number> {
  const res = await fetch(`${API_BASE}/api/notifications/unread-count`);
  if (!res.ok) throw new Error(`Failed to fetch unread count: ${res.status}`);
  const data = await res.json();
  return data.count ?? 0;
}

export async function markNotificationRead(notificationId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/notifications/${notificationId}/read`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to mark notification read: ${res.status}`);
}

export async function markAllNotificationsRead(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/notifications/read-all`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to mark all read: ${res.status}`);
}

export async function dismissNotification(notificationId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/notifications/${notificationId}/dismiss`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to dismiss notification: ${res.status}`);
}
