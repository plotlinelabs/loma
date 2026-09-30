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
 * the token breakdown with a link to the full usage page. The figure is the
 * chat's all-time total, which is why it won't match "Today" on My usage
 * for a chat that also ran on earlier days. */
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
          title="Chat cost so far (all time)"
          className={cn(
