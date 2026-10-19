"use client";

import { useEffect, useState } from "react";
import { RiGitPullRequestLine, RiLoader4Line } from "@remixicon/react";
import type { Task } from "@/lib/api";
import { elapsedSince, relativeAge } from "@/lib/server-time";
import { cn } from "@/lib/utils";
import { DEPLOY_QUEUED_LABEL, WAITING_LABELS, shortRepoName, taskTimestamp } from "./taskDisplay";

/** Re-render every `ms` while mounted (client only), for ages and timers. */
function useNow(ms: number): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, ms);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [ms]);
  return now;
}

/** "12m", "3h", "Yesterday" for when the task last changed column. */
export function TaskAge({ task, className }: { task: Task; className?: string }) {
  const now = useNow(60_000);
  const timestamp = task.column === "working" ? null : taskTimestamp(task);
  const age = now === null ? null : relativeAge(timestamp, now);
  if (!age) return null;
  return <span className={cn("shrink-0 text-[13px] tabular-nums text-muted-foreground", className)}>{age}</span>;
}

/** The line under the title: what Loma is doing, what happened, or what it needs. */
export function TaskSummary({ task, className }: { task: Task; className?: string }) {
  const now = useNow(1_000);
  if (task.status === "queued") {
    return <p className={cn("text-[14px] leading-5 text-muted-foreground", className)}>{DEPLOY_QUEUED_LABEL}</p>;
  }
  if (task.column === "working") {
    const elapsed = now === null ? null : elapsedSince(task.started_at, now);
    return (
      <p className={cn("flex min-w-0 items-center gap-1.5 text-[14px] leading-5 text-foreground/80", className)}>
        <RiLoader4Line aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin text-blue-600" />
        <span className="min-w-0 truncate">{task.step || "Starting"}</span>
        {elapsed && <span className="shrink-0 tabular-nums text-muted-foreground">· {elapsed}</span>}
      </p>
    );
  }
  if (!task.summary) return null;
  return <p className={cn("line-clamp-2 break-words text-[14px] leading-5 text-muted-foreground", className)}>{task.summary}</p>;
}

const CHIP_TONE: Record<string, string> = {
  question: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  plan: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  approval: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  information: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  failed: "bg-destructive/10 text-destructive",
  stopped: "bg-destructive/10 text-destructive",
  stopped_by_you: "bg-muted text-muted-foreground",
};

/** Why a stopped task waits, as a labelled chip (not colour alone). Finished
 * tasks need none: their column says it. */
export function WaitingChip({ task }: { task: Task }) {
  const reason = task.waiting_reason;
  if (!reason || reason === "finished") return null;
  return (
    <span className={cn("shrink-0 rounded-md px-2 py-0.5 text-xs font-medium leading-5", CHIP_TONE[reason])}>
      {WAITING_LABELS[reason]}
    </span>
  );
}

/** Linked chips for the PRs a task opened or linked, so a card shows which PR
 * belongs to which request. "#123" when every PR is in one repo, "docs #123"
 * otherwise; a chip never grows wider than its row. Clicks open GitHub, not the card. */
export function TaskPrChips({ task, max = 3, className, insideButton = false }: {
  task: Task;
  max?: number;
  className?: string;
  /** Inside a <button> row an <a> is invalid HTML: render spans that open the PR on click. */
  insideButton?: boolean;
}) {
  const prs = task.pull_requests || [];
  if (prs.length === 0) return null;
  const oneRepo = new Set(prs.map((pr) => pr.repo.toLowerCase())).size === 1;
  const shown = prs.slice(0, max);
  const hidden = prs.slice(max);
  const stop = (event: React.SyntheticEvent) => event.stopPropagation();
  return (
    <span className={cn("inline-flex min-w-0 max-w-full flex-wrap items-center gap-1.5", className)}>
      {shown.map((pr) => {
        const label = (
          <>
            <RiGitPullRequestLine aria-hidden className="h-3 w-3 shrink-0 text-emerald-600" />
            <span className="min-w-0 truncate">{oneRepo ? "" : `${shortRepoName(pr.repo)} `}#{pr.number}</span>
          </>
        );
        const props = {
          title: `Open ${pr.repo}#${pr.number} on GitHub`,
          "data-pr-chip": `${pr.repo}#${pr.number}`,
          onPointerDown: stop,
          onKeyDown: stop,
          className: "inline-flex min-w-0 max-w-full cursor-pointer items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-xs font-medium leading-4 text-foreground/80 tabular-nums hover:border-input hover:bg-muted hover:text-foreground",
        };
        return insideButton ? (
          <span key={props["data-pr-chip"]} {...props} role="link" onClick={(event) => {
            event.stopPropagation();
            event.preventDefault();
            window.open(pr.url, "_blank", "noopener,noreferrer");
          }}>{label}</span>
        ) : (
          <a key={props["data-pr-chip"]} {...props} href={pr.url} target="_blank" rel="noopener noreferrer" onClick={stop}>{label}</a>
        );
      })}
      {hidden.length > 0 && (
        <span className="shrink-0 rounded-md border border-border px-1.5 py-0.5 text-xs leading-4 text-muted-foreground" title={hidden.map((pr) => `${pr.repo}#${pr.number}`).join(", ")}>
          +{hidden.length}
        </span>
      )}
    </span>
  );
}
