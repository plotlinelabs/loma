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
