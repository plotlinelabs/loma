/** Purpose-bound assertions for the trusted dashboard-to-backend hop. */
export async function signProxyIdentity(email: string): Promise<Record<string, string>> {
  const secret = process.env.BACKEND_PROXY_SECRET;
  if (!secret || secret.length < 32 || !email || /[\r\n\0]/.test(email)) {
    throw new Error("Backend proxy authentication is not configured");
  }
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const bytes = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", bytes.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const signed = await crypto.subtle.sign(
    "HMAC", key, bytes.encode(`loma-proxy-identity-v1\n${timestamp}\n${email}`),
  );
  const signature = Array.from(new Uint8Array(signed), b => b.toString(16).padStart(2, "0")).join("");
  return { "X-Auth-Email": email, "X-Auth-Timestamp": timestamp, "X-Auth-Signature": signature };
}
