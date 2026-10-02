import { getGatewaySecret } from "@/lib/gateway-config";
import { auth } from "@/auth";
import { isSameOrigin } from "@/lib/same-origin";
import { createHmac, createHash } from "node:crypto";


export async function sessionGateway(request: Request, context: { params: Promise<{ path: string[] }> }, prefix: string) {
  const session = await auth();
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return Response.json({ error: "Sign in to manage agent work" }, { status: 401 });
  if (request.method !== "GET" && !isSameOrigin(request)) {
    return Response.json({ error: "Same-origin request required" }, { status: 403 });
  }
  let secret: string;
  try { secret = await getGatewaySecret(); }
  catch { return Response.json({ error: "Gateway configuration unavailable" }, { status: 503 }); }
  if (secret.length < 32) return Response.json({ error: "The human-session gateway is not configured. Ask an admin to connect it." }, { status: 503 });
  const { path } = await context.params;
  if (path.some(part => !/^[a-zA-Z0-9-]+$/.test(part))) return new Response(null, { status: 400 });
  const target = `${prefix}/${path.join("/")}`;
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
    try { return Response.json(JSON.parse(raw), { status: result.status, headers: { "Cache-Control": "no-store" } }); }
    catch { return Response.json({ error: result.status === 404 ? "Service is not enabled" : "Work request failed" }, { status: result.status }); }
  } catch { return Response.json({ error: "Work service unavailable. Your changes were not confirmed; refresh before retrying." }, { status: 503 }); }
}
