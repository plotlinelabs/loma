"use client";

import { useEffect, useState } from "react";
import { RiAddLine, RiCloseLine, RiCheckLine, RiToolsLine } from "@remixicon/react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import type { EvalTestCase, EvalRun, EvalVariantResult, ToolCall } from "@/lib/prompt-eval-api";

// Shared between the Loma-subject panel (dashboard/src/components/admin/PromptEvalPanel.tsx)
// and the generic-subject Prompt Lab page (dashboard/src/app/prompt-lab/page.tsx) — this is
// the visible proof that eval/runner.run_eval() on the backend really is subject-agnostic:
// both surfaces render the exact same case editor and results table.

export function newLocalCase(): EvalTestCase {
  return {
    case_id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    input: "",
    expected_contains: [],
    expected_not_contains: [],
    rubric: "",
  };
}

function splitList(text: string): string[] {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}

// A plain <Input value={items.join(", ")} onChange={...splitList} /> re-derives
// the displayed text from the split array on every keystroke — the instant you
// type a comma, splitList()'s trailing-empty-token filter collapses it away
// before you can type the next item, so a comma can never actually stick.
// Typing stays free-form in local `raw` state; the array only gets recomputed
// (and re-normalized) on blur.
function TagListInput({
  value,
  onCommit,
  placeholder,
}: {
  value: string[];
  onCommit: (next: string[]) => void;
  placeholder: string;
}) {
  const [raw, setRaw] = useState(value.join(", "));

  useEffect(() => {
    setRaw(value.join(", "));
  }, [value]);

  return (
    <Input
      value={raw}
      onChange={(e) => setRaw(e.target.value)}
      onBlur={() => onCommit(splitList(raw))}
      placeholder={placeholder}
      className="text-xs"
    />
  );
}

export function CaseEditor({
  cases,
  onChange,
}: {
  cases: EvalTestCase[];
  onChange: (cases: EvalTestCase[]) => void;
}) {
  const update = (idx: number, patch: Partial<EvalTestCase>) => {
    const next = cases.slice();
    next[idx] = { ...next[idx], ...patch };
    onChange(next);
  };
  const remove = (idx: number) => onChange(cases.filter((_, i) => i !== idx));

  return (
    <div className="space-y-2">
      {cases.map((c, idx) => (
        <div key={c.case_id} className="border border-border rounded-lg p-3 space-y-2 bg-card">
          <div className="flex items-start gap-2">
            <Textarea
              value={c.input}
              onChange={(e) => update(idx, { input: e.target.value })}
              placeholder="What should the agent be asked? e.g. What's our refund policy?"
              className="flex-1 min-h-[52px] text-[13px]"
            />
            <Button variant="ghost" size="xs" onClick={() => remove(idx)} className="mt-1">
              <RiCloseLine size={14} />
            </Button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <TagListInput
              value={c.expected_contains}
              onCommit={(next) => update(idx, { expected_contains: next })}
              placeholder="Must contain (comma-separated, optional)"
            />
            <TagListInput
              value={c.expected_not_contains}
              onCommit={(next) => update(idx, { expected_not_contains: next })}
              placeholder="Must NOT contain (comma-separated, optional)"
            />
          </div>
          <Input
            value={c.rubric}
            onChange={(e) => update(idx, { rubric: e.target.value })}
            placeholder="Rubric for an LLM judge (optional) — e.g. Is the tone friendly and concise?"
            className="text-xs"
          />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...cases, newLocalCase()])}>
        <RiAddLine size={14} className="mr-1" /> Add test case
      </Button>
    </div>
  );
}

// Mirrors eval.decision.JUDGE_MAX_DISAGREEMENT on the backend — display only,
// the actual pass/fail call is always made server-side.
const JUDGE_MAX_DISAGREEMENT = 0.4;

function JudgeInfo({ judge }: { judge: { score: number | null; reasoning: string; samples: number[] } }) {
  if (!judge.reasoning) return null;
  const samples = judge.samples || [];
  if (samples.length < 2) {
    return (
      <p className="text-[11px] text-muted-foreground mt-1 italic">Judge: {judge.reasoning}</p>
    );
  }
  const spread = Math.max(...samples) - Math.min(...samples);
  const disagrees = spread > JUDGE_MAX_DISAGREEMENT;
  return (
    <div className="mt-1">
      <p className={disagrees ? "text-[11px] text-amber-600" : "text-[11px] text-muted-foreground"}>
        Judge: median {judge.score?.toFixed(2)} from {samples.length} samples
        {disagrees ? ` — samples disagree (spread ${spread.toFixed(2)})` : ""}
      </p>
      <p className="text-[11px] text-muted-foreground italic">{judge.reasoning}</p>
    </div>
  );
}

