"use client";

// Same work records as Agent work, not copies or drag-to-authorize tasks.
import { useEffect, useState } from "react";
import Link from "next/link";
import { basePath } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Attention = { approvals: number; questions: number; deliveries: number; charges: number; proposals?: number; total: number };
type Work = { work_id: string; title: string; paused: boolean; revoked?: boolean; cron?: string; run_status?: string; dry_run?: boolean; agent_snapshot?: {name?: string} };
const lane = (w: Work) => !w.run_status ? "To do" : w.run_status === "running" ? "Running" : w.run_status === "done" ? "Done" : "Waiting / review";
export function AgentAttention() {
  const [attention, setAttention] = useState<Attention | null>(null);
  const [work, setWork] = useState<Work[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [counts, board] = await Promise.all([fetch(`${basePath}/work-api/attention`, {cache: "no-store"}), fetch(`${basePath}/work-api/board`, {cache: "no-store"})]);
        if (counts.status === 404) {if (!cancelled) {setAttention(null); setWork([]); setError("");} return;}
        if (!counts.ok || !board.ok) throw new Error("Agent work could not refresh. Open Agent work to retry; saved tasks are unaffected.");
        const [data, jobs] = await Promise.all([counts.json(), board.json()]);
        if (!cancelled) {setAttention(data); setWork(jobs.work); setError("");}
      } catch (e) {if (!cancelled) setError(e instanceof Error ? e.message : "Agent work could not refresh.");}
    };
    void load(); const timer = setInterval(() => void load(), 30000);
    return () => {cancelled = true; clearInterval(timer);};
  }, []);
  const parts = attention ? [[attention.approvals, "approval"], [attention.questions, "question"], [attention.deliveries, "delivery review"], [attention.charges, "charge review"], [attention.proposals || 0, "worker proposal"]].filter(([count]) => Number(count) > 0).map(([count, label]) => `${count} ${label}${count === 1 ? "" : "s"}`).join(" · ") : "";
  if (!attention && !error) return null;
  return <section aria-label="Agent work on your board" className="shrink-0 space-y-3 rounded-xl border p-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">Agent work</h2><p className="text-xs text-muted-foreground">Live work records. Open a card for controls; moving a chat task never grants permission.</p></div><Button asChild size="sm" variant="outline"><Link href={`${basePath}/agents/work`}>Manage agent work</Link></Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {!!attention?.total && <Link href={`${basePath}/agents/work?tab=needs`} className="block rounded-lg border border-amber-400 p-3 text-sm"><span className="font-medium">Needs you: {parts}</span><span className="block text-xs underline">Open Needs you</span></Link>}
    {!!attention?.proposals && <Link href={`${basePath}/agents/proposals`} className="block text-xs underline">Review worker proposals</Link>}
    {work.length > 0 ? <details><summary className="cursor-pointer text-sm underline">Show agent work cards ({work.length}{work.length === 100 ? "+" : ""})</summary><div className="mt-3 grid max-h-[35dvh] gap-3 overflow-y-auto sm:grid-cols-2 xl:grid-cols-4">{["To do", "Running", "Waiting / review", "Done"].map(column => <section key={column} aria-label={`Agent work: ${column}`} className="min-w-0 rounded-lg bg-muted/40 p-2"><h3 className="mb-2 text-xs font-semibold">{column}</h3><div className="space-y-2">{work.filter(w => lane(w) === column).map(w => <Link key={w.work_id} href={`${basePath}/agents/work?tab=work#work-${w.work_id}`} className="block space-y-1 rounded-lg border bg-background p-3"><span className="block text-[10px] font-semibold uppercase">⚙ Work{w.dry_run ? " · Safe test" : ""}</span><span className="block break-words text-sm font-medium">{w.title}</span><span className="block text-xs text-muted-foreground">{w.agent_snapshot?.name || "Agent"}</span><span className="block text-xs">{w.revoked ? "Access revoked" : w.paused ? "Triggers paused" : w.cron ? "Schedule enabled" : "Future triggers enabled"}</span>{w.run_status && <span className="block text-xs">Last run: {w.run_status.replaceAll("_", " ")}</span>}</Link>)}</div></section>)}</div></details> : <p className="text-sm text-muted-foreground">No agent work yet. Create your first job in Agent work.</p>}
  </section>;
}
