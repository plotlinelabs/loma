"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { RiMenuLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import CrosscutIcon from "@/components/CrosscutIcon";
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils";

/** Phone chrome shared by every route: one top bar (menu, page title, page
 * actions) and the bottom tab bar. Pages fill the bar through the
 * `MobileTopBarTitle` / `MobileTopBarActions` portals instead of rendering
 * their own header rows, so the list or chat below gets the height back. */
interface MobileChrome {
  titleNode: HTMLElement | null;
  actionsNode: HTMLElement | null;
  setTitleNode: (node: HTMLElement | null) => void;
  setActionsNode: (node: HTMLElement | null) => void;
  /** Top bar slid away by a downward scroll. */
  barHidden: boolean;
  /** A page asked for the bottom tab bar to go away (an open conversation). */
  navHidden: boolean;
  requestNavHidden: () => () => void;
}

const noop = () => {};
const MobileChromeContext = createContext<MobileChrome>({
  titleNode: null,
  actionsNode: null,
  setTitleNode: noop,
  setActionsNode: noop,
  barHidden: false,
  navHidden: false,
  requestNavHidden: () => noop,
});

export const useMobileChrome = () => useContext(MobileChromeContext);

// Scroll distances (px) before the bar reacts, so a resting thumb or a small
// correction does not make it flicker.
const HIDE_AFTER = 28;
const SHOW_AFTER = 14;
// Hiding or showing the bar resizes the scroll region under it. Ignore scroll
// deltas until that layout change has settled, or the clamp it causes near
// the end of a list reads as a scroll in the other direction.
const SETTLE_MS = 350;
// Only a scroll the person made counts. Chat scrolls itself to the newest
// message on load and while streaming; that must not hide the bar.
const GESTURE_MS = 1200;

function useHideOnScroll(enabled: boolean): boolean {
  const pathname = usePathname();
  // Keyed by route so a new page always starts with the bar showing.
  const [hiddenOn, setHiddenOn] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const last = new WeakMap<Element, number>();
    let travelled = 0;
    let settleUntil = 0;
    let gestureAt = 0;
    let isHidden = false;

    const set = (next: boolean) => {
      if (next === isHidden) return;
      isHidden = next;
      travelled = 0;
      settleUntil = performance.now() + SETTLE_MS;
      setHiddenOn(next ? pathname : null);
    };
    const onGesture = () => {
      gestureAt = performance.now();
    };
    const onScroll = (event: Event) => {
      const el = event.target;
      if (!(el instanceof HTMLElement) || el.tagName === "TEXTAREA") return;
      // Page content only: drawers, sheets and menus are portalled outside main.
      if (!el.closest("main.loma-dashboard")) return;
      const y = el.scrollTop;
      const previous = last.get(el) ?? y;
      last.set(el, y);
      const delta = y - previous;
      if (delta === 0) return; // horizontal scrollers (column chips)
      const now = performance.now();
      if (now < settleUntil) return;
      if (y <= 8) {
        set(false);
        return;
      }
      if (now - gestureAt > GESTURE_MS) return;
      // Too short to be worth collapsing for, and collapsing would make it
      // stop scrolling altogether.
      if (el.scrollHeight - el.clientHeight < 160) return;
      travelled = Math.sign(delta) === Math.sign(travelled) ? travelled + delta : delta;
      if (travelled > HIDE_AFTER) set(true);
      else if (travelled < -SHOW_AFTER) set(false);
    };

    // Scroll does not bubble; capture catches every nested scroll region.
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    document.addEventListener("touchstart", onGesture, { passive: true });
    document.addEventListener("touchmove", onGesture, { passive: true });
    document.addEventListener("touchend", onGesture, { passive: true });
    document.addEventListener("wheel", onGesture, { passive: true });
    return () => {
      document.removeEventListener("scroll", onScroll, { capture: true });
      document.removeEventListener("touchstart", onGesture);
      document.removeEventListener("touchmove", onGesture);
      document.removeEventListener("touchend", onGesture);
      document.removeEventListener("wheel", onGesture);
    };
  }, [enabled, pathname]);

  return enabled && hiddenOn === pathname;
}

