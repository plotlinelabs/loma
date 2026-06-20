import { auth } from "./auth";
import { NextResponse } from "next/server";

// Paths under /api that must NOT require auth or get the X-User-Email header.
const PUBLIC_API_PREFIXES = ["/api/auth", "/api/signup"];
// Backend-only paths the dashboard never needs to gate (also routed straight to
// the backend by the reverse proxy).
const BYPASS_PREFIXES = ["/webhook", "/metrics"];

export default auth((req) => {
  const { pathname } = req.nextUrl;

  if (
    BYPASS_PREFIXES.some((p) => pathname.startsWith(p)) ||
    PUBLIC_API_PREFIXES.some((p) => pathname.startsWith(p))
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
    headers.set("X-User-Email", req.auth?.user?.email || "");
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
