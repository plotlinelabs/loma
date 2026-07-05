"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { RiAddLine, RiCheckboxLine, RiHistoryLine } from "@remixicon/react";
import { useTaskAttention } from "@/lib/TaskAttentionContext";
import { useKeyboardVisible } from "@/hooks/useKeyboardVisible";
import { cn } from "@/lib/utils";

/** Phone-only bottom tab bar — the board, quick capture, and recent chats
 * are one thumb-tap away from anywhere (the floating menu keeps the rest).
 * Hides while the keyboard is open so typing gets the full height back. */
export default function BottomNav() {
  const pathname = usePathname();
  const { needsInputCount } = useTaskAttention();
  const keyboardOpen = useKeyboardVisible();

  if (keyboardOpen) return null;

  const tabs = [
    {
      name: "Tasks",
      href: "/tasks",
      icon: RiCheckboxLine,
      active: pathname.startsWith("/tasks"),
      badge: needsInputCount,
    },
    {
      name: "New",
      href: "/",
      icon: RiAddLine,
      active: pathname === "/",
    },
    {
      name: "Chats",
      href: "/conversations",
      icon: RiHistoryLine,
      active: pathname.startsWith("/conversations"),
    },
  ];

  return (
    <nav className="md:hidden shrink-0 grid grid-cols-3 border-t border-border bg-background pb-[max(0.25rem,env(safe-area-inset-bottom))]">
      {tabs.map((tab) => (
        <Link
          key={tab.name}
          href={tab.href}
          prefetch
          className={cn(
            "flex flex-col items-center gap-0.5 pt-2 pb-1 press-scale",
            tab.active ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <span className="relative">
            <tab.icon size={22} />
            {(tab.badge ?? 0) > 0 && (
              <span className="absolute -top-1 -right-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-none text-white">
                {tab.badge}
              </span>
            )}
          </span>
          <span className="text-[10px] font-medium">{tab.name}</span>
        </Link>
      ))}
    </nav>
  );
}
