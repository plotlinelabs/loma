import { auth } from "@/auth";
import { createHmac, createHash } from "node:crypto";

export const runtime = "nodejs";

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const session = await auth();
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return Response.json({ error: "Sign in to manage agent work" }, { status: 401 });
  if (request.method !== "GET" && request.headers.get("origin") !== new URL(request.url).origin) {
    return Response.json({ error: "Same-origin request required" }, { status: 403 });
  }
  const secret = process.env.LOMA_WORK_GATEWAY_SECRET || "";
  if (secret.length < 32) return Response.json({ error: "Bounded work is not configured. Ask an admin to connect the work gateway." }, { status: 503 });
  const { path } = await context.params;
  if (path.some(part => !/^[a-zA-Z0-9-]+$/.test(part))) return new Response(null, { status: 400 });
  const target = `/api/bounded-work/${path.join("/")}`;
  const body = request.method === "GET" ? "" : await request.text();
  if (body.length > 100000) return new Response(null, { status: 413 });
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = createHmac("sha256", secret).update([timestamp, request.method, target, email, createHash("sha256").update(body).digest("hex")].join("\n")).digest("hex");
  try {
    const result = await fetch(`${process.env.BACKEND_URL || "http://localhost:3000"}${target}`, {
      method: request.method, headers: { "Content-Type": "application/json", "X-User-Email": email, "X-Work-Time": timestamp, "X-Work-Signature": signature },
      body: body || undefined, cache: "no-store", signal: AbortSignal.timeout(20000),
    });
    const raw = await result.text();
    try { return Response.json(JSON.parse(raw), { status: result.status }); }
    catch { return Response.json({ error: result.status === 404 ? "Bounded work is not enabled" : "Work request failed" }, { status: result.status }); }
  } catch { return Response.json({ error: "Work service unavailable. Your changes were not confirmed; refresh before retrying." }, { status: 503 }); }
}
export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
