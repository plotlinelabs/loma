"use client";

import { useEffect } from "react";
import { basePath } from "@/lib/api";

/**
 * Registers the service worker on app load (not just on push opt-in) so it's
 * already active when a user opens the installed PWA and enables
 * notifications. Registration is idempotent — subscribeToPush()'s own
 * register() call is unaffected.
 */
export default function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
    navigator.serviceWorker.register(`${basePath}/sw.js`).catch(() => {});
  }, []);
  return null;
}
