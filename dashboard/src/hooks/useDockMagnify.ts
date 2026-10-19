"use client";

import { useEffect, type RefObject } from "react";

/** macOS Dock-style magnification for the sidebar's icon modes.
 *
 * How the real Dock behaves, and what this reproduces:
 * - Each icon's size follows a smooth bell curve of its distance to the
 *   pointer: the icon under the pointer is biggest, its neighbours step down
 *   ("small → big → small"), icons ~3 slots away are untouched.
 * - Icons grow OUT of the bar, anchored on the dock's screen edge (a left dock
 *   grows to the right, a bottom dock grows upward), not inside their tile.
 * - Grown icons push their neighbours apart along the dock, while the icon
 *   under the pointer stays put, so the row spreads both ways from the cursor.
 *   Larger gaps (between groups, the empty space above the account button)
 *   absorb the push, so far-away icons do not move.
 * - The theme toggle and account button are not dock items: they stay still,
 *   and `data-dock-stop` keeps grown icons from spreading over them.
 *
 * Everything is transforms (`--dock-s` scale, `--dock-t` shift along the
 * axis; globals.css applies them), so no layout runs while hovering. Item
 * positions are measured once on pointer enter, with transitions off and
 * transforms reset, and reused while the pointer moves: measuring live would
 * feed the growth back into the distances and make the row jitter.
 *
 * Side rail: icons can only grow out of the rail if nothing clips them, so
 * `data-dock-pop` (overflow visible) is set only when the icon list fits
 * without scrolling. When it has to scroll, it falls back to growing the glyph
 * inside its tile (`data-dock-glyph`), which is never clipped.
 *
 * Mouse/pen only, off under prefers-reduced-motion. Writes go straight to
 * style (one rAF per frame), no React state, so hovering never re-renders. */
