// CSRF guard for state-changing dashboard routes.
// Behind nginx, request.url is the dashboard's internal address, so compare the
// browser's Origin with the public host the proxy forwards (X-Forwarded-Host / Host),
// or with the configured AUTH_URL when the public address uses a non-default port.
export function isSameOrigin(request: Request): boolean {
  let origin: URL;
  try { origin = new URL(request.headers.get("origin") || ""); } catch { return false; }
  try { if (process.env.AUTH_URL && new URL(process.env.AUTH_URL).origin === origin.origin) return true; } catch { /* ignore a malformed AUTH_URL */ }
  const host = (request.headers.get("x-forwarded-host") || request.headers.get("host") || "").split(",")[0].trim().toLowerCase();
  return host !== "" && host === origin.host.toLowerCase();
}
