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
 * Mounted for every session (browser tab and installed PWA) but only active
 * on touch-primary devices (`pointer: coarse`), which is where the on-screen
 * keyboard lives. In a browser tab `visualViewport.height` already excludes
 * the URL bar, so `--app-h` matches `100dvh` until the keyboard opens; only
 * the keyboard case differs from dvh, which is exactly the case we want to
 * fix. On desktop `--app-h` is left unset (the shell falls back to `100dvh`)
 * so pinch-zoom never shrinks the layout. The window-scroll pin below is
 * gated on a focused editable so it never fights page scrolling.
 */
export default function ViewportHeightSync() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const touch = window.matchMedia("(pointer: coarse)");

    // Only pin the window scroll while the on-screen keyboard is plausibly
    // open (an editable element is focused). Pinning unconditionally fights
    // the user on any page that scrolls at the window level — scrolling down
    // fires visualViewport events and the page snaps back to the top.
    const keyboardLikelyOpen = () => {
      const el = document.activeElement;
      if (!el) return false;
      const tag = el.tagName;
      return (
        tag === "TEXTAREA" ||
        tag === "INPUT" ||
        (el as HTMLElement).isContentEditable === true
      );
    };

    const sync = () => {
      // Desktop, or a pinch-zoomed phone tab: visualViewport.height shrinks
      // with zoom, which is not a keyboard. Fall back to 100dvh so the shell
      // never collapses under a zoomed-in user (same guard as useKeyboardVisible).
      if (!touch.matches || Math.abs(vv.scale - 1) > 0.05) {
        document.documentElement.style.removeProperty("--app-h");
        return;
      }
      document.documentElement.style.setProperty("--app-h", `${vv.height}px`);
      // Counteract iOS scrolling the page when the keyboard appears.
      if (keyboardLikelyOpen() && (vv.offsetTop > 0 || window.scrollY > 0)) {
        window.scrollTo(0, 0);
      }
    };

    sync();
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    touch.addEventListener("change", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
      touch.removeEventListener("change", sync);
      document.documentElement.style.removeProperty("--app-h");
    };
  }, []);
  return null;
}
