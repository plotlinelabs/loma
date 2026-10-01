import { auth } from "@/auth";
import { getUsersCollection } from "@/auth-node";
import { configureGateway, getGatewaySecret, saveRuntimeSettings } from "@/lib/gateway-config";
import { sessionGateway } from "@/lib/session-gateway";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const reply = (data: object, status = 200) => Response.json(data, { status, headers: { "Cache-Control": "no-store" } });

async function handle(request: Request) {
  try {
    const session = await auth();
    const email = session?.user?.email?.trim().toLowerCase();
    if (!email) return reply({ error: "Sign in required" }, 401);
    const user = await (await getUsersCollection()).findOne({ email });
    if (!user || user.deleted || (user.status && user.status !== "active") || !["admin", "maintainer"].includes(user.system_role)) {
      return reply({ error: "Admin or maintainer access required" }, 403);
    }
    if (request.method === "POST") {
      if (request.headers.get("origin") !== new URL(request.url).origin) return reply({ error: "Same-origin request required" }, 403);
      const raw = await request.text();
      if (raw.length > 100000) return reply({ error: "Request too large" }, 413);
      if (raw) {
        let data;
        try { data = JSON.parse(raw); } catch { return reply({ error: "Invalid JSON" }, 400); }
        if (data?.action !== "save-settings") return reply({ error: "Unknown action" }, 400);
        // Access-control edits are admin-only, not available to maintainers.
        if (user.system_role !== "admin") return reply({ error: "Admin access required to change permissions" }, 403);
        try { await saveRuntimeSettings(email, data.settings); }
        catch { return reply({ error: "Settings could not be saved. Use valid active user emails and retry." }, 400); }
      }
      await configureGateway(email);
    }
    const configured = (await getGatewaySecret()).length >= 32;
    if (!configured) return reply({ configured: false, connected: false, scheduler_running: false });
    const result = await sessionGateway(new Request(request.url, { headers: request.headers }),
      { params: Promise.resolve({ path: ["setup-status"] }) }, "/api/human-tasks");
    if (!result.ok) return reply({ configured: true, connected: false, scheduler_running: false,
      error: "Configuration saved, but the backend connection could not be verified. Check that both services run the updated version and use the same Loma database." });
    const status = await result.json();
    return reply({ configured: true, connected: status.connected === true, scheduler_running: status.scheduler_running === true, settings: status.settings });
  } catch {
    return reply({ error: "Gateway setup unavailable. No secret values are exposed; retry when the database and backend are available." }, 503);
  }
}
export const GET = handle;
export const POST = handle;
