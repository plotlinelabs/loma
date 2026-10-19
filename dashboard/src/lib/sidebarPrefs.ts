"use client";

import { useSyncExternalStore } from "react";

/** Desktop sidebar preferences (collapsed rail, which edge it docks to, and
 * Dock-style icon magnification).
 *
 * Stored per device in localStorage and mirrored onto <html> as
 * `data-sidebar` / `data-sidebar-side`. Left/right is the classic sidebar;
 * top is a full-width icon bar and bottom a floating macOS-style dock. The root layout's inline head script
 * sets those attributes before first paint, and globals.css sizes the sidebar
 * and offsets the page from them, so the layout is right on the first frame.
 *
 * React reads the same values through useSyncExternalStore with a fixed server
 * snapshot: hydration renders the server value, then React re-renders with the
 * stored one. Reading localStorage in a useState initializer (the old way) made
 * the client's first render disagree with the server HTML, and React does not
 * patch mismatched attributes, so the page kept the expanded 220px offset next
 * to a collapsed 56px rail. */

export type SidebarSide = "left" | "right" | "top" | "bottom";

const SIDES: readonly SidebarSide[] = ["left", "right", "top", "bottom"];

/** Top and bottom lay the nav out as a row of icons (no labels). */
export const isHorizontalSide = (side: SidebarSide) => side === "top" || side === "bottom";

/** Where tooltips and popovers should open: away from the docked edge. */
export const awayFromEdge = (side: SidebarSide) =>
  ({ left: "right", right: "left", top: "bottom", bottom: "top" } as const)[side];

const COLLAPSED_KEY = "sidebar-collapsed";
const SIDE_KEY = "sidebar-side";
const MAGNIFY_KEY = "sidebar-magnify";

const listeners = new Set<() => void>();

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function readCollapsed(): boolean {
  return read(COLLAPSED_KEY) === "true";
}

function readSide(): SidebarSide {
  const v = read(SIDE_KEY);
  return (SIDES as readonly string[]).includes(v ?? "") ? (v as SidebarSide) : "left";
}

/** On by default; only an explicit "false" turns it off. */
function readMagnify(): boolean {
  return read(MAGNIFY_KEY) !== "false";
}

/** Keep the <html> attributes the CSS keys off in step with storage. */
function applyAttributes() {
  const root = document.documentElement;
  root.setAttribute("data-sidebar", readCollapsed() ? "collapsed" : "expanded");
  root.setAttribute("data-sidebar-side", readSide());
}

function notify() {
  applyAttributes();
  listeners.forEach((l) => l());
}

function onStorage(e: StorageEvent) {
  // Another tab changed a preference: follow it here too.
  if (e.key === COLLAPSED_KEY || e.key === SIDE_KEY || e.key === MAGNIFY_KEY) notify();
}

function subscribe(listener: () => void) {
  if (listeners.size === 0) window.addEventListener("storage", onStorage);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener("storage", onStorage);
  };
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode: the change still applies to this page */
  }
  notify();
}

export function useSidebarCollapsed(): boolean {
  return useSyncExternalStore(subscribe, readCollapsed, () => false);
}

export function useSidebarSide(): SidebarSide {
  return useSyncExternalStore(subscribe, readSide, () => "left" as SidebarSide);
}

export function useSidebarMagnify(): boolean {
  return useSyncExternalStore(subscribe, readMagnify, () => true);
}

export function setSidebarMagnify(on: boolean) {
  write(MAGNIFY_KEY, String(on));
}

export function toggleSidebarCollapsed() {
  write(COLLAPSED_KEY, String(!readCollapsed()));
}

export function setSidebarSide(side: SidebarSide) {
  write(SIDE_KEY, side);
}

/** Inline <head> script: sets the attributes before first paint. Tiny and
 * dependency-free because it runs before React. */
export const SIDEBAR_PREFS_SCRIPT = `(function(){try{var d=document.documentElement;d.setAttribute('data-sidebar',localStorage.getItem('${COLLAPSED_KEY}')==='true'?'collapsed':'expanded');var s=localStorage.getItem('${SIDE_KEY}');d.setAttribute('data-sidebar-side',${JSON.stringify(SIDES)}.indexOf(s)>-1?s:'left')}catch(e){}})()`;
