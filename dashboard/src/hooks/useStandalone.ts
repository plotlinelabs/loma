"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(display-mode: standalone)";

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

/** True when running as an installed home-screen PWA. False during SSR. */
export function useStandalone(): boolean {
  return useSyncExternalStore(
    subscribe,
    () =>
      window.matchMedia(QUERY).matches ||
      (navigator as { standalone?: boolean }).standalone === true,
    () => false,
  );
}
