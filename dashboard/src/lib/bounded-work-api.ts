import { basePath } from "@/lib/api";

export async function api<T>(path: string, body?: unknown, method = body ? "POST" : "GET"): Promise<T> {
  const result = await fetch(`${basePath}/work-api/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await result.json();
  if (!result.ok) throw new Error(data.error || "Request failed. Refresh before trying again.");
  return data as T;
}
