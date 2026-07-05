"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(max-width: 767px)"; // matches the app's md: breakpoint

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

/** True below the md (768px) breakpoint. False during SSR. */
export function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
