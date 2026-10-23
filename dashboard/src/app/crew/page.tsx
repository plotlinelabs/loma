"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  RiPauseLine,
  RiPlayLine,
  RiRefreshLine,
  RiStopLine,
  RiSunLine,
  RiMoonLine,
  RiTeamLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import { cn } from "@/lib/utils";
import {
  CrewAction,
  CrewNode,
  CrewSettings,
  CrewSource,
  CrewStatus,
  crewAction,
  fetchCrew,
  fetchCrewTree,
  saveCrew,
} from "@/lib/crew-api";

const ROLE_LABEL: Record<string, string> = {
  cos: "Chief of Staff",
  pm: "PM",
  worker: "Worker",
  reviewer: "PM review",
};

const STATE_STYLE: Record<string, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  waiting: "bg-muted text-muted-foreground",
  in_review: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  blocked: "bg-red-500/15 text-red-700 dark:text-red-300",
  accepted: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
  cancelled: "bg-muted text-muted-foreground line-through",
};

const STATE_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  waiting: "Waiting on team",
  in_review: "In PM review",
  blocked: "Needs you",
  accepted: "Accepted",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const SOURCES: { key: CrewSource; label: string }[] = [
  { key: "gmail", label: "Gmail (read only)" },
  { key: "calendar", label: "Calendar" },
  { key: "board", label: "Taskboard" },
  { key: "slack", label: "Slack" },
  { key: "linear", label: "Linear" },
];

const LIMIT_FIELDS: { key: keyof CrewSettings; label: string; min: number; max: number; step?: number }[] = [
  { key: "max_concurrent", label: "Agents at once", min: 1, max: 8 },
  { key: "daily_usd", label: "Daily budget (USD)", min: 1, max: 500, step: 1 },
  { key: "max_tasks_per_day", label: "Tasks per day", min: 1, max: 100 },
  { key: "max_fanout", label: "Tasks per parent", min: 1, max: 12 },
  { key: "max_rework", label: "Rework rounds", min: 0, max: 3 },
];

function NodeRow({ node, depth }: { node: CrewNode; depth: number }) {
  return (
    <>
      <div
        className="flex items-start gap-2 py-1.5 border-b border-border/50 last:border-0"
        style={{ paddingLeft: depth * 20 }}
        data-testid="crew-node"
      >
        <Badge variant="outline" className="shrink-0">{ROLE_LABEL[node.role] ?? node.role}</Badge>
        <div className="min-w-0 flex-1">
          <Link
            href={`/conversations/${node.conversation_id}`}
            className="text-sm text-foreground hover:underline truncate block"
          >
            {node.title || "Untitled"}
          </Link>
          {node.error && <p className="text-xs text-red-600 dark:text-red-400 truncate">{node.error}</p>}
          {node.pull_requests.length > 0 && (
            <div className="flex gap-1 mt-0.5 flex-wrap">
              {node.pull_requests.map((pr) => (
                <a key={pr.url} href={pr.url} target="_blank" rel="noreferrer"
                   className="text-xs text-muted-foreground hover:underline">
                  {pr.repo}#{pr.number}
                </a>
              ))}
            </div>
          )}
        </div>
        {node.rework_round > 0 && (
          <span className="text-xs text-muted-foreground shrink-0">rework {node.rework_round}</span>
        )}
        <span className={cn("text-xs rounded px-1.5 py-0.5 shrink-0", STATE_STYLE[node.state])}>
          {STATE_LABEL[node.state] ?? node.state}
        </span>
        <span className="text-xs text-muted-foreground w-14 text-right shrink-0 tabular-nums">
          ${node.cost_usd.toFixed(2)}
        </span>
      </div>
      {node.children.map((c) => (
        <NodeRow key={c.conversation_id} node={c} depth={depth + 1} />
      ))}
    </>
  );
}