// Captured, not scored — see eval.schema.ToolCall. Makes the documented
// tool-permission risk (a test case's input can provoke a real tool call)
// visible per run instead of only documented in DESIGN.md.
function ToolCallsInfo({ calls }: { calls: ToolCall[] }) {
  if (!calls || calls.length === 0) return null;
  return (
    <div className="mt-1.5 border-l-2 border-amber-300 pl-2">
      {calls.map((c, i) => (
        <p key={i} className="text-[11px] text-amber-700">
          <RiToolsLine size={11} className="inline mr-1 -mt-0.5" />
          <b>{c.tool}</b>
          {c.input && Object.keys(c.input).length > 0 && (
            <span className="text-muted-foreground"> {JSON.stringify(c.input)}</span>
          )}
          {c.output && (
            <span className="block text-muted-foreground truncate" title={c.output}>
              → {c.output.slice(0, 200)}
            </span>
          )}
        </p>
      ))}
    </div>
  );
}

export function PassBadge({ pass }: { pass: boolean }) {
  return pass ? (
    <Badge variant="secondary" className="text-[10px] bg-emerald-50 text-emerald-600">
      <RiCheckLine size={11} className="mr-0.5" /> Pass
    </Badge>
  ) : (
    <Badge variant="secondary" className="text-[10px] bg-red-50 text-red-600">
      <RiCloseLine size={11} className="mr-0.5" /> Fail
    </Badge>
  );
}

function VariantCell({ vr }: { vr: EvalVariantResult | undefined }) {
  // Missing entirely (shouldn't happen once a case is scored, but a run
  // mid-flight — see the polling hook — can have cases with fewer variant
  // results than the run's own variant list while a case is still running).
  if (!vr) return <TableCell className="align-top text-muted-foreground text-xs">—</TableCell>;
  return (
    <TableCell className="align-top">
      <div className="flex items-center gap-1.5 mb-1">
        <PassBadge pass={vr.passed} />
        {vr.metric_results?.composite_score?.score != null && (
          <span
            className="text-[10px] font-medium text-foreground"
            title="Composite quality score — judge confidence blended with latency, see DESIGN.md"
          >
            {vr.metric_results.composite_score.score.toFixed(2)}
          </span>
        )}
        {vr.latency_ms != null && (
          <span className="text-[10px] text-muted-foreground">{Math.round(vr.latency_ms)}ms</span>
        )}
        {vr.cost_usd != null && (
          <span className="text-[10px] text-muted-foreground">${vr.cost_usd.toFixed(4)}</span>
        )}
      </div>
      <p className="whitespace-pre-wrap text-foreground">{vr.response}</p>
      {vr.failures.length > 0 && (
        <p className="text-[11px] text-red-500 mt-1">{vr.failures.join(", ")}</p>
      )}
      {vr.judge && <JudgeInfo judge={vr.judge} />}
      <ToolCallsInfo calls={vr.tool_calls} />
    </TableCell>
  );
}

export function ResultsTable({ run }: { run: EvalRun }) {
  const summary = run.summary;
  const columnWidth = `${Math.floor(70 / Math.max(run.variants.length, 1))}%`;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span>
          {run.case_results.length}
          {run.total_cases && run.total_cases !== run.case_results.length ? `/${run.total_cases}` : ""} case
          {run.case_results.length === 1 ? "" : "s"}
          {run.status && run.status !== "completed" && (
            <span className="ml-1 text-amber-600">({run.status})</span>
          )}
        </span>
        {summary?.variants.map((vs) => (
          <span key={vs.variant_id}>
            {vs.label}: <b className="text-foreground">{Math.round(vs.pass_rate * 100)}%</b> pass
            {vs.avg_composite_score != null && (
              <>
                {" "}
                · quality <b className="text-foreground">{vs.avg_composite_score.toFixed(2)}</b>
              </>
            )}
            {vs.avg_judge_score != null && <> · judge {vs.avg_judge_score.toFixed(2)}</>}
          </span>
        ))}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead style={{ width: "30%" }}>Input</TableHead>
            {run.variants.map((v) => (
              <TableHead key={v.variant_id} style={{ width: columnWidth }}>
                {v.label}
                <span className="block font-normal text-[10px] text-muted-foreground">
                  {v.model} · {v.agent_profile}
                </span>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.case_results.map((r) => {
            // Look up by variant_id, not array index — a run's variant_results
            // for a given case aren't guaranteed to be in the same order as
            // run.variants (asyncio.gather preserves order today, but this
            // stays correct even if that ever changes).
            const byVariantId = new Map(r.variant_results.map((vr) => [vr.variant_id, vr]));
            return (
              <TableRow key={r.case_id}>
                <TableCell className="align-top whitespace-pre-wrap">{r.input}</TableCell>
                {run.variants.map((v) => (
                  <VariantCell key={v.variant_id} vr={byVariantId.get(v.variant_id)} />
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
