"use client";

import { useEffect } from "react";

/**
 * Keeps the app's height in lockstep with the iOS on-screen keyboard.
 *
 * dvh units ignore the keyboard on iOS, so a sticky bottom composer ends up
 * hidden behind it. This syncs `--app-h` to `visualViewport.height` and pins
 * the window scroll (iOS shoves the page up when the keyboard opens); with
 * the app shell sized to `--app-h`, sticky-bottom elements sit exactly on
 * top of the keyboard — the native-app feel.
 *
 * Mounted only in the installed PWA (standalone) where there's no browser
 * chrome to interact with.
 */
export default function ViewportHeightSync() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;

    const sync = () => {
      document.documentElement.style.setProperty("--app-h", `${vv.height}px`);
      // Counteract iOS scrolling the page when the keyboard appears.
      if (vv.offsetTop > 0 || window.scrollY > 0) {
        window.scrollTo(0, 0);
      }
    };

    sync();
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
      document.documentElement.style.removeProperty("--app-h");
    };
  }, []);
  return null;
}