export function useDockMagnify(
  ref: RefObject<HTMLElement | null>,
  { enabled, axis, max = 2, slots = 3 }: { enabled: boolean; axis: "x" | "y"; max?: number; slots?: number },
) {
  useEffect(() => {
    const root = ref.current;
    if (!root || !enabled) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (reduce.matches) return;

    type Item = { el: HTMLElement; c: number; size: number };
    let items: Item[] = [];
    let range = 130;
    let lo = -Infinity;
    let hi = Infinity;
    let frame = 0;
    let pointer: number | null = null;

    const reset = () => {
      items.forEach(({ el }) => {
        el.style.setProperty("--dock-s", "1");
        el.style.setProperty("--dock-t", "0px");
      });
      root.style.setProperty("--dock-ext-start", "0px");
      root.style.setProperty("--dock-ext-end", "0px");
    };

    const measure = () => {
      root.setAttribute("data-dock-measuring", "");
      root.querySelectorAll<HTMLElement>("[data-dock-item]").forEach((el) => {
        el.style.setProperty("--dock-s", "1");
        el.style.setProperty("--dock-t", "0px");
      });
      // Pop out of the rail only if the scrolling icon list fits as is.
      const vp = root.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]");
      const fits = !vp || vp.scrollHeight <= vp.clientHeight + 1;
      root.toggleAttribute("data-dock-pop", axis === "x" || fits);

      items = Array.from(root.querySelectorAll<HTMLElement>("[data-dock-item]"))
        .map((el) => {
          const r = el.getBoundingClientRect();
          return axis === "x"
            ? { el, c: r.left + r.width / 2, size: r.width }
            : { el, c: r.top + r.height / 2, size: r.height };
        })
        .filter((it) => it.size > 0)
        .sort((a, b) => a.c - b.c);
      // Influence spans ~`slots` icons each way, scaled to the real spacing.
      const pitches = items.slice(1).map((it, i) => it.c - items[i].c).sort((a, b) => a - b);
      const pitch = pitches.length ? pitches[Math.floor(pitches.length / 2)] : 44;
      range = Math.max(90, pitch * slots);
      // Spread-out icons must stay on screen (8px margin).
      const box = root.getBoundingClientRect();
      lo = axis === "x" ? 8 : Math.max(0, box.top) + 8;
      hi = axis === "x" ? window.innerWidth - 8 : Math.min(window.innerHeight, box.bottom) - 8;
      // ...and off the logo when it sits before the icons.
      const bound = root.querySelector<HTMLElement>("[data-dock-bound]")?.getBoundingClientRect();
      if (bound && bound.width > 0 && items.length) {
        const end = (axis === "x" ? bound.right : bound.bottom) + 4;
        if (end <= items[0].c) lo = Math.max(lo, end);
      }
      // ...and off the non-magnified controls after them (theme, account).
      const stop = root.querySelector<HTMLElement>("[data-dock-stop]")?.getBoundingClientRect();
      if (stop && stop.width > 0 && items.length) {
        const start = (axis === "x" ? stop.left : stop.top) - 4;
        if (start >= items[items.length - 1].c) hi = Math.min(hi, start);
      }
      root.removeAttribute("data-dock-measuring");
    };

    const paint = () => {
      frame = 0;
      if (pointer === null || !items.length) return;
      const p = pointer;
      const pop = root.hasAttribute("data-dock-pop");
      const n = items.length;
      const bell = (centre: number) => {
        const d = Math.abs(p - centre);
        return d >= range ? 1 : 1 + (max - 1) * (Math.cos((Math.PI * d) / range) + 1) / 2;
      };
      let scale = items.map(({ c }) => bell(c));
      if (!pop) {
        items.forEach(({ el }, i) => el.style.setProperty("--dock-s", scale[i].toFixed(3)));
        return;
      }
      const gapOf = (i: number) => Math.min(6, items[i + 1].c - items[i + 1].size / 2 - (items[i].c + items[i].size / 2));
      let half: number[] = [];
      let pos: number[] = [];
      const layout = () => {
        // Anchor: the icon under (or nearest to) the pointer keeps its centre.
        let a = 0;
        for (let i = 1; i < n; i++) if (Math.abs(items[i].c - p) < Math.abs(items[a].c - p)) a = i;
        half = items.map((it, i) => (it.size * scale[i]) / 2);
        pos = items.map((it) => it.c);
        const pushDown = (from: number) => {
          for (let i = from + 1; i < n; i++)
            pos[i] = Math.max(items[i].c, pos[i - 1] + half[i - 1] + gapOf(i - 1) + half[i]);
        };
        const pushUp = (from: number) => {
          for (let i = from - 1; i >= 0; i--)
            pos[i] = Math.min(items[i].c, pos[i + 1] - half[i + 1] - gapOf(i) - half[i]);
        };
        pushDown(a);
        pushUp(a);
        // Keep the spread on screen and off the logo: nudge it back in.
        const under = lo - (pos[0] - half[0]);
        if (under > 0) { for (let i = 0; i <= a; i++) pos[i] += under; pushDown(a); }
        const over = pos[n - 1] + half[n - 1] - hi;
        if (over > 0) { for (let i = a; i < n; i++) pos[i] -= over; pushUp(a); }
      };
      // When an end is pinned (top of the rail), the spread drifts; re-derive
      // sizes from where icons are actually drawn so the biggest one stays
      // under the cursor. Converges in a couple of passes.
      layout();
      for (let k = 0; k < 3; k++) {
        scale = pos.map(bell);
        layout();
      }
      items.forEach(({ el, c }, i) => {
        el.style.setProperty("--dock-s", scale[i].toFixed(3));
        el.style.setProperty("--dock-t", `${(pos[i] - c).toFixed(1)}px`);
      });
      // How far the spread reaches past the original ends: the bottom dock's
      // pill widens by this much, like the real Dock's shelf.
      const first = items[0], last = items[n - 1];
      root.style.setProperty("--dock-ext-start", `${Math.max(0, first.c - first.size / 2 - (pos[0] - half[0])).toFixed(1)}px`);
      root.style.setProperty("--dock-ext-end", `${Math.max(0, pos[n - 1] + half[n - 1] - (last.c + last.size / 2)).toFixed(1)}px`);
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
      // Let the icons settle back before the rail clips again.
      window.setTimeout(() => {
        if (!root.hasAttribute("data-dock-active")) root.removeAttribute("data-dock-pop");
      }, 260);
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
      root.removeAttribute("data-dock-pop");
      root.removeEventListener("pointerenter", onEnter);
      root.removeEventListener("pointermove", onMove);
      root.removeEventListener("pointerleave", onLeave);
      root.removeEventListener("scroll", onScroll, true);
      reduce.removeEventListener("change", onLeave);
    };
  }, [ref, enabled, axis, max, slots]);
}