export function MobileChromeProvider({ children }: { children: ReactNode }) {
  const isMobile = useIsMobile();
  const [titleNode, setTitleNode] = useState<HTMLElement | null>(null);
  const [actionsNode, setActionsNode] = useState<HTMLElement | null>(null);
  const [navRequests, setNavRequests] = useState(0);
  const barHidden = useHideOnScroll(isMobile);

  const requestNavHidden = useCallback(() => {
    setNavRequests((count) => count + 1);
    return () => setNavRequests((count) => count - 1);
  }, []);

  const value = useMemo<MobileChrome>(() => ({
    titleNode,
    actionsNode,
    setTitleNode,
    setActionsNode,
    barHidden,
    navHidden: navRequests > 0,
    requestNavHidden,
  }), [titleNode, actionsNode, barHidden, navRequests, requestNavHidden]);

  return <MobileChromeContext.Provider value={value}>{children}</MobileChromeContext.Provider>;
}

/** The one phone top bar. 48px: menu, the page's title, the page's actions.
 * Routes that set no title show the Loma mark, so the row is never just a
 * lone menu button. */
export function MobileTopBar({ onMenu }: { onMenu: () => void }) {
  const { setTitleNode, setActionsNode, barHidden } = useMobileChrome();
  return (
    <header
      data-slot="mobile-top-bar"
      data-hidden={barHidden ? "true" : undefined}
      // `inert` while slid away: an off-screen button must not take focus.
      inert={barHidden}
      className={cn(
        "md:hidden fixed inset-x-0 top-0 z-30 border-b border-border bg-background/95 backdrop-blur",
        "pt-[env(safe-area-inset-top)] transition-transform duration-200 motion-reduce:transition-none",
        barHidden && "-translate-y-full",
      )}
    >
      {/* 48px including the bottom border, to match the padding `main` reserves. */}
      <div className="flex h-[calc(3rem-1px)] items-center gap-1 px-1">
        <Button
          variant="ghost"
          size="icon"
          onClick={onMenu}
          aria-label="Toggle menu"
          className="size-11 shrink-0 rounded-full text-muted-foreground press-scale"
        >
          <RiMenuLine size={20} />
        </Button>
        <div className="flex min-w-0 flex-1 items-center">
          <div ref={setTitleNode} data-slot="mobile-top-bar-title" className="peer flex min-w-0 flex-1 items-center gap-2 empty:hidden" />
          <div className="hidden items-center gap-2 peer-empty:flex">
            <CrosscutIcon size={18} />
            <span className="font-[family-name:var(--font-logo)] text-base font-bold tracking-[0.5px] text-foreground/80">Loma</span>
          </div>
        </div>
        <div ref={setActionsNode} data-slot="mobile-top-bar-actions" className="flex shrink-0 items-center gap-0.5 empty:hidden" />
      </div>
    </header>
  );
}

/** Render a page's title into the phone top bar. Renders nothing on desktop. */
export function MobileTopBarTitle({ children }: { children: ReactNode }) {
  const { titleNode } = useMobileChrome();
  const isMobile = useIsMobile();
  if (!isMobile || !titleNode) return null;
  return createPortal(children, titleNode);
}

/** Render a page's actions into the phone top bar. Renders nothing on desktop. */
export function MobileTopBarActions({ children }: { children: ReactNode }) {
  const { actionsNode } = useMobileChrome();
  const isMobile = useIsMobile();
  if (!isMobile || !actionsNode) return null;
  return createPortal(children, actionsNode);
}

/** Hide the bottom tab bar while `active` (for example inside a conversation). */
export function useHideBottomNav(active: boolean) {
  const { requestNavHidden } = useMobileChrome();
  useEffect(() => {
    if (!active) return;
    return requestNavHidden();
  }, [active, requestNavHidden]);
}
