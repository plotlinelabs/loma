"use client";

import { useEffect, type RefObject } from "react";
import { DOCK_REVEALED_ATTR } from "@/hooks/useDockAutohide";

/** Gap kept between the lifted element and the top of the revealed dock. */
const GAP_PX = 10;

/** Keeps a bottom-floating element (the task composer) clear of the
 * auto-hidden bottom dock.
 *
 * With auto-hide on, the page gets the full height back, so the floating
 * composer sits where the dock appears. When the dock is revealed
 * (`<html data-dock-revealed>`, set by useDockAutohide) and would overlap the
 * element, the element is lifted just enough to clear it: `data-dock-lifted`
 * + `--dock-lift` on the element, applied as a transform in globals.css. It
 * drops back when the dock hides.
 *
 * One exception, so the composer never slides out from under the cursor: if
 * the pointer is on the element when the dock hides (the user moved up from
 * the dock onto the composer), it stays lifted until the pointer leaves it.
 *
 * Only the bottom dock can collide; with the dock always visible the page is
 * already padded above it, and top/left/right bars never touch the composer.
 * No React state: attribute + CSS variable writes only. */
export function useDockClearance(ref: RefObject<HTMLElement | null>, enabled: boolean) {
  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const html = document.documentElement;
    let lifted = false;

    const overlap = () => {
      if (html.getAttribute("data-sidebar-side") !== "bottom") return 0;
      const dock = document.querySelector<HTMLElement>(".loma-sidebar");
      if (!dock) return 0;
      // Where the dock rests once shown (it may still be sliding in, so its
      // live rect is not reliable): `bottom` offset + its own height.
      const cs = getComputedStyle(dock);
      const dockTop = window.innerHeight - (parseFloat(cs.bottom) || 0) - dock.offsetHeight;
      const d = dock.getBoundingClientRect();
      // Measure the element at rest (without the current lift).
      const r = el.getBoundingClientRect();
      const lift = lifted ? parseFloat(el.style.getPropertyValue("--dock-lift")) || 0 : 0;
      const bottom = r.bottom + lift;
      const target = el.querySelector<HTMLElement>("[data-slot=task-composer]")?.getBoundingClientRect() ?? r;
      // No horizontal overlap (composer pushed aside by a drawer): leave it.
      if (target.right <= d.left || target.left >= d.right) return 0;
      return Math.max(0, Math.ceil(bottom - dockTop + GAP_PX));
    };

    const setLift = (px: number) => {
      lifted = px > 0;
      el.style.setProperty("--dock-lift", `${px}px`);
      el.toggleAttribute("data-dock-lifted", lifted);
    };

    const sync = () => {
      if (html.hasAttribute(DOCK_REVEALED_ATTR)) {
        setLift(overlap());
      } else if (lifted && el.matches(":hover")) {
        // Pointer is on the composer: drop it once the pointer leaves.
        el.addEventListener("pointerleave", sync, { once: true });
      } else {
        setLift(0);
      }
    };

    const observer = new MutationObserver(sync);
    observer.observe(html, { attributes: true, attributeFilter: [DOCK_REVEALED_ATTR, "data-sidebar-side"] });
    window.addEventListener("resize", sync);
    sync();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", sync);
      el.removeEventListener("pointerleave", sync);
      setLift(0);
    };
  }, [ref, enabled]);
}
