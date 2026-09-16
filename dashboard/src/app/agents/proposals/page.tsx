"use client";

// Worker write/send proposals. A proposal is a request from an isolated
// worker; nothing is sent until you approve the exact version shown here.
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/bounded-work-api";
import { basePath } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";

type Cell = string | number | boolean;
type Args = Record<string, string | Cell[][]>;
type Reconciliation = { outcome: string; evidence: string; actor: string; at: string; version: number };
type Proposal = { proposal_id: string; conversation_id: string; action: string; args: Args; reason: string; owner: string; status: string; version: number; created_at: string; expires_at: string; receipt?: unknown; decided_by?: string; reconciliation?: Reconciliation };
type Listing = { proposals: Proposal[]; attention_limited?: boolean };

const labels: Record<string, string> = { "gmail.send": "Send email", "gmail.draft": "Create Gmail draft", "slack.send": "Send Slack message", "calendar.create": "Create calendar event", "docs.append": "Append to Google Doc", "sheets.write": "Write to Google Sheet" };
const fields: Record<string, string> = { to: "Recipient", cc: "Cc", subject: "Subject", body: "Message", channel: "Slack channel ID", text: "Message", thread_ts: "Thread", summary: "Event title", start: "Starts", end: "Ends", description: "Description", attendees: "Attendees", location: "Location", document_id: "Document ID", spreadsheet_id: "Spreadsheet ID", range: "Range", values: "Values (JSON rows)" };
const statusLabel = (status: string) => ({ pending: "Needs your decision", approved: "Approved", executing: "Executing", executed: "Executed", uncertain: "Outcome unknown", rejected: "Rejected", expired: "Expired", cancelled: "Cancelled" }[status] || status);
const target = (p: Proposal) => String(p.args.to ?? p.args.channel ?? p.args.summary ?? p.args.document_id ?? p.args.spreadsheet_id ?? "");
const show = (value: string | Cell[][]) => typeof value === "string" ? value : JSON.stringify(value);
const unresolved = (p: Proposal) => p.status === "uncertain" && !["sent", "not_sent"].includes(p.reconciliation?.outcome || "");

