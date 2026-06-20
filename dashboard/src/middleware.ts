import { auth } from "./auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  // Redirect unauthenticated users to login
  if (!req.auth?.user) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  const { pathname } = req.nextUrl;

  // For API calls (except NextAuth), inject X-User-Email header.
  // The actual proxying to the Python backend is handled by rewrites() in next.config.ts,
  // which reliably supports POST bodies and SSE streaming.
  if (pathname.startsWith("/api/") && !pathname.startsWith("/api/auth")) {
    const headers = new Headers(req.headers);
    headers.set("X-User-Email", req.auth?.user?.email || "");
    return NextResponse.next({ request: { headers } });
  }

  // All other routes: NextAuth's authorized callback handles redirect to /login
});

export const config = {
  matcher: [
    "/((?!login|webhook|metrics|api/auth|api/signup|_next/static|_next/image|favicon.ico).*)",
  ],
};
