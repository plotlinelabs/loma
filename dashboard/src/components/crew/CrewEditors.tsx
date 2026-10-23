"use client";

/**
 * Editors for a user-defined crew: members (agents, permissions, links) and
 * schedules. Nothing here knows about specific roles.
 */
import { RiAddLine, RiArrowRightLine, RiDeleteBinLine, RiPlayLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { AgentIdentity } from "@/lib/agents-api";
import type { CrewMember, CrewSchedule, WritePolicy } from "@/lib/crew-api";
import { blankMember } from "@/lib/crew-templates";

const NONE = "__none__";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const WRITE_LABEL: Record<WritePolicy, string> = {
  ask: "Ask me first",
  deny: "Never",
  allow: "Without asking",
};

export function slugify(name: string, taken: string[]): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 28) || "member";
  let id = base;
  for (let i = 2; taken.includes(id); i++) id = `${base}-${i}`;
  return id;
}

/** Drop links and schedules that point at a removed member. */
export function removeMember(members: CrewMember[], schedules: CrewSchedule[], id: string) {
  return {
    members: members
      .filter((m) => m.id !== id)
      .map((m) => ({
        ...m,
        delegates_to: m.delegates_to.filter((d) => d !== id),
        reviewed_by: m.reviewed_by === id ? null : m.reviewed_by,
      })),
    schedules: schedules.filter((s) => s.member_id !== id),
  };
}

