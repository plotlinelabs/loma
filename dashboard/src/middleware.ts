import { auth } from "./auth";
import { NextResponse } from "next/server";
import { signProxyIdentity } from "./lib/proxy-identity";

// Paths under /api that must NOT require auth or get the X-User-Email header.
// /api/whoami is the nginx auth_request target; its own handler reads the session.
const PUBLIC_API_PREFIXES = ["/api/auth", "/api/signup", "/api/whoami"];
// Backend-only paths the dashboard never needs to gate (also routed straight to
// the backend by the reverse proxy).
const BYPASS_PREFIXES = ["/webhook", "/metrics"];
// PWA/browser assets fetched WITHOUT cookies (manifest requests are
// uncredentialed; icon fetches during install too). Auth-gating these would
// 307 them to /login and silently break Add to Home Screen on iOS.
const PUBLIC_ASSET_PREFIXES = ["/manifest.webmanifest", "/icons/", "/sw.js", "/favicon"];

export default auth(async (req) => {
  const { pathname } = req.nextUrl;

  if (
    BYPASS_PREFIXES.some((p) => pathname.startsWith(p)) ||
    PUBLIC_API_PREFIXES.some((p) => pathname.startsWith(p)) ||
    PUBLIC_ASSET_PREFIXES.some((p) => pathname.startsWith(p))
  ) {
    return NextResponse.next();
  }

  const isApi = pathname.startsWith("/api/");

  if (!req.auth?.user) {
    // API calls get 401 (don't redirect XHR/fetch to the HTML login page);
    // page navigations go to /login.
    return isApi
      ? NextResponse.json({ error: "Unauthorized" }, { status: 401 })
      : NextResponse.redirect(new URL("/login", req.url));
  }

  // Authenticated /api/* calls: inject the user's email so the Python backend
  // (which rewrites() forwards to) can resolve the caller's role.
  if (isApi) {
    const headers = new Headers(req.headers);
    try {
      const assertion = await signProxyIdentity(req.auth?.user?.email || "");
      headers.set("X-User-Email", assertion["X-Auth-Email"]);
      headers.set("X-Auth-Timestamp", assertion["X-Auth-Timestamp"]);
      headers.set("X-Auth-Signature", assertion["X-Auth-Signature"]);
    } catch {
      return NextResponse.json({ error: "Authentication unavailable" }, { status: 503 });
    }
    return NextResponse.next({ request: { headers } });
  }

  return NextResponse.next();
});

// Keep the matcher simple: run middleware on everything except static assets and
// the login page. The per-path exclusions above are handled in code, because a
// multi-segment negative-lookahead matcher (e.g. "api/auth") was excluding all of
// /api under Next.js 16, so the middleware never ran for API routes and the
// X-User-Email header was never injected.
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|login).*)"],
};
