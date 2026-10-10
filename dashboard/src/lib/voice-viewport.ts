/** What voice mode can see and move in the open task drawer: the chat
 * transcript's scroll position and the messages inside the viewport. The
 * maths is pure (tested in tests/voice-viewport.test.cjs); the two functions
 * at the bottom read the DOM. */

export type ScrollDirection = "up" | "down" | "top" | "bottom";
export type ScrollAmount = "page" | "half";

export interface ViewportMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export interface ScreenMessage {
  who: "user" | "loma";
  text: string;
}

export interface ScreenBlock {
  who: "user" | "loma";
  text: string;
  /** Offset of the block's top edge from the top of the scrolled content. */
  top: number;
  height: number;
}

/** Keep a line of the previous page in view so the reader keeps their place. */
const PAGE_OVERLAP = 0.85;
const EDGE_PX = 4;
export const SCREEN_TEXT_LIMIT = 1500;

const maxTop = (m: ViewportMetrics) => Math.max(0, m.scrollHeight - m.clientHeight);
const clamp = (value: number, max: number) => Math.min(max, Math.max(0, value));

/** Where the transcript should scroll to. Returns the current offset when it
 * is already at that edge, so the caller can say nothing moved. */
export function planScroll(m: ViewportMetrics, direction: ScrollDirection, amount: ScrollAmount = "page"): number {
  const max = maxTop(m);
  if (direction === "top") return 0;
  if (direction === "bottom") return max;
  const step = Math.round(m.clientHeight * (amount === "half" ? 0.5 : PAGE_OVERLAP));
  return clamp(m.scrollTop + (direction === "down" ? step : -step), max);
}

/** Spoken-friendly place in the thread: "top", "bottom", "all" (it all fits) or a percentage. */
export function describePosition(m: ViewportMetrics): string {
  const max = maxTop(m);
  if (max <= EDGE_PX) return "all";
  if (m.scrollTop <= EDGE_PX) return "top";
  if (m.scrollTop >= max - EDGE_PX) return "bottom";
  return `${Math.round((m.scrollTop / max) * 100)}%`;
}

/** The part of a block's text that is inside the viewport. Text is assumed to
 * be spread evenly over the block's height, then snapped to whole words. */
export function visibleSlice(text: string, top: number, height: number, viewTop: number, viewHeight: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean || height <= 0) return "";
  const from = clamp((viewTop - top) / height, 1);
  const to = clamp((viewTop + viewHeight - top) / height, 1);
  if (to <= from) return "";
  let start = Math.floor(from * clean.length);
  let end = Math.ceil(to * clean.length);
  if (start > 0) {
    const space = clean.indexOf(" ", start);
    start = space === -1 ? start : space + 1;
  }
  if (end < clean.length) {
    const space = clean.lastIndexOf(" ", end);
    end = space > start ? space : end;
  }
  return clean.slice(start, end).trim();
}

/** Messages inside the viewport, top to bottom, sharing one character budget. */
export function visibleMessages(blocks: ScreenBlock[], viewTop: number, viewHeight: number, limit = SCREEN_TEXT_LIMIT): ScreenMessage[] {
  const seen = blocks
    .map((b) => ({ who: b.who, text: visibleSlice(b.text, b.top, b.height, viewTop, viewHeight) }))
    .filter((b) => b.text);
  if (!seen.length) return [];
  const share = Math.max(80, Math.floor(limit / seen.length));
  return seen.map((b) => ({ who: b.who, text: b.text.length > share ? `${b.text.slice(0, share).trimEnd()}...` : b.text }));
}

export interface TaskScreen {
  title: string;
  position: string;
  visible: ScreenMessage[];
}

const DRAWER = "[data-task-drawer]";

function openTaskScroller(): { scroller: HTMLElement; title: string } | null {
  if (typeof document === "undefined") return null;
  const drawer = document.querySelector<HTMLElement>(DRAWER);
  const scroller = drawer?.querySelector<HTMLElement>('[data-slot="chat-messages"]');
  if (!drawer || !scroller) return null;
  const title = drawer.querySelector<HTMLElement>('[data-slot="sheet-title"]')?.innerText.trim() || "this task";
  return { scroller, title };
}

function screenAt(scroller: HTMLElement, title: string, scrollTop: number): TaskScreen {
  const metrics = { scrollTop, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight };
  // Positions are taken relative to the content, so they hold while a smooth scroll is still moving.
  const origin = scroller.getBoundingClientRect().top - scroller.scrollTop;
  const blocks: ScreenBlock[] = Array.from(scroller.querySelectorAll<HTMLElement>("[data-chat-role]")).map((el) => {
    const rect = el.getBoundingClientRect();
    return {
      who: el.dataset.chatRole === "user" ? "user" : "loma",
      text: el.innerText,
      top: rect.top - origin,
      height: rect.height,
    };
  });
  return { title, position: describePosition(metrics), visible: visibleMessages(blocks, scrollTop, metrics.clientHeight) };
}

/** What the open task drawer is showing, or null when no task chat is open. */
export function readTaskScreen(): TaskScreen | null {
  const open = openTaskScroller();
  return open ? screenAt(open.scroller, open.title, open.scroller.scrollTop) : null;
}

/** Scroll the open task's transcript and report what is now in view. */
export function scrollTaskScreen(direction: ScrollDirection, amount: ScrollAmount = "page"): (TaskScreen & { moved: boolean }) | null {
  const open = openTaskScroller();
  if (!open) return null;
  const { scroller, title } = open;
  const target = planScroll(scroller, direction, amount);
  const moved = Math.abs(target - scroller.scrollTop) > EDGE_PX;
  if (moved) {
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    scroller.scrollTo({ top: target, behavior: reduced ? "auto" : "smooth" });
  }
  return { ...screenAt(scroller, title, moved ? target : scroller.scrollTop), moved };
}
