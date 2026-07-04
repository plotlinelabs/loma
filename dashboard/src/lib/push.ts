// Web push subscription helpers (tasks-board notifications).

import { basePath } from "./api";

const API_BASE = basePath;

export type PushState = "unsupported" | "denied" | "subscribed" | "unsubscribed";

export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    window.isSecureContext
  );
}

/** Whether the backend has VAPID keys configured (404 = push disabled). */
export async function isPushConfigured(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/push/vapid-public-key`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function getPushState(): Promise<PushState> {
  if (!isPushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  const registration = await navigator.serviceWorker.getRegistration(`${basePath}/sw.js`);
  const subscription = await registration?.pushManager.getSubscription();
  return subscription ? "subscribed" : "unsubscribed";
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** Must be called from a user gesture (permission prompt). */
export async function subscribeToPush(): Promise<PushState> {
  if (!isPushSupported()) return "unsupported";

  const registration = await navigator.serviceWorker.register(`${basePath}/sw.js`);
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "denied" : "unsubscribed";

  const keyRes = await fetch(`${API_BASE}/api/push/vapid-public-key`);
  if (!keyRes.ok) throw new Error("Push is not configured on the server");
  const { key } = await keyRes.json();

  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key) as BufferSource,
    }));

  const res = await fetch(`${API_BASE}/api/push/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: subscription.toJSON() }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to save subscription: ${res.status}`);
  }
  return "subscribed";
}

export async function unsubscribeFromPush(): Promise<PushState> {
  const registration = await navigator.serviceWorker.getRegistration(`${basePath}/sw.js`);
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return "unsubscribed";

  await fetch(`${API_BASE}/api/push/subscriptions`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  }).catch(() => {});
  await subscription.unsubscribe();
  return "unsubscribed";
}
