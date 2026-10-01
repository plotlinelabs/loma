/** Server-only configuration shared with the backend through the existing Loma DB.
 * Never return the key through an API, serialize it into a client component, or log it.
 */
import "server-only";
import { randomBytes } from "node:crypto";
import { getDashboardDb } from "@/auth-node";

export async function getGatewaySecret(): Promise<string> {
  const config = await (await getDashboardDb()).collection<{ _id: string; secret: string }>("gateway_config").findOne({ _id: "human-session" });
  return config ? String(config.secret || "") : process.env.LOMA_WORK_GATEWAY_SECRET || "";
}

export async function configureGateway(actor: string) {
  const collection = (await getDashboardDb()).collection<{ _id: string; secret: string; created_by: string; created_at: Date }>("gateway_config");
  // Unique _id plus setOnInsert makes repeated/concurrent setup safe; never rotates a live key.
  try {
    await collection.updateOne({ _id: "human-session" }, { $setOnInsert: {
      secret: randomBytes(32).toString("hex"), created_by: actor, created_at: new Date(),
    } }, { upsert: true });
  } catch (error) {
    if ((error as { code?: number }).code !== 11000) throw error;
  }
}

export type RuntimeSettings = { bounded_work_enabled: boolean; ashby_allowed_users: string[] };

export async function saveRuntimeSettings(actor: string, input: unknown) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid settings");
  const data = input as Record<string, unknown>;
  if (Object.keys(data).some(key => !["bounded_work_enabled", "ashby_allowed_users"].includes(key)) ||
      typeof data.bounded_work_enabled !== "boolean" || !Array.isArray(data.ashby_allowed_users) ||
      data.ashby_allowed_users.length > 1000 ||
      data.ashby_allowed_users.some(email => typeof email !== "string" || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) {
    throw new Error("Provide a boolean and a list of valid email addresses");
  }
  const settings: RuntimeSettings = { bounded_work_enabled: data.bounded_work_enabled,
    ashby_allowed_users: [...new Set((data.ashby_allowed_users as string[]).map(email => email.trim().toLowerCase()))] };
  const db = await getDashboardDb();
  // Users must already have active Loma accounts. Empty list deliberately revokes all access.
  for (const email of settings.ashby_allowed_users) {
    const user = await db.collection("users").findOne({ email });
    if (!user || user.deleted || (user.status && user.status !== "active")) throw new Error("Every allowed user must have an active Loma account");
  }
  await db.collection<{ _id: string }>("gateway_config").updateOne({ _id: "runtime-settings" },
    { $set: { ...settings, updated_by: actor, updated_at: new Date() } }, { upsert: true });
}