function Chip({ on, children, onClick, testId }: { on: boolean; children: React.ReactNode; onClick: () => void; testId?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-pressed={on}
      className={cn(
        "rounded-md border px-2 py-0.5 text-xs transition-colors",
        on ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function WiringSummary({ members }: { members: CrewMember[] }) {
  const name = (id: string | null) => members.find((m) => m.id === id)?.name ?? id;
  const rows = members.filter((m) => m.delegates_to.length || m.reviewed_by);
  if (!rows.length) {
    return <p className="text-xs text-muted-foreground">No links yet. Members only work on tasks you or a schedule give them.</p>;
  }
  return (
    <div className="space-y-1" data-testid="crew-wiring">
      {rows.map((m) => (
        <div key={m.id} className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="font-medium text-foreground">{m.name}</span>
          {m.delegates_to.length > 0 && (
            <>
              <RiArrowRightLine size={12} className="text-muted-foreground" />
              <span className="text-muted-foreground">hands work to</span>
              <span className="text-foreground">{m.delegates_to.map(name).join(", ")}</span>
            </>
          )}
          {m.reviewed_by && (
            <span className="text-muted-foreground">
              {m.delegates_to.length ? " · " : ""}reviewed by <span className="text-foreground">{name(m.reviewed_by)}</span>
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function MembersEditor({
  members, schedules, agents, allowUnattended, onChange,
}: {
  members: CrewMember[];
  schedules: CrewSchedule[];
  agents: AgentIdentity[];
  allowUnattended: boolean;
  onChange: (members: CrewMember[], schedules: CrewSchedule[]) => void;
}) {
  const update = (id: string, patch: Partial<CrewMember>) =>
    onChange(members.map((m) => (m.id === id ? { ...m, ...patch } : m)), schedules);
  const add = () => {
    const id = slugify("member", members.map((m) => m.id));
    onChange([...members, blankMember(id)], schedules);
  };

  return (
    <div className="space-y-3">
      {members.map((m) => {
        const others = members.filter((o) => o.id !== m.id);
        return (
          <Card key={m.id} className="p-3 space-y-3" data-testid={`crew-member-${m.id}`}>
            <div className="flex items-start gap-2">
              <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label className="text-xs">Name</Label>
                  <Input value={m.name} maxLength={60} data-testid="member-name" onChange={(e) => update(m.id, { name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Based on agent</Label>
                  <Select value={m.agent_id ?? NONE} onValueChange={(v) => update(m.id, { agent_id: v === NONE ? null : v })}>
                    <SelectTrigger className="w-full" aria-label="Agent"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>Loma (all your tools)</SelectItem>
                      {agents.map((a) => <SelectItem key={a.agent_id} value={a.agent_id}>{a.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <Button
                size="sm" variant="ghost" className="h-8 w-8 p-0 mt-5" aria-label={`Remove ${m.name}`}
                onClick={() => { const next = removeMember(members, schedules, m.id); onChange(next.members, next.schedules); }}
              >
                <RiDeleteBinLine size={15} />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">What it does</Label>
              <Input value={m.description} maxLength={300} placeholder="One line other members see when handing it work"
                     onChange={(e) => update(m.id, { description: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Instructions</Label>
              <Textarea rows={2} value={m.instructions} maxLength={8000} data-testid="member-instructions"
                        placeholder="How this member should work"
                        onChange={(e) => update(m.id, { instructions: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Can hand work to</Label>
              <div className="flex flex-wrap gap-1.5">
                {others.length === 0 && <span className="text-xs text-muted-foreground">Add another member first.</span>}
                {others.map((o) => (
                  <Chip key={o.id} on={m.delegates_to.includes(o.id)} testId={`delegate-${m.id}-${o.id}`}
                        onClick={() => update(m.id, {
                          delegates_to: m.delegates_to.includes(o.id)
                            ? m.delegates_to.filter((d) => d !== o.id) : [...m.delegates_to, o.id],
                        })}>
                    {o.name}
                  </Chip>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-4">
              <div className="space-y-1">
                <Label className="text-xs">Reviewed by</Label>
                <Select value={m.reviewed_by ?? NONE} onValueChange={(v) => update(m.id, { reviewed_by: v === NONE ? null : v })}>
                  <SelectTrigger className="w-full" aria-label="Reviewed by" data-testid={`reviewer-${m.id}`}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>No review</SelectItem>
                    {others.map((o) => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Send and merge</Label>
                <Select value={m.writes} onValueChange={(v) => update(m.id, { writes: v as WritePolicy })}>
                  <SelectTrigger className="w-full" aria-label="Send and merge"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(WRITE_LABEL) as WritePolicy[]).map((p) => (
                      <SelectItem key={p} value={p} disabled={p === "allow" && !allowUnattended}>{WRITE_LABEL[p]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Model</Label>
                <Input value={m.model} placeholder="Default" onChange={(e) => update(m.id, { model: e.target.value })} />
              </div>
              <label className="flex items-center gap-2 text-xs sm:mt-5">
                <Switch checked={m.can_escalate} onCheckedChange={(v) => update(m.id, { can_escalate: v })} />
                Can ask me
              </label>
            </div>
            <p className="text-[11px] text-muted-foreground">id <code>{m.id}</code></p>
          </Card>
        );
      })}
      <Button size="sm" variant="outline" onClick={add} data-testid="crew-add-member">
        <RiAddLine size={14} /> Add member
      </Button>
    </div>
  );
}

export function SchedulesEditor({
  schedules, members, canRun, onChange, onRun,
}: {
  schedules: CrewSchedule[];
  members: CrewMember[];
  canRun: (s: CrewSchedule) => boolean;
  onChange: (schedules: CrewSchedule[]) => void;
  onRun: (s: CrewSchedule) => void;
}) {
  const update = (id: string, patch: Partial<CrewSchedule>) =>
    onChange(schedules.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const add = () => {
    if (!members.length) return;
    const id = slugify("schedule", schedules.map((s) => s.id));
    onChange([...schedules, {
      id, name: "New schedule", member_id: members[0].id, time: "09:00", days: [0, 1, 2, 3, 4],
      prompt: "", include_activity: false, deliver: { inbox: true, slack_dm: false, updates: false }, enabled: true,
    }]);
  };

  return (
    <div className="space-y-3">
      {schedules.map((s) => (
        <Card key={s.id} className="p-3 space-y-3" data-testid={`crew-schedule-${s.id}`}>
          <div className="flex items-start gap-2">
            <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-3">
              <div className="space-y-1">
                <Label className="text-xs">Name</Label>
                <Input value={s.name} maxLength={80} onChange={(e) => update(s.id, { name: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Member</Label>
                <Select value={s.member_id} onValueChange={(v) => update(s.id, { member_id: v })}>
                  <SelectTrigger className="w-full" aria-label="Member"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {members.map((m) => <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Time</Label>
                <Input type="time" value={s.time} onChange={(e) => update(s.id, { time: e.target.value })} />
              </div>
            </div>
            <div className="mt-5 flex gap-1">
              <Button size="sm" variant="outline" disabled={!canRun(s)} onClick={() => onRun(s)} data-testid={`run-${s.id}`}>
                <RiPlayLine size={14} /> Run now
              </Button>
              <Button size="sm" variant="ghost" className="h-8 w-8 p-0" aria-label={`Remove ${s.name}`}
                      onClick={() => onChange(schedules.filter((x) => x.id !== s.id))}>
                <RiDeleteBinLine size={15} />
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {DAYS.map((label, i) => (
              <Chip key={label} on={s.days.includes(i)}
                    onClick={() => update(s.id, {
                      days: s.days.includes(i) ? s.days.filter((d) => d !== i) : [...s.days, i].sort(),
                    })}>
                {label}
              </Chip>
            ))}
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Prompt</Label>
            <Textarea rows={2} value={s.prompt} maxLength={8000} placeholder="What the member should do"
                      onChange={(e) => update(s.id, { prompt: e.target.value })} />
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={s.enabled} onCheckedChange={(v) => update(s.id, { enabled: v })} /> On
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={s.include_activity} onCheckedChange={(v) => update(s.id, { include_activity: v })} />
              Include today&apos;s crew activity
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={s.deliver.inbox} onCheckedChange={(v) => update(s.id, { deliver: { ...s.deliver, inbox: v } })} />
              Send to Loma inbox
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={s.deliver.slack_dm} onCheckedChange={(v) => update(s.id, { deliver: { ...s.deliver, slack_dm: v } })} />
              Slack DM
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={s.deliver.updates} onCheckedChange={(v) => update(s.id, { deliver: { ...s.deliver, updates: v } })} />
              Also send updates, not just the result
            </label>
          </div>
        </Card>
      ))}
      <Button size="sm" variant="outline" onClick={add} disabled={!members.length} data-testid="crew-add-schedule">
        <RiAddLine size={14} /> Add schedule
      </Button>
    </div>
  );
}
