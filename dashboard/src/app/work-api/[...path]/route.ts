import { sessionGateway } from "@/lib/session-gateway";
export const runtime = "nodejs";
const proxy = (request: Request, context: { params: Promise<{ path: string[] }> }) => sessionGateway(request, context, "/api/bounded-work");
export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
