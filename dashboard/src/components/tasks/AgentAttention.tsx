"use client";

// Read-only bridge between the taskboard and bounded agent work. It only
// surfaces counts and links to the Needs-you view: nothing on this board can
// approve, execute or retry an agent action.
import { useEffect, useState } from "react";
import Link from "next/link";
import { basePath } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Attention = { approvals: number; questions: number; deliveries: number; charges: number; total: number };

export function AgentAttention() {
  const [attention, setAttention] = useState<Attention | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const result = await fetch(`${basePath}/work-api/attention`, { cache: "no-store" });
        if (!result.ok) return; // Bounded work disabled/unconfigured: stay hidden.
        const data = (await result.json()) as Attention;
        if (!cancelled && typeof data.total === "number") setAttention(data);
      } catch {
        // Network/setup problems must never break the taskboard.
      }
    };
    void load();
    const timer = setInterval(() => void load(), 60000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  if (!attention || attention.total === 0) return null;
  const parts = [
    attention.approvals > 0 && `${attention.approvals} approval${attention.approvals === 1 ? "" : "s"}`,
    attention.questions > 0 && `${attention.questions} question${attention.questions === 1 ? "" : "s"}`,
    attention.deliveries > 0 && `${attention.deliveries} delivery review${attention.deliveries === 1 ? "" : "s"}`,
    attention.charges > 0 && `${attention.charges} charge review${attention.charges === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ");
  return (
    <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-400 bg-amber-50/40 p-3 text-sm dark:bg-amber-950/10">
      <span className="font-medium">✋ Agent work needs you: {parts}</span>
      <Button asChild size="sm" variant="outline" className="border-amber-400">
        <Link href={`${basePath}/agents/work?tab=needs`}>Review in Needs you</Link>
      </Button>
    </div>
  );
}
