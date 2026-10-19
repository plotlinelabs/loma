"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";
import { DOCK_PEEK_EVENT, dockPeekRequestedAt, type SidebarSide } from "@/lib/sidebarPrefs";

/** macOS "Automatically hide and show the Dock" for the desktop sidebar.
 *
 * What the real Dock does, and how it maps here:
 * - Hidden by default, takes no screen space: globals.css drops the page's
 *   padding/margin for the bar while `<html data-sidebar-autohide>` is set,
 *   and slides the bar off its edge unless the bar has `data-dock-shown`.
 * - Revealed by pushing the pointer against its screen edge (a thin edge
 *   zone, after a short dwell so a pointer just passing by does not flash it).
 *   Works for every position: bottom, top, left, right.
 * - Revealed while dragging something to the edge (HTML5 drag-and-drop does
 *   not fire pointermove, so `dragover` is watched too).
 * - Stays up while the pointer is on it (including icons magnified out of the
 *   bar and the strip between the floating dock and the edge), and hides a
 *   moment after the pointer leaves.
 * - Stays up while something it opened is open (account menu, popovers):
 *   those render in a portal outside the bar, so leaving the bar for the menu
 *   must not hide it. Re-checked when the menu closes.
 * - Keyboard: tabbing (Tab / arrow keys) into the bar reveals it and it stays
 *   while focus is in it. Only focus that arrived by keyboard navigation
 *   counts: a click leaves focus on the link, and closing a menu with Escape
 *   hands focus back to its trigger (and makes it :focus-visible), and neither
 *   should pin the bar on screen.
 * - `peek()` shows it briefly, like an app bouncing in a hidden Dock: used on
 *   new notifications, and on DOCK_PEEK_EVENT (the user just turned the mode
 *   on or moved the bar) so the bar is not lost the first time.
 *
 * Touch-only screens cannot hover to an edge: the CSS only hides on
 * `(hover: hover)` devices, and this hook does nothing there. No React state:
 * one attribute on the bar, so moving the pointer never re-renders. */
const EDGE_PX = 6;
const SHOW_DELAY_MS = 120;
const HIDE_DELAY_MS = 450;
/** Pointer may wander this far off the bar before it counts as leaving. */
const SLOP_PX = 10;

export function useDockAutohide(
  ref: RefObject<HTMLElement | null>,
  { enabled, side }: { enabled: boolean; side: SidebarSide },
) {
  const peekRef = useRef<(ms?: number) => void>(() => {});

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    if (!enabled || !window.matchMedia("(hover: hover)").matches) {
      root.removeAttribute("data-dock-shown");
      peekRef.current = () => {};
      return;
    }

    let showTimer = 0;
    let hideTimer = 0;
    let peekUntil = 0;
    let last: { x: number; y: number; target: EventTarget | null } | null = null;
    let keyboardNav = false;
    const shown = () => root.hasAttribute("data-dock-shown");

    const atEdge = (x: number, y: number) =>
      side === "bottom" ? y >= window.innerHeight - EDGE_PX
        : side === "top" ? y <= EDGE_PX
        : side === "left" ? x <= EDGE_PX
        : x >= window.innerWidth - EDGE_PX;

    /** On the bar, or in the band between the bar and its screen edge. */
    const overBar = (x: number, y: number, target: EventTarget | null) => {
      if (target instanceof Node && root.contains(target)) return true;
      const r = root.getBoundingClientRect();
      const left = side === "left" ? 0 : r.left - SLOP_PX;
      const right = side === "right" ? window.innerWidth : r.right + SLOP_PX;
      const top = side === "top" ? 0 : r.top - SLOP_PX;
      const bottom = side === "bottom" ? window.innerHeight : r.bottom + SLOP_PX;
      return x >= left && x <= right && y >= top && y <= bottom;
    };

    /** Something keeps the bar up regardless of the pointer. */
    const focusHeld = () => keyboardNav && root.contains(document.activeElement);
    const held = () =>
      Date.now() < peekUntil ||
      focusHeld() ||
      !!root.querySelector('[aria-expanded="true"], [data-state="open"]');

    const show = () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
      showTimer = hideTimer = 0;
      root.setAttribute("data-dock-shown", "");
    };
    const hideSoon = (delay = HIDE_DELAY_MS) => {
      window.clearTimeout(showTimer);
      showTimer = 0;
      if (!shown() || hideTimer) return;
      hideTimer = window.setTimeout(() => {
        hideTimer = 0;
        if (held() || (last && overBar(last.x, last.y, last.target))) return;
        root.removeAttribute("data-dock-shown");
      }, delay);
    };

    const track = (x: number, y: number, target: EventTarget | null) => {
      last = { x, y, target };
      if (shown()) {
        if (overBar(x, y, target) || atEdge(x, y)) {
          window.clearTimeout(hideTimer);
          hideTimer = 0;
        } else if (!held()) hideSoon();
        return;
      }
      if (atEdge(x, y)) {
        if (!showTimer) showTimer = window.setTimeout(show, SHOW_DELAY_MS);
      } else if (showTimer) {
        window.clearTimeout(showTimer);
        showTimer = 0;
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Tab" || e.key.startsWith("Arrow")) keyboardNav = true;
    };
    const onPointerDown = () => { keyboardNav = false; };
    const onPointerMove = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      track(e.clientX, e.clientY, e.target);
    };
    const onDragOver = (e: DragEvent) => track(e.clientX, e.clientY, e.target);
    // Pointer left the browser window.
    const onWindowLeave = () => { last = null; hideSoon(); };
    const onFocusIn = () => { if (keyboardNav) show(); };
    const onPeek = () => peekRef.current();
    const onFocusOut = () => window.setTimeout(() => { if (!held()) hideSoon(); }, 0);
    // A menu or popover opened from the bar closed: hide if nothing else holds it.
    const observer = new MutationObserver(() => {
      if (shown() && !held() && !(last && overBar(last.x, last.y, last.target))) hideSoon();
    });
    observer.observe(root, { subtree: true, attributes: true, attributeFilter: ["aria-expanded", "data-state"] });

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener(DOCK_PEEK_EVENT, onPeek);
    document.addEventListener("pointermove", onPointerMove, { passive: true });
    document.addEventListener("dragover", onDragOver, { passive: true });
    document.documentElement.addEventListener("mouseleave", onWindowLeave);
    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("focusout", onFocusOut);

    peekRef.current = (ms = 1600) => {
      peekUntil = Date.now() + ms;
      show();
      hideSoon(ms + 20);
    };
    // Just turned on or moved to this edge: show where it went. Otherwise, if
    // the pointer is already on it, keep it up.
    if (Date.now() - dockPeekRequestedAt() < 1000) peekRef.current(1800);
    else if (root.matches(":hover")) show();

    return () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
      observer.disconnect();
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener(DOCK_PEEK_EVENT, onPeek);
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("dragover", onDragOver);
      document.documentElement.removeEventListener("mouseleave", onWindowLeave);
      root.removeEventListener("focusin", onFocusIn);
      root.removeEventListener("focusout", onFocusOut);
      root.removeAttribute("data-dock-shown");
      peekRef.current = () => {};
    };
  }, [ref, enabled, side]);

  return useCallback((ms?: number) => peekRef.current(ms), []);
}
