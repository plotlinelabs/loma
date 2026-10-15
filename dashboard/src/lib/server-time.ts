/** A running conversation with no heartbeat for this long is treated as stopped. */
export const DEAD_RUN_AFTER_MS = 5 * 60 * 1000;

/**
 * Epoch ms for a backend timestamp. The API sends UTC datetimes without an
 * offset; JS would read those as local time (5.5 h off in IST).
 */
export function parseServerTime(value: string | null | undefined): number {
  if (!value) return NaN;
  const hasOffset = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  return Date.parse(hasOffset ? value : `${value}Z`);
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
