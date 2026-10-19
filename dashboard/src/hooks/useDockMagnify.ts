"use client";

import { useEffect, type RefObject } from "react";

/** macOS Dock-style magnification for the sidebar's icon modes.
 *
 * Every `[data-dock-item]` inside `ref` gets a `--dock-s` scale (1 → max) from
 * its distance to the pointer along the dock's axis, with a cosine falloff so
 * neighbours grow a little too. CSS (globals.css, `.loma-sidebar`) decides what
 * the scale applies to: the glyph only in the narrow side rail (so nothing is
 * clipped), the whole tile in the top/bottom dock (which also pushes
 * neighbours apart, like the real Dock).
 *
 * Item centres are measured once when the pointer enters, with transitions off
 * and scales reset, and reused while it moves: measuring live would feed the
 * growth back into the distances and make the row jitter. Mouse/pen only, and
 * off under prefers-reduced-motion. Writes go straight to style (one rAF per
 * frame), no React state, so hovering never re-renders the sidebar. */
export function useDockMagnify(
  ref: RefObject<HTMLElement | null>,
  { enabled, axis, max = 1.6, range = 110 }: { enabled: boolean; axis: "x" | "y"; max?: number; range?: number },
) {
  useEffect(() => {
    const root = ref.current;
    if (!root || !enabled) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (reduce.matches) return;

    let items: HTMLElement[] = [];
    let centres: number[] = [];
    let frame = 0;
    let pointer: number | null = null;

    const reset = () => items.forEach((el) => el.style.setProperty("--dock-s", "1"));

    const measure = () => {
      items = Array.from(root.querySelectorAll<HTMLElement>("[data-dock-item]"));
      root.setAttribute("data-dock-measuring", "");
      reset();
      centres = items.map((el) => {
        const r = el.getBoundingClientRect();
        return axis === "x" ? r.left + r.width / 2 : r.top + r.height / 2;
      });
      root.removeAttribute("data-dock-measuring");
    };

    const paint = () => {
      frame = 0;
      if (pointer === null) return;
      items.forEach((el, i) => {
        const d = Math.abs(pointer! - centres[i]);
        const s = d >= range ? 1 : 1 + (max - 1) * (Math.cos((Math.PI * d) / range) + 1) / 2;
        el.style.setProperty("--dock-s", s.toFixed(3));
      });
    };

    const isMouse = (e: PointerEvent) => e.pointerType === "mouse" || e.pointerType === "pen";
    const onEnter = (e: PointerEvent) => {
      if (!isMouse(e)) return;
      measure();
      root.setAttribute("data-dock-active", "");
    };
    const onMove = (e: PointerEvent) => {
      if (!isMouse(e) || reduce.matches) return;
      if (!root.hasAttribute("data-dock-active")) onEnter(e);
      pointer = axis === "x" ? e.clientX : e.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const onLeave = () => {
      pointer = null;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      root.removeAttribute("data-dock-active");
      reset();
    };
    // The side rail scrolls: item positions move under a still pointer.
    const onScroll = () => {
      if (pointer === null) return;
      measure();
      if (!frame) frame = requestAnimationFrame(paint);
    };

    root.addEventListener("pointerenter", onEnter);
    root.addEventListener("pointermove", onMove);
    root.addEventListener("pointerleave", onLeave);
    root.addEventListener("scroll", onScroll, true);
    reduce.addEventListener("change", onLeave);
    return () => {
      onLeave();
      root.removeEventListener("pointerenter", onEnter);
      root.removeEventListener("pointermove", onMove);
      root.removeEventListener("pointerleave", onLeave);
      root.removeEventListener("scroll", onScroll, true);
      reduce.removeEventListener("change", onLeave);
    };
  }, [ref, enabled, axis, max, range]);
}
