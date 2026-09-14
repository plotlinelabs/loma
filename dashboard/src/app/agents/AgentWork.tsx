"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { basePath, createFlow, fetchFlows, pauseFlow, resumeFlow, type Flow } from "@/lib/api";
import type { AgentIdentity } from "@/lib/agents-api";
import { useUser } from "@/lib/UserContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RiAddLine, RiTimeLine } from "@remixicon/react";

const selectClass = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm";
const date = (value: string, zone: string) => new Date(value).toLocaleString(undefined, { timeZone: zone, dateStyle: "medium", timeStyle: "short" });

export function AgentWork({ agent, onClose }: { agent: AgentIdentity; onClose: () => void }) {
  const { user, hasRole } = useUser();
  const [jobs, setJobs] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState("");
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [frequency, setFrequency] = useState("weekdays");
  const [time, setTime] = useState("09:00");
  const [zone, setZone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [confirmEnable, setConfirmEnable] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchFlows(undefined, "scheduled", agent.agent_id);
      setJobs(data.flows);
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load scheduled work"); }
    finally { setLoading(false); }
  }, [agent.agent_id]);
  useEffect(() => { void load(); }, [load]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || !user) return;
    setError(""); setNotice("");
    try {
      // Validate without silently falling back to the server's timezone.
      new Intl.DateTimeFormat(undefined, { timeZone: zone }).format();
    } catch { setError("Enter a valid timezone, such as Asia/Kolkata or America/New_York."); return; }
    setBusy("save");
    const [hour, minute] = time.split(":").map(Number);
    try {
      const { flow } = await createFlow({
        name: name.trim(), prompt: prompt.trim(), agent_id: agent.agent_id,
        trigger_type: "scheduled", schedule_type: "recurring",
        cron: `${minute} ${hour} * * ${frequency === "weekdays" ? "1-5" : "*"}`,
        frequency: `${frequency === "weekdays" ? "Weekdays" : "Every day"} at ${time}`,
        timezone: zone, run_as: user.email, visibility: "private", status: "paused",
      });
      setJobs((previous) => [flow, ...previous]);
      setEditing(false); setName(""); setPrompt("");
      setNotice("Schedule saved and paused. Review it below, then enable it when you are ready.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save schedule"); }
    finally { setBusy(""); }
  };

  const toggle = async (job: Flow) => {
    if (busy) return;
    setBusy(job.flow_id); setError(""); setNotice("");
    try {
      const { flow } = await (job.status === "active" ? pauseFlow(job.flow_id) : resumeFlow(job.flow_id));
      setJobs((previous) => previous.map((item) => item.flow_id === flow.flow_id ? flow : item));
      setConfirmEnable(null);
      setNotice(flow.status === "active" ? (flow.next_run_at ? "Schedule enabled. It will wake at its next scheduled time." : "Schedule enabled, but no next wake-up is available. Check that the scheduler is running.") : "Schedule paused. A run already in progress is not stopped.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not update schedule"); }
    finally { setBusy(""); }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-2xl max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="pr-6 break-words">{agent.name}: scheduled work</DialogTitle>
          <DialogDescription>Each job uses its creator’s account. Results are visible to the owner and workspace admins.</DialogDescription>
        </DialogHeader>
        {error && <Alert variant="destructive" role="alert"><AlertDescription>{error}</AlertDescription></Alert>}
        {notice && <p role="status" className="text-sm rounded-lg bg-muted p-3">{notice}</p>}
        {editing ? (
          <form onSubmit={save} className="space-y-4">
            <div className="space-y-1.5"><Label htmlFor="work-name">What job should this agent do?</Label><Input id="work-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Prepare my morning meeting brief" required maxLength={120} autoFocus /></div>
            <div className="space-y-1.5"><Label htmlFor="work-prompt">Instructions and expected result</Label><Textarea id="work-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Review today's meetings and prepare a short brief with attendees, context and questions." required maxLength={8000} rows={4} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5"><Label htmlFor="work-frequency">Repeat</Label><select id="work-frequency" className={selectClass} value={frequency} onChange={(e) => setFrequency(e.target.value)}><option value="weekdays">Weekdays (Mon–Fri)</option><option value="daily">Every day</option></select></div>
              <div className="space-y-1.5"><Label htmlFor="work-time">Wake-up time</Label><Input id="work-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} required /></div>
            </div>
            <div className="space-y-1.5"><Label htmlFor="work-zone">Timezone</Label><Input id="work-zone" value={zone} onChange={(e) => { setZone(e.target.value); setError(""); }} required placeholder="Asia/Kolkata" /><p className="text-xs text-muted-foreground">Uses local time in this timezone, including daylight-saving changes.</p></div>
            <div className="rounded-lg border bg-muted/30 p-3 text-sm space-y-2">
              <p className="flex items-center gap-2"><RiTimeLine size={16} />{frequency === "weekdays" ? "Monday to Friday" : "Every day"} at {time}, {zone}</p>
              <p className="break-all">Runs as <strong>{user?.email}</strong></p>
              <p className="text-xs text-muted-foreground">Pins the current agent instructions. Later agent edits will not change this job. Results are available to you and workspace admins in run history.</p>
              <p className="text-xs text-muted-foreground">This uses existing Flow access. Tool selections are guidance, not enforced permissions. There is no approval gate yet; only schedule work you already authorize.</p>
            </div>
            <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={!!busy} onClick={() => { setEditing(false); setName(""); setPrompt(""); setError(""); }}>Cancel</Button><Button type="submit" disabled={!!busy || !name.trim() || !prompt.trim()}>{busy === "save" ? "Saving…" : "Save paused schedule"}</Button></div>
          </form>
        ) : (
          <div className="space-y-3">
            <div className="flex justify-between items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading || !!busy}>Refresh</Button>
              {hasRole("operator") && <Button size="sm" onClick={() => { setEditing(true); setError(""); setNotice(""); }}><RiAddLine size={16} />New schedule</Button>}
            </div>
            {loading ? <p role="status" className="py-6 text-center text-sm text-muted-foreground">Loading scheduled work…</p> : jobs.length === 0 && error ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Schedules could not be loaded. Use Refresh to try again.</p>
            ) : jobs.length === 0 ? (
              <div className="py-8 text-center space-y-2"><RiTimeLine className="mx-auto text-muted-foreground" size={26} /><p className="text-sm font-medium">No scheduled work yet</p><p className="text-sm text-muted-foreground">Give this agent a repeatable job. It wakes, works, then sleeps.</p>{!hasRole("operator") && <p className="text-xs text-muted-foreground">Operator access is required to create schedules.</p>}</div>
            ) : jobs.map((job) => (
              <section key={job.flow_id} aria-label={job.name} className="rounded-xl border p-4 space-y-3 min-w-0">
                <div className="flex items-start justify-between gap-2"><h3 className="text-sm font-medium break-words min-w-0">{job.name}</h3><span className="text-xs rounded-full bg-muted px-2 py-1 shrink-0">{job.status === "active" ? "Enabled" : job.status === "paused" ? "Paused" : "Completed"}</span></div>
                <p className="text-xs text-muted-foreground break-words">{job.frequency || job.cron} · {job.timezone}</p>
                <p className="text-xs break-all">Account: {job.run_as}</p>
                <p className="text-xs text-muted-foreground">{job.status !== "active" ? "No automatic wake-ups while paused." : job.next_run_at ? `Next wake-up: ${date(job.next_run_at, job.timezone)}` : "Next wake-up not available. Check that the scheduler is running."}</p>
                <p className="text-xs text-muted-foreground">{job.last_run_at ? `Last attempt: ${date(job.last_run_at, job.timezone)}` : "Not run yet"} · {job.run_count} completed {job.run_count === 1 ? "run" : "runs"}</p>
                {job.last_error && <p role="alert" className="text-sm text-destructive break-words">Needs attention: {job.last_error}</p>}
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" asChild><Link href={`${basePath}/flows/${job.flow_id}`}>Details &amp; run history</Link></Button>
                  {job.can_manage && job.status !== "completed" && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => job.status === "active" ? void toggle(job) : setConfirmEnable(job.flow_id)}>{busy === job.flow_id ? "Saving…" : job.status === "active" ? "Pause schedule" : "Enable schedule"}</Button>}
                </div>
                {confirmEnable === job.flow_id && <div className="rounded-lg border bg-muted/30 p-3 text-sm space-y-3">
                  <p className="break-all">Enable recurring execution using {job.run_as}? Actions do not wait for approval.</p>
                  <p className="whitespace-pre-wrap break-words text-xs max-h-40 overflow-y-auto">{job.prompt}</p>
                  {job.agent_snapshot?.context && <details className="text-xs"><summary className="cursor-pointer">Review saved agent instructions</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words">{job.agent_snapshot.context}</pre></details>}
                  <div className="flex flex-wrap gap-2"><Button size="sm" disabled={!!busy} onClick={() => void toggle(job)}>Confirm &amp; enable</Button><Button variant="ghost" size="sm" disabled={!!busy} onClick={() => setConfirmEnable(null)}>Cancel</Button></div>
                </div>}
              </section>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
