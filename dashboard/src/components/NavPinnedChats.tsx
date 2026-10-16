"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { RiPushpinLine } from "@remixicon/react";
import { fetchPinnedConversations } from "@/lib/api";
import { useUser } from "@/lib/UserContext";
import { cn } from "@/lib/utils";
import { NAV_ACTIVE, NAV_IDLE, NAV_ROW } from "./NavBoards";

/** Chats you pinned, under the boards. Shows nothing until you pin one. */
export function NavPinnedChats({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
  const { pinnedIds } = useUser();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const openId = pathname.startsWith("/chat") ? searchParams.get("continue") : null;
  const [chats, setChats] = useState<{ id: string; title: string }[]>([]);
  const pinKey = [...pinnedIds].sort().join(",");

  useEffect(() => {
    if (!pinKey) return;
    let cancelled = false;
    fetchPinnedConversations()
      .then(({ conversations }) => {
        if (cancelled) return;
        setChats(conversations.map((c) => ({
          id: c.conversation_id,
          title: c.title || c.prompt?.slice(0, 60) || "Untitled chat",
        })));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pinKey]);

  if (collapsed || !pinKey || chats.length === 0) return null;
  return (
    <div className="pt-3">
      <div className="flex items-center gap-1.5 px-3 pb-1 text-[12px] font-medium text-muted-foreground">
        <RiPushpinLine size={12} aria-hidden="true" />
        Pinned chats
      </div>
      {chats.filter((chat) => pinnedIds.has(chat.id)).map((chat) => (
        <Link
          key={chat.id}
          href={`/chat?continue=${chat.id}`}
          prefetch={false}
          onClick={onNavigate}
          aria-current={openId === chat.id ? "page" : undefined}
          className={cn(NAV_ROW, "px-3 py-1.5 max-md:py-2.5", openId === chat.id ? NAV_ACTIVE : NAV_IDLE)}
        >
          <span className="min-w-0 truncate">{chat.title}</span>
        </Link>
      ))}
    </div>
  );
}
