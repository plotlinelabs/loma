import { auth } from "@/auth";
import { getUsersCollection } from "@/auth-node";
import { configureGateway, getGatewaySecret } from "@/lib/gateway-config";
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
      await configureGateway(email);
    }
    const configured = (await getGatewaySecret()).length >= 32;
    if (!configured) return reply({ configured: false, connected: false, scheduler_running: false });
    const result = await sessionGateway(new Request(request.url, { headers: request.headers }),
      { params: Promise.resolve({ path: ["setup-status"] }) }, "/api/human-tasks");
    if (!result.ok) return reply({ configured: true, connected: false, scheduler_running: false,
      error: "Configuration saved, but the backend connection could not be verified. Check that both services run the updated version and use the same Loma database." });
    const status = await result.json();
    return reply({ configured: true, connected: status.connected === true, scheduler_running: status.scheduler_running === true });
  } catch {
    return reply({ error: "Gateway setup unavailable. No secret values are exposed; retry when the database and backend are available." }, 503);
  }
}
export const GET = handle;
export const POST = handle;