export default function ProposalsPage() {
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [warning, setWarning] = useState("");
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<Proposal | null>(null);
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [investigation, setInvestigation] = useState<Proposal | null>(null);
  const [outcome, setOutcome] = useState("unknown");
  const [evidence, setEvidence] = useState("");
  const load = useCallback(async () => { setData(await api<Listing>("proposals")); }, []);
  useEffect(() => { void load().catch(e => setError(e instanceof Error ? e.message : "Proposals could not load")); }, [load]);
  type Outcome = { notice?: string; warning?: string } | void;
  const act = async (fn: () => Promise<Outcome>, message: string) => {
    if (busy) return;
    setBusy(true); setError(""); setNotice(""); setWarning("");
    try { const outcome = await fn(); await load(); if (outcome?.warning) setWarning(outcome.warning); else setNotice(outcome?.notice || message); }
    catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); }
  };
  const pending = data?.proposals.filter(p => p.status === "pending" && new Date(p.expires_at) > new Date()) || [];
  const reviews = data?.proposals.filter(unresolved) || [];
  const history = data?.proposals.filter(p => !(p.status === "pending" && new Date(p.expires_at) > new Date()) && !unresolved(p)) || [];
  const decide = (decision: string) => act(async () => {
    if (!review) return;
    const args = decision === "edit" && edit ? Object.fromEntries(Object.entries(edit).map(([k, v]) => [k, k === "values" ? JSON.parse(v) : v])) : undefined;
    const result = await api<Proposal>(`proposals/${review.proposal_id}`, { decision, version: review.version, ...(args ? { args } : {}) });
    setReview(null); setEdit(null);
    if (decision === "approve" && result.status === "uncertain") return { warning: "Approved, but the outcome is unknown. Check the provider before doing anything else; it will not be retried." };
    if (decision === "approve" && result.status !== "executed") return { notice: `Approved. Current status: ${statusLabel(result.status)}.` };
  }, decision === "approve" ? "Approved and executed. The receipt is in history." : decision === "edit" ? "New version saved. Review it again before approving." : "Decision saved. Nothing was sent.");

  return <main className="mx-auto h-full w-full max-w-4xl space-y-6 overflow-y-auto p-4 pb-28 md:p-8 md:pb-12">
    <header><Link className="text-sm text-muted-foreground underline" href={`${basePath}/agents/work`}>Agent work</Link><h1 className="mt-2 text-2xl font-semibold">Worker proposals</h1><p className="mt-1 text-sm text-muted-foreground">Isolated workers can only propose sends and writes. Nothing leaves your account until you approve the exact version shown.</p></header>
    {error && <div role="alert" className="rounded-xl border border-destructive p-3 text-sm">{error}<Button variant="ghost" size="sm" onClick={() => void load().catch(e => setError(e.message))}>Retry</Button></div>}
    {warning && <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-amber-400 p-3 text-sm">{warning}<Button variant="ghost" size="sm" onClick={() => setWarning("")}>Dismiss</Button></div>}
    {notice && <div role="status" className="flex items-center justify-between gap-3 rounded-xl bg-muted p-3 text-sm">{notice}<Button variant="ghost" size="sm" onClick={() => setNotice("")}>Dismiss</Button></div>}
    {data?.attention_limited && <p role="status" className="rounded-lg border border-amber-300 p-3 text-sm">Large backlog: showing the oldest 200 pending proposals. Resolve these and refresh.</p>}
    {!data && !error && <p role="status">Loading proposals…</p>}
    {data && <section aria-label="Needs you" className="space-y-3">
      <h2 className="font-medium">Needs you ({pending.length + reviews.length})</h2>
      {pending.length + reviews.length === 0 && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No proposals are waiting for you.</p>}
      {reviews.map(p => <article key={p.proposal_id} className="min-w-0 space-y-2 rounded-xl border border-amber-400 p-4"><p className="text-xs font-semibold uppercase">⚠ Delivery review · Not an approval</p><h3 className="break-words font-medium">Did “{labels[p.action] || p.action}” to {target(p)} happen?</h3><p className="text-xs text-muted-foreground">No reliable receipt was saved. Do not repeat it before checking the provider.</p><Button variant="outline" onClick={() => { setInvestigation(p); setOutcome("unknown"); setEvidence(""); }}>Investigate outcome</Button></article>)}
      {pending.map(p => <article key={p.proposal_id} className="min-w-0 space-y-2 rounded-xl border border-amber-400 bg-amber-50/40 p-4 dark:bg-amber-950/10"><p className="text-xs font-semibold uppercase tracking-wide">✋ Proposal · Needs you</p><h3 className="break-words font-medium">{labels[p.action] || p.action} to {target(p)}</h3><p className="break-words text-sm">{p.reason}</p><p className="text-xs text-muted-foreground">Account: {p.owner} · Expires {new Date(p.expires_at).toLocaleString()}</p><Button variant="outline" onClick={() => { setReview(p); setEdit(null); }}>Review exact action</Button></article>)}
    </section>}
    {data && <section aria-label="History" className="space-y-3"><h2 className="font-medium">History</h2>{history.length === 0 && <p className="text-sm text-muted-foreground">Decided proposals and receipts appear here.</p>}{history.map(p => <details key={p.proposal_id} className="rounded-xl border p-3 text-sm"><summary className="cursor-pointer">{labels[p.action] || p.action} to {target(p)} · {statusLabel(p.status)}</summary><p className="mt-2 break-words">{p.reason}</p><p className="break-all text-xs">Account: {p.owner} · Version {p.version} · {new Date(p.created_at).toLocaleString()}</p><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{JSON.stringify(p.receipt || { status: p.status }, null, 2)}</pre>{p.reconciliation && <p className="mt-2 whitespace-pre-wrap break-words text-xs">Owner investigation: {p.reconciliation.outcome.replaceAll("_", " ")} · {p.reconciliation.evidence}</p>}</details>)}</section>}

    <Dialog open={!!review} onOpenChange={v => { if (!v && !busy) { setReview(null); setEdit(null); } }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>Review before authorizing</DialogTitle><DialogDescription>Approval applies only to this exact version and executes it from your account.</DialogDescription></DialogHeader>{review && <div className="space-y-4">
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <p className="text-sm">{review.reason}</p><dl className="space-y-2 text-sm"><div><dt className="font-medium">Account</dt><dd className="break-all">{review.owner}</dd></div><div><dt className="font-medium">Action</dt><dd>{labels[review.action] || review.action} · Version {review.version}</dd></div></dl>
      {Object.entries(edit || review.args).map(([key, value]) => <div key={key}><Label htmlFor={`review-${key}`}>{fields[key] || key}</Label>{edit ? <Textarea id={`review-${key}`} value={String(value)} onChange={e => setEdit({ ...edit, [key]: e.target.value })} rows={["body", "text", "description", "values"].includes(key) ? 8 : 2} /> : <p id={`review-${key}`} className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-muted p-3 text-sm">{show(value)}</p>}</div>)}
      <p className="text-xs text-muted-foreground">Expires {new Date(review.expires_at).toLocaleString()}. Editing creates a new version that must be reviewed again.</p>
      <div className="flex flex-wrap gap-2">{edit ? <><Button disabled={busy} onClick={() => void decide("edit")}>Save new version</Button><Button variant="outline" disabled={busy} onClick={() => setEdit(null)}>Cancel edit</Button></> : <><Button disabled={busy} onClick={() => void decide("approve")}>{busy ? "Working…" : `Approve & ${review.action.endsWith(".send") ? "send" : "apply"}`}</Button><Button variant="outline" disabled={busy} onClick={() => setEdit(Object.fromEntries(Object.entries(review.args).map(([k, v]) => [k, show(v)])))}>Edit proposal</Button><Button variant="ghost" disabled={busy} onClick={() => void decide("reject")}>Reject</Button></>}</div>
    </div>}</DialogContent></Dialog>

    <Dialog open={!!investigation} onOpenChange={v => { if (!v && !busy) setInvestigation(null); }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>Investigate outcome</DialogTitle><DialogDescription>Record what you verified with the provider. This never re-sends or authorizes a retry.</DialogDescription></DialogHeader>{investigation && <form className="space-y-4" onSubmit={e => { e.preventDefault(); void act(async () => { await api(`proposals/${investigation.proposal_id}/reconcile`, { version: investigation.reconciliation?.version || 0, outcome, evidence }); setInvestigation(null); }, "Investigation recorded. Nothing was re-sent."); }}>
      <details><summary className="cursor-pointer text-sm underline">Review exact action and receipt</summary>{Object.entries(investigation.args).map(([k, v]) => <p key={k} className="mt-2 whitespace-pre-wrap break-words text-sm"><span className="font-medium">{fields[k] || k}:</span> {show(v)}</p>)}<pre className="mt-2 whitespace-pre-wrap break-words text-xs">{JSON.stringify(investigation.receipt || {}, null, 2)}</pre></details>
      <div><Label htmlFor="outcome">What did you find?</Label><select id="outcome" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={outcome} onChange={e => setOutcome(e.target.value)}><option value="sent">It happened</option><option value="not_sent">It did not happen</option><option value="unknown">Still unknown</option></select></div>
      <div><Label htmlFor="evidence">Evidence or notes</Label><Textarea id="evidence" required value={evidence} onChange={e => setEvidence(e.target.value)} /></div>
      <Button disabled={busy}>Record investigation</Button>
    </form>}</DialogContent></Dialog>
  </main>;
}
