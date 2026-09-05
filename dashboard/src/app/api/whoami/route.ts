import { auth } from "@/auth";

export const runtime = "nodejs";

// Used by the nginx `auth_request` subrequest: returns the caller's email (from
// the verified NextAuth session) in the X-Auth-Email response header so nginx can
// inject a trusted X-User-Email when proxying /api/* to the Python backend.
// Returns 401 when there is no valid session, which makes nginx reject the call.
//
// This exists because Next.js does not forward middleware-injected request headers
// through next.config rewrites() to an external backend, so the proxy (nginx) must
// resolve identity out-of-band instead.
export async function GET() {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) {
    return new Response(null, { status: 401 });
  }
  return new Response(null, {
    status: 200,
    headers: { "X-Auth-Email": email },
  });
}