export default function CrewPage() {
  const [status, setStatus] = useState<CrewStatus | null>(null);
  const [draft, setDraft] = useState<CrewSettings | null>(null);
  const [roots, setRoots] = useState<CrewNode[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([fetchCrew(), fetchCrewTree(3)]);
      setStatus(s);
      setDraft((d) => d ?? s.crew);
      setRoots(t.roots);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  const act = async (action: CrewAction) => {
    setBusy(true);
    setError(null);
    try {
      const s = await crewAction(action);
      setStatus(s);
      setDraft(s.crew);
      setRoots((await fetchCrewTree(3)).roots);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      // The server keeps only the settings keys; state changes go through actions.
      const s = await saveCrew(draft);
      setStatus(s);
      setDraft(s.crew);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof CrewSettings>(key: K, value: CrewSettings[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  if (!status || !draft) {
    return (
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-3">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  const state = status.crew.exists ? status.crew.state : "paused";
  const active = state === "active";

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
        <div className="pwa-header-offset flex items-center justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Crew</h1>
            <p className="text-sm text-muted-foreground">
              A Chief of Staff plans your day, PMs split the work, workers do it and a PM review checks it.
            </p>
          </div>
          <div className="flex items-center gap-1">
            {active ? (
              <>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => act("pause")} data-testid="crew-pause">
                  <RiPauseLine size={14} /> Pause
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => act("stop")} data-testid="crew-stop">
                  <RiStopLine size={14} /> Stop all
                </Button>
              </>
            ) : (
              <Button size="sm" disabled={busy || !status.enabled} onClick={() => act("activate")} data-testid="crew-activate">
                <RiPlayLine size={14} /> Turn on
              </Button>
            )}
            <Button size="sm" variant="ghost" className="h-8 w-8 p-0" onClick={load} aria-label="Refresh">
              <RiRefreshLine size={16} />
            </Button>
          </div>
        </div>

        {!status.enabled && (
          <Card className="p-3 border-amber-500/40 bg-amber-500/5">
            <p className="text-sm">Crews are turned off on this server. An admin can enable them with <code>LOMA_CREWS_ENABLED=true</code>.</p>
          </Card>
        )}
        {error && (
          <Card className="p-3 border-red-500/40 bg-red-500/5" data-testid="crew-error">
            <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
          </Card>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {[
            ["Status", active ? "On" : state === "stopped" ? "Stopped" : "Paused"],
            ["Running now", `${status.running} / ${status.crew.max_concurrent}`],
            ["Spend today", `$${status.spend_today_usd.toFixed(2)} / $${status.crew.daily_usd}`],
            ["Needs you", String(status.needs_you)],
          ].map(([label, value]) => (
            <Card key={label} className="p-3">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="text-base font-semibold tabular-nums" data-testid={`crew-stat-${label}`}>{value}</p>
            </Card>
          ))}
        </div>

        <Card className="p-4">
          <CardContent className="p-0 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Today</h2>
              <div className="flex gap-1">
                <Button size="sm" variant="outline" disabled={busy || !active} onClick={() => act("run-plan")} data-testid="crew-run-plan">
                  <RiSunLine size={14} /> Plan now
                </Button>
                <Button size="sm" variant="outline" disabled={busy || !active} onClick={() => act("run-report")} data-testid="crew-run-report">
                  <RiMoonLine size={14} /> Report now
                </Button>
              </div>
            </div>
            {roots.length === 0 ? (
              <EmptyState icon={RiTeamLine} title="No crew work yet"
                description="Turn the crew on. Your Chief of Staff plans every morning, or press Plan now." />
            ) : (
              <div data-testid="crew-tree">
                {roots.map((r) => <NodeRow key={r.conversation_id} node={r} depth={0} />)}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="p-4">
          <CardContent className="p-0 space-y-4">
            <h2 className="text-sm font-semibold">Settings</h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label htmlFor="crew-morning">Morning plan</Label>
                <Input id="crew-morning" type="time" value={draft.morning_time}
                       onChange={(e) => set("morning_time", e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="crew-evening">Evening report</Label>
                <Input id="crew-evening" type="time" value={draft.evening_time}
                       onChange={(e) => set("evening_time", e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="crew-tz">Timezone</Label>
                <Input id="crew-tz" value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="crew-focus">What should the crew focus on?</Label>
              <Textarea id="crew-focus" rows={3} value={draft.focus}
                        placeholder="e.g. Ship the Q4 roadmap items, keep the inbox at zero, prep for customer calls"
                        onChange={(e) => set("focus", e.target.value)} />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Sources for the morning plan</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {SOURCES.map((s) => (
                  <label key={s.key} className="flex items-center gap-2 text-sm">
                    <Switch checked={draft.sources[s.key]} data-testid={`crew-source-${s.key}`}
                            onCheckedChange={(v) => set("sources", { ...draft.sources, [s.key]: v })} />
                    {s.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Send the plan and report to</p>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={draft.reports.inbox}
                          onCheckedChange={(v) => set("reports", { ...draft.reports, inbox: v })} />
                  Loma inbox
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={draft.reports.slack_dm}
                          onCheckedChange={(v) => set("reports", { ...draft.reports, slack_dm: v })} />
                  Slack DM
                </label>
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Limits</p>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {LIMIT_FIELDS.map((f) => (
                  <div key={f.key} className="space-y-1">
                    <Label htmlFor={`crew-${f.key}`} className="text-xs">{f.label}</Label>
                    <Input id={`crew-${f.key}`} type="number" min={f.min} max={f.max} step={f.step ?? 1}
                           value={draft[f.key] as number}
                           onChange={(e) => set(f.key, Number(e.target.value) as never)} />
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Crew agents can never send email or Slack messages, change your calendar or merge PRs on their own.
                They ask you first under Needs you.
              </p>
            </div>
            <div className="flex justify-end">
              <Button size="sm" disabled={busy} onClick={save} data-testid="crew-save">
                {saved ? "Saved" : "Save settings"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
