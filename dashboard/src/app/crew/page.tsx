"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  RiPauseLine,
  RiPlayLine,
  RiRefreshLine,
  RiSendPlaneLine,
  RiStopLine,
  RiTeamLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/EmptyState";
import { MembersEditor, SchedulesEditor, WiringSummary } from "@/components/crew/CrewEditors";
import { cn } from "@/lib/utils";
import { AgentIdentity, fetchAgentIdentities } from "@/lib/agents-api";
import {
  CrewAction,
  CrewNode,
  CrewSchedule,
  CrewSettings,
  CrewStatus,
  crewAction,
  fetchCrew,
  fetchCrewTree,
  giveTask,
  runSchedule,
  saveCrew,
} from "@/lib/crew-api";
import { CREW_TEMPLATES } from "@/lib/crew-templates";

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
  in_review: "In review",
  blocked: "Needs you",
  accepted: "Accepted",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const LIMIT_FIELDS: { key: keyof CrewSettings; label: string; min: number; max: number; step?: number }[] = [
  { key: "max_concurrent", label: "Agents at once", min: 1, max: 8 },
  { key: "daily_usd", label: "Daily budget (USD)", min: 1, max: 500, step: 1 },
  { key: "max_tasks_per_day", label: "Tasks per day", min: 1, max: 100 },
  { key: "max_depth", label: "Levels deep", min: 1, max: 5 },
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
        <Badge variant="outline" className="shrink-0">
          {node.kind === "review" ? `${node.member_name ?? node.member_id} (review)` : node.member_name ?? node.member_id}
        </Badge>
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
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [template, setTemplate] = useState("chief-of-staff");
  const [task, setTask] = useState({ member_id: "", title: "", brief: "" });

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
    fetchAgentIdentities().then((r) => setAgents(r.agents.filter((a) => a.status !== "disabled"))).catch(() => {});
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  // Unsaved edits: Run now and Give task use the saved crew, so ask to save first.
  const dirty = useMemo(() => {
    if (!status || !draft) return false;
    const pick = (c: CrewSettings) => JSON.stringify([c.members, c.schedules, c.notes, c.timezone,
      c.max_concurrent, c.daily_usd, c.max_depth, c.max_fanout, c.max_rework, c.max_tasks_per_day]);
    return pick(status.crew) !== pick(draft);
  }, [status, draft]);

  const run = async (fn: () => Promise<CrewStatus>, keepDraft = true) => {
    setBusy(true);
    setError(null);
    try {
      const s = await fn();
      setStatus(s);
      if (!keepDraft) setDraft(s.crew);
      setRoots((await fetchCrewTree(3)).roots);
      return s;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const act = (action: CrewAction) => run(() => crewAction(action));

  const save = async () => {
    if (!draft) return;
    const s = await run(() => saveCrew({
      timezone: draft.timezone, notes: draft.notes, members: draft.members, schedules: draft.schedules,
      max_concurrent: draft.max_concurrent, daily_usd: draft.daily_usd, max_depth: draft.max_depth,
      max_fanout: draft.max_fanout, max_rework: draft.max_rework, max_tasks_per_day: draft.max_tasks_per_day,
    }), false);
    if (s) {
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  };

  const applyTemplate = () => {
    const t = CREW_TEMPLATES.find((x) => x.id === template);
    if (!t || !draft) return;
    if (draft.members.length && !window.confirm(`Replace your ${draft.members.length} members and schedules with "${t.name}"?`)) return;
    setDraft({ ...draft, members: structuredClone(t.members), schedules: structuredClone(t.schedules) });
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
  const savedMembers = status.crew.members;
  const canRun = (s: CrewSchedule) => active && !busy && !dirty && status.crew.schedules.some((x) => x.id === s.id);
  const taskMember = task.member_id || savedMembers[0]?.id || "";

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
        <div className="pwa-header-offset flex items-center justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Crew</h1>
            <p className="text-sm text-muted-foreground">
              Build your own team of agents: choose who is on it, what each one may do, who hands work to whom and who checks it.
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
              <Button size="sm" disabled={busy || !status.enabled || !savedMembers.length} onClick={() => act("activate")} data-testid="crew-activate">
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
            <h2 className="text-sm font-semibold">Give a task</h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <Select value={taskMember} onValueChange={(v) => setTask({ ...task, member_id: v })} disabled={!savedMembers.length}>
                <SelectTrigger className="w-full" aria-label="Give to" data-testid="task-member"><SelectValue placeholder="Member" /></SelectTrigger>
                <SelectContent>
                  {savedMembers.map((m) => <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <Input className="sm:col-span-2" placeholder="Title" value={task.title} data-testid="task-title"
                     onChange={(e) => setTask({ ...task, title: e.target.value })} />
            </div>
            <Textarea rows={2} placeholder="What should they do?" value={task.brief} data-testid="task-brief"
                      onChange={(e) => setTask({ ...task, brief: e.target.value })} />
            <div className="flex justify-end">
              <Button size="sm" data-testid="task-give"
                      disabled={busy || !active || !taskMember || !task.title.trim() || !task.brief.trim()}
                      onClick={async () => {
                        if (await run(() => giveTask({ member_id: taskMember, title: task.title, brief: task.brief })))
                          setTask({ member_id: taskMember, title: "", brief: "" });
                      }}>
                <RiSendPlaneLine size={14} /> Give task
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="p-4">
          <CardContent className="p-0 space-y-3">
            <h2 className="text-sm font-semibold">Activity</h2>
            {roots.length === 0 ? (
              <EmptyState icon={RiTeamLine} title="No crew work yet"
                description="Give a member a task, or add a schedule and turn the crew on." />
            ) : (
              <div data-testid="crew-tree">
                {roots.map((r) => <NodeRow key={r.conversation_id} node={r} depth={0} />)}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="p-4">
          <CardContent className="p-0 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Members</h2>
              <div className="flex items-center gap-1">
                <Select value={template} onValueChange={setTemplate}>
                  <SelectTrigger size="sm" className="w-52" aria-label="Template" data-testid="crew-template"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CREW_TEMPLATES.map((t) => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" onClick={applyTemplate} data-testid="crew-apply-template">Use template</Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {CREW_TEMPLATES.find((t) => t.id === template)?.description} Templates only fill in the form; change anything before saving.
            </p>
            <WiringSummary members={draft.members} />
            <MembersEditor
              members={draft.members} schedules={draft.schedules} agents={agents}
              allowUnattended={draft.unattended_writes_allowed}
              onChange={(members, schedules) => setDraft({ ...draft, members, schedules })}
            />
          </CardContent>
        </Card>

        <Card className="p-4">
          <CardContent className="p-0 space-y-3">
            <h2 className="text-sm font-semibold">Schedules</h2>
            {dirty && <p className="text-xs text-muted-foreground">Save your changes to use Run now.</p>}
            <SchedulesEditor
              schedules={draft.schedules} members={draft.members} canRun={canRun}
              onChange={(schedules) => set("schedules", schedules)}
              onRun={(s) => run(() => runSchedule(s.id))}
            />
          </CardContent>
        </Card>

        <Card className="p-4">
          <CardContent className="p-0 space-y-4">
            <h2 className="text-sm font-semibold">Crew settings</h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label htmlFor="crew-tz">Timezone</Label>
                <Input id="crew-tz" value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="crew-notes">Notes every member sees</Label>
              <Textarea id="crew-notes" rows={3} value={draft.notes}
                        placeholder="e.g. Ship the Q4 roadmap items, keep the inbox at zero, prep for customer calls"
                        onChange={(e) => set("notes", e.target.value)} />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Limits</p>
              <div className="grid grid-cols-2 sm:grid-cols-6 gap-2">
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
                Every member acts as you. Sending email or Slack messages, changing your calendar and merging PRs follow each
                member&apos;s &quot;Send and merge&quot; setting.
                {!draft.unattended_writes_allowed && " This server only allows \"Ask me first\" or \"Never\"."}
              </p>
            </div>
          </CardContent>
        </Card>

        <div className="sticky bottom-0 flex justify-end gap-2 bg-background/90 py-2 backdrop-blur">
          {dirty && <Button size="sm" variant="ghost" onClick={() => setDraft(status.crew)}>Discard changes</Button>}
          <Button size="sm" disabled={busy || !dirty} onClick={save} data-testid="crew-save">
            {saved ? "Saved" : "Save crew"}
          </Button>
        </div>
      </div>
    </div>
  );
}
