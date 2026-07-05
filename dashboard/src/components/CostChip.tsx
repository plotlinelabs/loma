"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RiArrowRightUpLine } from "@remixicon/react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { fetchConversationCost, type ConversationCost } from "@/lib/api";
import { cn } from "@/lib/utils";

const POLL_MS = 12_000;

export function formatUsd(v: number): string {
  if (v >= 0.01) return `$${v.toFixed(2)}`;
  if (v > 0) return "<1¢";
  return "$0.00";
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Live spend counter for one chat. Polls the lightweight cost endpoint
 * (turn costs land when turns finish, so 12s is plenty); tap/click opens
 * the token breakdown with a link to the full usage page. */
export function CostChip({ conversationId, className }: {
  conversationId: string;
  className?: string;
}) {
  const [cost, setCost] = useState<ConversationCost | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchConversationCost(conversationId)
        .then((c) => { if (!cancelled) setCost(c); })
        .catch(() => {});
    };
    load(); // always fetch on mount — only the poll respects hidden tabs
    const interval = setInterval(() => {
      if (!document.hidden) load();
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [conversationId]);

  if (!cost) return null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title="Chat cost so far"
          className={cn(
            "h-9 px-3 flex items-center rounded-full border border-border bg-background/80 backdrop-blur",
            "text-xs font-medium tabular-nums text-muted-foreground press-scale",
            className,
          )}
        >
          {formatUsd(cost.total_cost_usd)}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-3 text-[13px]">
        <div className="mb-2 font-medium">Spent on this chat</div>
        <dl className="space-y-1 text-muted-foreground">
          <div className="flex justify-between">
            <dt>Total</dt>
            <dd className="tabular-nums text-foreground">{formatUsd(cost.total_cost_usd)}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Tokens in</dt>
            <dd className="tabular-nums">{formatTokens(cost.input_tokens)}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Tokens out</dt>
            <dd className="tabular-nums">{formatTokens(cost.output_tokens)}</dd>
          </div>
          {/* Cache traffic is billed too — without it the totals above can't
              explain the $ figure. Zero (hidden) on pre-capture chats. */}
          {cost.cache_read_tokens > 0 && (
            <div className="flex justify-between">
              <dt>Cache read</dt>
              <dd className="tabular-nums">{formatTokens(cost.cache_read_tokens)}</dd>
            </div>
          )}
          {cost.cache_creation_tokens > 0 && (
            <div className="flex justify-between">
              <dt>Cache write</dt>
              <dd className="tabular-nums">{formatTokens(cost.cache_creation_tokens)}</dd>
            </div>
          )}
          <div className="flex justify-between">
            <dt>Turns</dt>
            <dd className="tabular-nums">{cost.total_turns}</dd>
          </div>
        </dl>
        <Link
          href="/my-usage"
          className="mt-2.5 flex items-center gap-1 text-xs text-brand-600 hover:underline"
        >
          See all my usage <RiArrowRightUpLine size={12} />
        </Link>
      </PopoverContent>
    </Popover>
  );
}
