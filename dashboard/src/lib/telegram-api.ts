/**
 * Telegram API client — typed fetch wrappers for the Telegram personal channel.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

async function apiError(res: Response, fallback: string): Promise<Error> {
  try {
    const data = await res.json();
    if (typeof data?.error === "string" && data.error.trim()) {
      return new Error(data.error);
    }
  } catch {
    // Fall through to the generic status message.
  }
  return new Error(`${fallback}: ${res.status}`);
}

// ── Types ─────────────────────────────────────────────────────────────────

export interface TelegramStatus {
  configured: boolean;
  linked: boolean;
  bot_username: string | null;
  telegram_username: string | null;
  linked_at: string | null;
}

export interface TelegramLink {
  deep_link: string;
  bot_username: string;
  expires_in_minutes: number;
}

// ── API calls ─────────────────────────────────────────────────────────────

export async function fetchTelegramStatus(): Promise<TelegramStatus> {
  const res = await fetch(`${API_BASE}/api/telegram/status`);
  if (!res.ok) throw await apiError(res, "Failed to fetch Telegram status");
  return res.json();
}

export async function createTelegramLink(): Promise<TelegramLink> {
  const res = await fetch(`${API_BASE}/api/telegram/link`, { method: "POST" });
  if (!res.ok) throw await apiError(res, "Failed to create Telegram link");
  return res.json();
}

export async function disconnectTelegram(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/telegram/link`, { method: "DELETE" });
  if (!res.ok) throw await apiError(res, "Failed to disconnect Telegram");
}
