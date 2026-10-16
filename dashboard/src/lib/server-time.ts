/** A running conversation with no heartbeat for this long is treated as stopped. */
export const DEAD_RUN_AFTER_MS = 5 * 60 * 1000;

/**
 * Epoch ms for a backend timestamp. The API sends UTC datetimes without an
 * offset; JS would read those as local time (5.5 h off in IST).
 */
export function parseServerTime(value: string | null | undefined): number {
  if (!value) return NaN;
  // Only a date-time without an offset is the API's naive UTC; a date-only
  // value or one with an offset parses as written.
  const naive = value.includes("T") && !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  return Date.parse(naive ? `${value}Z` : value);
}

/** Compact age for lists: "now", "12m", "3h", "Yesterday", "Oct 14", "Oct 14, 2025". */
export function relativeAge(value: string | null | undefined, now: number = Date.now()): string | null {
  const at = parseServerTime(value);
  if (!Number.isFinite(at)) return null;
  const minutes = Math.floor((now - at) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h`;
  const date = new Date(at);
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (at >= startOfToday - 86_400_000) return "Yesterday";
  return date.toLocaleDateString(undefined, date.getFullYear() === today.getFullYear()
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

/** "1m 12s" / "14m" / "2h 5m" since a start time, for running work. */
export function elapsedSince(value: string | null | undefined, now: number = Date.now()): string | null {
  const at = parseServerTime(value);
  if (!Number.isFinite(at)) return null;
  const seconds = Math.max(0, Math.floor((now - at) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 10) return `${minutes}m ${seconds % 60}s`;
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** A run still marked running whose heartbeat has gone quiet (crash/OOM). */
export function isDeadRun(
  status: string | undefined,
  lastHeartbeat: string | null | undefined,
  now: number = Date.now(),
): boolean {
  const heartbeat = parseServerTime(lastHeartbeat);
  return status === "running" && Number.isFinite(heartbeat) && now - heartbeat > DEAD_RUN_AFTER_MS;
}
