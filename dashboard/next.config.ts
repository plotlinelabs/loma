import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL || "http://localhost:3000";

const nextConfig: NextConfig = {
  // Support path-based preview deployments (e.g. /pr/27).
  // In production NEXT_PUBLIC_BASE_PATH is not set, so basePath defaults to "" (no prefix).
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
  skipTrailingSlashRedirect: true,

  // Proxy /api/* (except /api/auth/*) to the Python backend.
  // middleware.ts injects the X-User-Email header before these rewrites run.
  // Using rewrites() instead of middleware NextResponse.rewrite() because
  // rewrites() reliably handles POST request bodies and SSE streaming.
  async rewrites() {
    return [
      {
        source: "/api/auth/:path*",
        destination: "/api/auth/:path*", // keep NextAuth routes local
      },
      {
        source: "/api/:path*",
        destination: `${BACKEND}/api/:path*`,
      },
      {
        source: "/webhook",
        destination: `${BACKEND}/webhook`, // unified webhook endpoint
      },
    ];
  },
};

export default nextConfig;
