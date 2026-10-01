"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { basePath, type HumanTask } from "@/lib/api";

export function HumanTaskPanel({ conversationId }: { conversationId: string }) {
  const [task, setTask] = useState<(HumanTask & { title: string; assignee: string }) | null>(null);
  const [canRespond, setCanRespond] = useState(false);
  const [response, setResponse] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = `${basePath}/human-task-api/${encodeURIComponent(conversationId)}`;
  const load = useCallback(async () => {
    const result = await fetch(url, { cache: "no-store" });
    const data = await result.json();
    if (!result.ok) throw new Error(data.error || "Unable to load request");
    setTask(data.task);
    setCanRespond(data.can_respond);
  }, [url]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);

  const respond = async (decision: string) => {
    if (!task || !response.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, response: response.trim(), version: task.version }),
      });
      const data = await result.json();
      if (!result.ok) throw new Error(data.error || "Response not confirmed; refresh before retrying");
      setTask(data.task);
    } catch (e) { setError(e instanceof Error ? e.message : "Response not confirmed"); }
    finally { setBusy(false); }
  };

  return (
    <section className="flex-1 overflow-y-auto bg-muted/20 p-6" aria-label="Human task">
      <div className="mx-auto max-w-2xl space-y-5">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Human response required</p>
          <h2 className="mt-1 text-xl font-semibold">{task?.title || "Loading request..."}</h2>
        </div>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {task && <>
          <div className="rounded-xl border bg-card p-5 space-y-4">
            <p className="text-sm text-muted-foreground">Requested by {task.requested_by} · Assigned to {task.assignee}</p>
            <p className="whitespace-pre-wrap text-sm">{task.details}</p>
            <div className="flex flex-wrap gap-4 text-sm text-brand-600">
              <a href={`${basePath}/chat?continue=${encodeURIComponent(task.source_conversation_id)}`}>Source conversation</a>
              {task.ticket_url && <a href={task.ticket_url} target="_blank" rel="noopener noreferrer">Customer ticket</a>}
            </div>
          </div>
          {task.state === "pending" ? <>
            <p className="text-sm text-muted-foreground">Review the exact request above. Only an explicit approval authorizes it. Completing a board task or providing information is not approval.</p>
            {canRespond ? <>
              <label htmlFor="human-task-response" className="block text-sm font-medium">Your decision notes or requested information</label>
              <Textarea id="human-task-response" value={response} onChange={e => setResponse(e.target.value)} maxLength={10000} rows={5} disabled={busy} />
              <div className="flex flex-wrap gap-2">
                {task.kind === "approval" && <>
                  <Button disabled={busy || !response.trim()} onClick={() => respond("approve")}>Approve</Button>
                  <Button variant="destructive" disabled={busy || !response.trim()} onClick={() => respond("reject")}>Reject</Button>
                </>}
                <Button variant="outline" disabled={busy || !response.trim()} onClick={() => respond("provide_information")}>Provide information</Button>
              </div>
            </> : <p className="text-sm">Waiting for the assigned person to respond.</p>}
          </> : <div className="rounded-xl border bg-card p-5 space-y-2" role="status">
            <p className="font-medium">Response saved: {task.decision?.replaceAll("_", " ")}</p>
            <p className="text-sm text-muted-foreground">{task.responded_by}</p>
            <p className="whitespace-pre-wrap text-sm">{task.response}</p>
            <p className="text-sm">Agent continuation: {task.resume_state.replaceAll("_", " ")}</p>
            {task.resume_state === "needs_review" && <p className="text-sm text-destructive">Check the source conversation and external records before continuing manually. Some actions may already have succeeded.</p>}
          </div>}
        </>}
        <Button variant="ghost" disabled={busy} onClick={() => load().then(() => setError("")).catch(e => setError(e.message))}>Refresh status</Button>
      </div>
    </section>
  );
}
