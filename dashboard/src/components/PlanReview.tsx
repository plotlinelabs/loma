"use client";

import { Children, isValidElement, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { RiChat3Line, RiCloseLine, RiPlayLine, RiRefreshLine } from "@remixicon/react";
import { diffPlans, parsePlan, slug } from "@/lib/plan-doc";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { Artifact } from "./ArtifactViewer";
import { markdownComponents } from "./MarkdownCodeBlock";

export type PlanStatus = "pending" | "approved" | "superseded";

export interface PlanComment {
  id: string;
  /** The passage the comment is about; empty for a general note. */
  quote: string;
  text: string;
}

export interface PlanActions {
  status: PlanStatus;
  /** False while the agent is still running, so a review can't race it. */
  canAct: boolean;
  onRevise: (plan: Artifact, comments: PlanComment[]) => void;
  onApprove: (plan: Artifact, notes: PlanComment[]) => void;
  /** Every version of this plan in the chat, oldest first, and how to open one. */
  versions?: Artifact[];
  onOpenVersion?: (plan: Artifact) => void;
}

/** Plain text of rendered heading children, for its id. */
function textOf(children: ReactNode): string {
  return Children.toArray(children).map((child) =>
    typeof child === "string" || typeof child === "number" ? String(child)
      : isValidElement<{ children?: ReactNode }>(child) ? textOf(child.props.children) : "",
  ).join("");
}

/** Headings carry ids so the outline can jump to them. */
const planComponents = {
  ...markdownComponents,
  h2: ({ children }: { children?: ReactNode }) => <h2 id={`plan-${slug(textOf(children))}`} className="scroll-mt-4">{children}</h2>,
};

const draftKey = (planId: string) => `loma.plan-comments.${planId}`;

function loadDraft(planId: string): PlanComment[] {
  try {
    const raw = localStorage.getItem(draftKey(planId));
    return raw ? (JSON.parse(raw) as PlanComment[]) : [];
  } catch {
    return [];
  }
}

function saveDraft(planId: string, comments: PlanComment[]) {
  try {
    if (comments.length) localStorage.setItem(draftKey(planId), JSON.stringify(comments));
    else localStorage.removeItem(draftKey(planId));
  } catch {
    // Drafts are a convenience; the review still works without storage.
  }
}

/** The message that asks the agent for the next version of a plan. */
export function revisionMessage(plan: Artifact, comments: PlanComment[]): string {
  const lines = comments.map((c, i) =>
    c.quote ? `${i + 1}. On "${c.quote}": ${c.text}` : `${i + 1}. ${c.text}`,
  );
  return `Revise the plan (v${plan.version}) with this feedback:\n\n${lines.join("\n")}`;
}

/** The message that approves a plan and leaves plan mode. */
export function approvalMessage(plan: Artifact, notes: PlanComment[] = []): string {
  const base = `Plan v${plan.version} approved. Go ahead and execute it.`;
  if (!notes.length) return base;
  const lines = notes.map((c) => (c.quote ? `- On "${c.quote}": ${c.text}` : `- ${c.text}`));
  return `${base} Apply these notes as you go:\n\n${lines.join("\n")}`;
}

const STATUS_LABEL: Record<PlanStatus, string> = {
  pending: "Awaiting review",
  approved: "Approved",
  superseded: "Superseded",
};

const STATUS_DOT: Record<PlanStatus, string> = {
  pending: "bg-amber-500",
  approved: "bg-green-500",
  superseded: "bg-muted-foreground/40",
};

export function PlanStatusDot({ status }: { status: PlanStatus }) {
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", STATUS_DOT[status])} aria-hidden />;
}

/**
 * Plan review, modelled on Claude's plan mode: read the plan, select any
 * passage to comment on it, then revise (stays in plan mode) or approve
 * (the agent executes the plan in the same conversation).
 */
export default function PlanReview({ plan, actions }: { plan: Artifact; actions?: PlanActions }) {
  const status = actions?.status ?? "pending";
  const reviewable = !!actions && status === "pending";
  const bodyRef = useRef<HTMLDivElement>(null);
  // The viewer keys this component by plan id, so a new plan starts fresh.
  const [comments, setComments] = useState<PlanComment[]>(() => loadDraft(plan.id));
  const [selection, setSelection] = useState<{ quote: string; top: number; left: number } | null>(null);
  const [draftQuote, setDraftQuote] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [note, setNote] = useState("");

  const updateComments = useCallback((next: PlanComment[]) => {
    setComments(next);
    saveDraft(plan.id, next);
  }, [plan.id]);

  // Offer "Comment" next to any text selected inside the plan.
  const handleMouseUp = () => {
    if (!reviewable) return;
    const sel = window.getSelection();
    const quote = sel?.toString().trim() || "";
    const body = bodyRef.current;
    if (!quote || !sel?.rangeCount || !body || !body.contains(sel.anchorNode)) {
      setSelection(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const host = body.getBoundingClientRect();
    // Centre under the selection, kept inside the panel.
    const left = Math.min(Math.max(rect.left + rect.width / 2 - host.left, 60), host.width - 60);
    setSelection({ quote: quote.slice(0, 280), top: rect.bottom - host.top + body.scrollTop + 4, left });
  };

  const addComment = (quote: string, text: string) => {
    if (!text.trim()) return;
    updateComments([...comments, { id: crypto.randomUUID(), quote, text: text.trim() }]);
  };

  const pending = note.trim()
    ? [...comments, { id: "note", quote: "", text: note.trim() }]
    : comments;

  const doc = useMemo(() => parsePlan(plan.content), [plan.content]);
  const versions = actions?.versions ?? [];
  const index = versions.findIndex((v) => v.id === plan.id);
  const previous = index > 0 ? versions[index - 1] : null;
  const diff = useMemo(() => (previous ? diffPlans(previous.content, plan.content) : null), [previous, plan.content]);
  const [showChanges, setShowChanges] = useState(false);
  const jump = (id: string) => document.getElementById(`plan-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  // The RFC meta line (Author is always Loma, so it's left out), or an older plan's chips.
  const chips = [
    ...doc.meta.filter((field) => field.label !== "Author"),
    doc.size && { label: "Size", value: doc.size },
    doc.risk && { label: "Risk", value: doc.risk },
    doc.touches && { label: "Touches", value: doc.touches },
  ].filter((chip): chip is { label: string; value: string } => !!chip);

  return (
    <div className="flex h-full flex-col">
      <div ref={bodyRef} className="@container relative min-h-0 flex-1 overflow-y-auto p-3 md:p-6" onMouseUp={handleMouseUp}>
        <div className="mx-auto flex max-w-[46rem] flex-wrap items-center gap-2 text-[12px] text-muted-foreground @5xl:max-w-[62rem]">
          <PlanStatusDot status={status} />
          <span>{STATUS_LABEL[status]}</span>
          {versions.length > 1 ? (
            <span role="group" aria-label="Plan versions" className="flex overflow-hidden rounded-md border border-border">
              {versions.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  aria-pressed={v.id === plan.id}
                  onClick={() => v.id !== plan.id && actions?.onOpenVersion?.(v)}
                  className={cn("px-2 py-0.5 text-[12px]", v.id === plan.id ? "bg-foreground text-background" : "hover:bg-muted")}
                >
                  v{v.version}
                </button>
              ))}
            </span>
          ) : (
            <span>v{plan.version}</span>
          )}
          {diff && (
            <button
              type="button"
              aria-expanded={showChanges}
              onClick={() => setShowChanges((on) => !on)}
              className="rounded-md bg-muted px-2 py-0.5 text-[12px] text-foreground hover:bg-muted/70"
            >
              What changed in v{plan.version}
            </button>
          )}
          {reviewable && <span className="ml-auto hidden md:inline">Select text to comment</span>}
        </div>

        {diff && showChanges && (
          <div className="mx-auto mt-3 max-w-[46rem] space-y-1.5 rounded-lg border border-border bg-muted/40 p-3 text-[13px] @5xl:max-w-[62rem]">
            {diff.note && <p className="text-foreground">{diff.note}</p>}
            {[["Edited", diff.changed], ["Added", diff.added], ["Removed", diff.removed]].map(([label, names]) =>
              (names as string[]).length ? (
                <p key={label as string} className="flex flex-wrap items-center gap-1.5">
                  <span className="text-muted-foreground">{label as string}:</span>
                  {(names as string[]).map((name) => (
                    <button key={name} type="button" onClick={() => jump(slug(name))} className="rounded bg-background px-1.5 py-0.5 hover:underline">{name}</button>
                  ))}
                </p>
              ) : null,
            )}
            {!diff.note && !diff.changed.length && !diff.added.length && !diff.removed.length && (
              <p className="text-muted-foreground">Only wording changed.</p>
            )}
          </div>
        )}

        <header className="mx-auto mt-4 max-w-[46rem] space-y-3 @5xl:max-w-[62rem]">
          <h1 className="text-[24px] font-semibold leading-tight text-foreground">{doc.title}</h1>
          {doc.summary && <p className="text-[15px] leading-relaxed text-foreground/85">{doc.summary}</p>}
          {doc.decision && (
            <p className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-[14px] leading-snug text-foreground">
              <b className="font-semibold text-brand-700">Decision asked:</b> {doc.decision}
            </p>
          )}
          {(chips.length > 0 || doc.questions.length > 0) && (
            <div className="flex flex-wrap gap-1.5 text-[12px]">
              {chips.map((chip) => (
                <span key={chip.label} className="rounded-full bg-muted px-2.5 py-1 text-foreground/80">
                  <span className="text-muted-foreground">{chip.label}:</span> {chip.value}
                </span>
              ))}
              {doc.questions.length > 0 && (
                <button
                  type="button"
                  onClick={() => jump(slug(doc.sections.find((sec) => /question/i.test(sec.heading))?.heading ?? ""))}
                  className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-900 hover:bg-amber-200 dark:bg-amber-500/20 dark:text-amber-200"
                >
                  {doc.questions.length} {doc.questions.length === 1 ? "question" : "questions"} for you
                </button>
              )}
            </div>
          )}
          {doc.sections.length > 2 && (
            <nav aria-label="On this plan" className="flex flex-wrap gap-1.5 @5xl:hidden">
              {doc.sections.map((sec) => (
                <button key={sec.id} type="button" onClick={() => jump(sec.id)} className="rounded-md border border-border px-2 py-0.5 text-[12px] text-muted-foreground hover:text-foreground">
                  {sec.heading}
                </button>
              ))}
            </nav>
          )}
        </header>

        <div className="mx-auto mt-4 flex max-w-[46rem] gap-8 @5xl:max-w-[62rem]">
          {doc.sections.length > 2 && (
            <nav aria-label="On this plan" className="sticky top-0 hidden h-fit w-44 shrink-0 space-y-1 pt-2 text-[13px] @5xl:block">
              <div className="pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">On this plan</div>
              {doc.sections.map((sec) => (
                <button key={sec.id} type="button" onClick={() => jump(sec.id)} className="block w-full truncate text-left text-muted-foreground hover:text-foreground">
                  {sec.heading}
                </button>
              ))}
            </nav>
          )}
          <div className="prose prose-sm min-w-0 flex-1 prose-code:before:content-none prose-code:after:content-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={planComponents}>{doc.body}</ReactMarkdown>
          </div>
        </div>

        {selection && draftQuote === null && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="absolute -translate-x-1/2 shadow-md"
            style={{ top: selection.top, left: selection.left }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setDraftQuote(selection.quote);
              setDraftText("");
              setSelection(null);
            }}
          >
            <RiChat3Line size={14} /> Comment
          </Button>
        )}

        {comments.length > 0 && (
          <div className="mx-auto mt-6 max-w-[46rem] space-y-2">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Your comments</div>
            {comments.map((c) => (
              <div key={c.id} className="group/comment flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2 text-[13px]">
                <div className="min-w-0 flex-1">
                  {c.quote && <div className="mb-0.5 truncate border-l-2 border-amber-500/60 pl-2 text-xs text-muted-foreground">{c.quote}</div>}
                  <div className="text-foreground">{c.text}</div>
                </div>
                {reviewable && (
                  <button
                    type="button"
                    aria-label="Remove comment"
                    onClick={() => updateComments(comments.filter((x) => x.id !== c.id))}
                    className="touch-target rounded p-1 text-muted-foreground opacity-0 hover:text-foreground group-hover/comment:opacity-100 pointer-coarse:opacity-100"
                  >
                    <RiCloseLine size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {draftQuote !== null && (
        <div className="border-t border-border p-3">
          <div className="mb-1.5 truncate border-l-2 border-amber-500/60 pl-2 text-xs text-muted-foreground">{draftQuote}</div>
          <Textarea
            autoFocus
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                addComment(draftQuote, draftText);
                setDraftQuote(null);
              }
              if (e.key === "Escape") setDraftQuote(null);
            }}
            placeholder="What should change here?"
            rows={2}
            className="text-[13px]"
          />
          <div className="mt-2 flex justify-end gap-1.5">
            <Button type="button" variant="ghost" size="sm" onClick={() => setDraftQuote(null)}>Cancel</Button>
            <Button
              type="button"
              size="sm"
              disabled={!draftText.trim()}
              onClick={() => {
                addComment(draftQuote, draftText);
                setDraftQuote(null);
              }}
            >
              Add comment
            </Button>
          </div>
        </div>
      )}

      {reviewable && draftQuote === null && (
        <div className="border-t border-border p-3">
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Anything else to change?"
            rows={1}
            className="min-h-0 text-[13px]"
          />
          <div className="mt-2 flex items-center justify-end gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!actions.canAct || pending.length === 0}
              onClick={() => {
                actions.onRevise(plan, pending);
                updateComments([]);
                setNote("");
              }}
            >
              <RiRefreshLine size={14} />
              Request changes{pending.length ? ` (${pending.length})` : ""}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!actions.canAct}
              onClick={() => {
                actions.onApprove(plan, pending);
                updateComments([]);
                setNote("");
              }}
            >
              <RiPlayLine size={14} />
              Approve and build
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
