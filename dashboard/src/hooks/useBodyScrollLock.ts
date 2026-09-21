"use client";

import { useEffect } from "react";

/** Locks window-level scrolling while `active` (mobile drawers and sheets).
 * Without this, iOS Safari scrolls the page behind a fixed overlay and
 * detaches `position: fixed` chrome from the viewport. */
export function useBodyScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const { body } = document;
    const prev = body.style.overflow;
    body.style.overflow = "hidden";
    return () => {
      body.style.overflow = prev;
    };
  }, [active]);
}
