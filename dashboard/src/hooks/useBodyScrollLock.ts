"use client";

import { useEffect } from "react";

// Ref-counted so overlapping locks (drawer + artifact sheet) release in any
// order without one of them writing a stale "hidden" back onto the body.
let locks = 0;
let previousOverflow = "";

/** Locks window-level scrolling while `active` (mobile drawers and sheets).
 * Without this, iOS Safari scrolls the page behind a fixed overlay and
 * detaches `position: fixed` chrome from the viewport. */
export function useBodyScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const { body } = document;
    if (locks === 0) previousOverflow = body.style.overflow;
    locks += 1;
    body.style.overflow = "hidden";
    return () => {
      locks -= 1;
      if (locks === 0) body.style.overflow = previousOverflow;
    };
  }, [active]);
}
