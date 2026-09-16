/** Session-authenticated issuer. Private key exists ONLY in this service's memory.
 * Never mount this service's process namespace into an agent container.
 * Single-dashboard-process deployment: restart revokes every active grant.
 */
import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { MongoClient, ObjectId } from "mongodb";
import { auth } from "@/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Claims = { aud: string; sub: string; email: string; execution_id: string;
  project_id: string | null; agent_id: string | null; iat: number; exp: number };
type Grant = { claims: Claims; expires: number; tokens: Map<string, number> };
function newState() {
  return { keys: generateKeyPairSync("ed25519"), grants: new Map<string, Grant>(),
    mongo: new MongoClient(process.env.OBSERVABILITY_MONGODB_URI!).connect() };
}
const globalState = globalThis as typeof globalThis & { recallIssuer?: ReturnType<typeof newState> };
function state() { return globalState.recallIssuer ??= newState(); }
const digest = (s: string) => createHash("sha256").update(s).digest("hex");
const now = () => Math.floor(Date.now() / 1000);
function reply(body: object, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}
async function eligible(claims: Claims) {
  const db = (await state().mongo).db(process.env.OBSERVABILITY_DB_NAME || "loma_observability");
  const user = await db.collection("users").findOne({ _id: new ObjectId(claims.sub),
    email: claims.email, status: { $in: [null, "active"] }, deleted: { $ne: true }, recall_excluded: { $ne: true } });
  const doc = await db.collection("conversations").findOne({ conversation_id: claims.execution_id,
    "metadata.user_name": claims.email, deleted: { $ne: true }, recall_excluded: { $ne: true },
    "metadata.recall_excluded": { $ne: true } });
  return !!user && !!doc && (doc.project_id ?? null) === claims.project_id
    && (doc.metadata?.agent_id ?? null) === claims.agent_id;
}
function issue(grant: Grant) {
  const iat = now();
  const claims = { ...grant.claims, iat, exp: Math.min(iat + 300, grant.expires) };
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const capability = payload + "." + sign(null, Buffer.from(payload), state().keys.privateKey).toString("base64url");
  grant.tokens.set(digest(capability), claims.exp);
  return { capability, expires_at: claims.exp };
}
export async function POST(request: Request) {
  try {
    if ((process.env.LOMA_RECALL_ENABLED ?? "true").trim().toLowerCase() !== "true")
      return reply({ error: "recall_disabled" }, 403);
    const reader = request.body?.getReader();
    if (!reader) return reply({ error: "invalid_argument" }, 400);
    const chunks: Uint8Array[] = []; let bytes = 0;
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      bytes += value.byteLength;
      if (bytes > 8192) { await reader.cancel(); return reply({ error: "invalid_argument" }, 400); }
      chunks.push(value);
    }
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
    catch { return reply({ error: "invalid_argument" }, 400); }
    if (!body || typeof body !== "object" || Array.isArray(body)) return reply({ error: "invalid_argument" }, 400);
    const s = state();
    for (const [key, grant] of s.grants) {
      if (grant.expires <= now()) s.grants.delete(key);
      else for (const [token, exp] of grant.tokens) if (exp <= now()) grant.tokens.delete(token);
    }
    if (body.action === "launch") {
      // Headers, request email and preview fallbacks NEVER establish identity.
      const session = await auth();
      const email = session?.user?.email;
      if (!email) return reply({ error: "unauthorized" }, 401);
      if (typeof body.conversation_id !== "string" || body.conversation_id.length > 128)
        return reply({ error: "invalid_argument" }, 400);
      const db = (await s.mongo).db(process.env.OBSERVABILITY_DB_NAME || "loma_observability");
      const user = await db.collection("users").findOne({ email });
      const doc = await db.collection("conversations").findOne({ conversation_id: body.conversation_id,
        "metadata.user_name": email });
      if (!user || !doc) return reply({ error: "not_found" }, 404);
      const claims: Claims = { aud: "loma:recall:fetch:v1", sub: String(user._id), email,
        execution_id: body.conversation_id, project_id: doc.project_id ?? null,
        agent_id: doc.metadata?.agent_id ?? null, iat: now(), exp: now() + 300 };
      if (!await eligible(claims)) return reply({ error: "not_found" }, 404);
      if (s.grants.size >= 10000 || [...s.grants.values()].filter(g => g.claims.sub === claims.sub).length >= 32)
        return reply({ error: "rate_limited" }, 429);
      const token = randomBytes(32).toString("base64url");
      const grant: Grant = { claims, expires: now() + 7200, tokens: new Map() };
      s.grants.set(digest(token), grant);
      return reply({ ...issue(grant), grant: token, user_id: claims.sub, email });
    }
    if (body.action === "validate" && typeof body.capability === "string") {
      const hash = digest(body.capability);
      const grant = [...s.grants.values()].find(g => (g.tokens.get(hash) ?? 0) > now());
      if (!grant || !await eligible(grant.claims) || ![...s.grants.values()].includes(grant)) return reply({ error: "unauthorized" }, 401);
      const jwk = s.keys.publicKey.export({ format: "jwk" });
      return reply({ public_key: jwk.x });
    }
    if (typeof body.grant !== "string" || body.grant.length > 128)
      return reply({ error: "unauthorized" }, 401);
    const key = digest(body.grant);
    const grant = s.grants.get(key);
    if (!grant) return reply({ error: "unauthorized" }, 401);
    if (body.action === "revoke") { s.grants.delete(key); return reply({ revoked: true }); }
    if (body.action !== "renew") return reply({ error: "invalid_argument" }, 400);
    if (!await eligible(grant.claims) || s.grants.get(key) !== grant) { s.grants.delete(key); return reply({ error: "unauthorized" }, 401); }
    return reply(issue(grant));
  } catch { return reply({ error: "recall_unavailable" }, 503); }
}
