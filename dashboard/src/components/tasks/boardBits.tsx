"use client";

import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

/** Boards 1-9 have a jump shortcut; the label follows the platform. */
export const boardShortcut = (index: number, mac: boolean) =>
  index < 9 ? `${mac ? "⌥" : "Alt+"}${index + 1}` : null;

/** The board's emoji, in a fixed-width slot so names line up. */
export function BoardEmoji({ emoji, className }: { emoji?: string; className?: string }) {
  return (
    <span aria-hidden className={cn("flex h-5 w-5 shrink-0 items-center justify-center text-[15px] leading-none", className)}>
      {emoji || "📋"}
    </span>
  );
}

/** How many of the board's tasks are waiting on you. */
export function NeedsYouCount({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null;
  return (
    <span
      className={cn(
        "flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold tabular-nums text-white",
        className,
      )}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

const noSubscribe = () => () => {};
const isMacPlatform = () => /Mac|iPhone|iPad/.test(navigator.platform);

/** Shortcut labels follow the platform; false while server-rendering. */
export const useIsMac = () => useSyncExternalStore(noSubscribe, isMacPlatform, () => false);
