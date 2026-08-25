"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RiLoader4Line, RiPlayLine, RiHistoryLine, RiFlaskLine } from "@remixicon/react";

import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { CaseEditor, ResultsTable } from "@/components/prompt-eval/shared";
import { CsvUpload } from "@/components/prompt-eval/CsvUpload";
import { useEvalRunPolling } from "@/lib/useEvalRunPolling";
import type { PromptSetting, PromptSettingKey } from "@/lib/prompt-settings-api";
import {
  EVALUABLE_SETTING_KEYS,
  type EvalTestCase,
  type EvalSuite,
  type PromptVersion,
  createSuite,
  getSuite,
  getLatestRunForSuite,
  listSuites,
  updateSuiteCases,
  runSuite,
  promoteDraft,
  fetchPromptHistory,
  fetchPromptDiff,
} from "@/lib/prompt-eval-api";

const SETTING_TITLES: Record<string, string> = {
  identity_guidelines: "Identity & Guidelines",
  company_information: "Company Information",
};

// ------------------------------------------------------------- history --- //

function HistoryPanel({ settingKey }: { settingKey: string }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<PromptVersion[] | null>(null);
  const [diff, setDiff] = useState<{ versionId: string; text: string } | null>(null);

  const load = async () => {
    setOpen(true);
    if (history === null) {
      try {
        setHistory(await fetchPromptHistory(settingKey));
      } catch {
        setHistory([]);
      }
    }
  };

  const viewDiff = async (versionId: string) => {
    try {
      const text = await fetchPromptDiff(settingKey, versionId, "HEAD");
      setDiff({ versionId, text: text || "(no changes vs. the live version)" });
    } catch (e) {
      setDiff({ versionId, text: e instanceof Error ? e.message : "Failed to load diff" });
    }
  };

  return (
    <div className="mt-3">
      <Button variant="ghost" size="xs" onClick={() => (open ? setOpen(false) : load())}>
        <RiHistoryLine size={13} className="mr-1" /> {open ? "Hide history" : "View history"}
      </Button>
      {open && (
        <div className="mt-2 border border-border rounded-lg divide-y divide-border">
          {history === null ? (
            <div className="p-3 text-xs text-muted-foreground">Loading…</div>
          ) : history.length === 0 ? (
            <div className="p-3 text-xs text-muted-foreground">No saved versions yet.</div>
          ) : (
            history.map((v) => (
              <div key={v.version_id} className="p-2.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-foreground">{v.message}</span>
                  <Button variant="ghost" size="xs" onClick={() => viewDiff(v.version_id)}>
                    Diff vs. live
                  </Button>
                </div>
                <div className="text-muted-foreground mt-0.5">
                  {v.actor_email || "unknown"} · {new Date(v.created_at).toLocaleString()}
                  {v.eval_run_id && " · via prompt eval"}
                </div>
                {diff?.versionId === v.version_id && (
                  <pre className="mt-2 p-2 bg-surface-2 rounded text-[11px] overflow-x-auto whitespace-pre-wrap">
                    {diff.text}
                  </pre>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- panel --- //

interface PromptEvalPanelProps {
  promptSettings: PromptSetting[];
  promptDrafts: Record<string, string>;
  onDraftChange: (key: PromptSettingKey, value: string) => void;
  onPromoted: () => void | Promise<void>;
}

export default function PromptEvalPanel({
  promptSettings,
  promptDrafts,
  onDraftChange,
  onPromoted,
}: PromptEvalPanelProps) {
  // Same fallback rule as admin/page.tsx's getPromptDraft — an unsaved edit
  // wins, otherwise fall back to the live content. This is the same shared
  // state the Core Prompt card above reads and writes, not a copy: editing
  // the "Draft" box here or the textarea above updates the same value.
  const getDraft = (key: PromptSettingKey): string =>
    promptDrafts[key] ?? promptSettings.find((s) => s.setting_key === key)?.content ?? "";

  const [settingKey, setSettingKey] = useState<(typeof EVALUABLE_SETTING_KEYS)[number]>(
    EVALUABLE_SETTING_KEYS[0],
  );
  const [suite, setSuite] = useState<EvalSuite | null>(null);
  const [hadPriorEval, setHadPriorEval] = useState<boolean | null>(null);
  const [cases, setCases] = useState<EvalTestCase[]>([]);
  const [loadingSuite, setLoadingSuite] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { run, start: startPolling, reset: resetRun, isPolling } = useEvalRunPolling();
  const running = submitting || isPolling;

  // Load (or create) the suite for the selected section, and whether any
  // eval has ever been run for it before — the source of truth for that is
  // the backend (eval.service.has_eval_run_for_setting), not "did I just
  // run one in this session" (see the bug this replaced: hadPriorEval used
  // to be set unconditionally to true right after any run, so the "no eval
  // has ever been run" banner could never actually show).
  useEffect(() => {
    let cancelled = false;
    setLoadingSuite(true);
    resetRun();
    setError(null);
    setHadPriorEval(null);
    (async () => {
      try {
        const suites = await listSuites({ subject_type: "loma", setting_key: settingKey });
        if (cancelled) return;
        if (suites.length > 0) {
          const detail = await getSuite(suites[0].suite_id);
          if (cancelled) return;
          setSuite(detail.suite);
          setCases(detail.suite.cases);
          setHadPriorEval(detail.had_prior_eval ?? false);
          // Re-hydrate this section's most recent run instead of leaving
          // the reset() above as the final word — Admin's Settings tab
          // unmounts this whole component when you switch to another
          // top-level tab (Radix Tabs' default lazy-unmount), which was
          // silently discarding a just-finished run's results on every
          // tab switch since the run only ever lived in local React state.
          const latestRun = await getLatestRunForSuite(detail.suite.suite_id);
          if (cancelled) return;
          if (latestRun) startPolling(latestRun);
        } else {
          // No suite has ever been created for this section — so no eval
          // could have been run for it either.
          setSuite(null);
          setCases([]);
          setHadPriorEval(false);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load test suite");
      } finally {
        if (!cancelled) setLoadingSuite(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [settingKey]);

  const saveCases = async (): Promise<EvalSuite> => {
    // Drop the still-empty placeholder row CaseEditor always keeps around —
    // otherwise it gets persisted here and CSV upload's server-side append
    // (suite_cases(suite) + new_cases) leaves it sitting in front of every
    // uploaded row.
    const persistedCases = cases.filter((c) => c.input.trim() !== "");
    let target = suite;
    if (!target) {
      target = await createSuite({
        subject_type: "loma",
        setting_key: settingKey,
        label: `${SETTING_TITLES[settingKey] || settingKey} — eval suite`,
        cases: persistedCases,
      });
    } else {
      target = await updateSuiteCases(target.suite_id, persistedCases);
    }
    setSuite(target);
    setCases(target.cases);
    return target;
  };

  const handleRun = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const savedSuite = await saveCases();
      const draft = getDraft(settingKey as PromptSettingKey);
      if (!draft.trim()) {
        throw new Error("This section's draft is empty — write something in the Core Prompt textarea above first.");
      }
      const pendingRun = await runSuite(savedSuite.suite_id, { draft_text: draft });
      startPolling(pendingRun); // 202 — poll from here until completed/failed
      // hadPriorEval intentionally untouched here — it reflects whether an
      // eval existed *before* this run (loaded when the section was
      // selected), which is exactly what the "no eval has been run before"
      // banner needs. This run itself doesn't retroactively change that.
    } catch (e) {
      setError(e instanceof Error ? e.message : "Eval run failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePromote = async () => {
    if (!run) return;
    // The Loma subject's variant assembly (api/prompt_eval_routes.py::_build_variants)
    // always names the draft variant "draft" — find it by id rather than
    // assuming array position.
    const draftVariant = run.variants.find((v) => v.variant_id === "draft");
    if (!draftVariant) {
      setError("Couldn't find the draft variant on this run — try running the evaluation again.");
      return;
    }
    setPromoting(true);
    setError(null);
    try {
      await promoteDraft({ setting_key: settingKey, content: draftVariant.prompt_text, run_id: run.run_id });
      await onPromoted();
      resetRun();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Promote failed");
    } finally {
      setPromoting(false);
    }
  };

  const currentSetting = promptSettings.find((s) => s.setting_key === settingKey);
  const currentDraft = getDraft(settingKey as PromptSettingKey);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="px-5 py-4 border-b border-border">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-[13px]">Prompt Evaluation</CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              Run a draft against test cases and compare it to what&apos;s live before promoting it.
            </p>
          </div>
          <Link
            href="/prompt-lab"
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground whitespace-nowrap mt-0.5"
          >
            <RiFlaskLine size={13} /> Testing an unrelated prompt? Try Prompt Lab
          </Link>
        </div>
      </CardHeader>
      <CardContent className="p-4 space-y-4">
        <div className="flex items-center gap-2">
          {EVALUABLE_SETTING_KEYS.map((key) => (
            <Button
              key={key}
              variant={key === settingKey ? "default" : "outline"}
              size="sm"
              onClick={() => setSettingKey(key)}
              className={key === settingKey ? "bg-accent-200 text-accent-on hover:bg-accent-300" : ""}
            >
              {SETTING_TITLES[key]}
            </Button>
          ))}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-foreground mb-1 block">
              Current <span className="font-normal text-muted-foreground">(live)</span>
            </label>
            <Textarea
              value={currentSetting?.content || ""}
              readOnly
              placeholder="(empty — nothing saved for this section yet)"
              className="min-h-[160px] text-[13px] font-mono bg-surface-2 text-muted-foreground cursor-default"
            />
            {currentSetting?.updated_at && (
              <p className="text-[11px] text-muted-foreground mt-1">
                Last updated {new Date(currentSetting.updated_at).toLocaleString()}
              </p>
            )}
          </div>
          <div>
            <label className="text-xs font-semibold text-foreground mb-1 block">
              Draft <span className="font-normal text-muted-foreground">(unsaved)</span>
            </label>
            <Textarea
              value={currentDraft}
              onChange={(e) => onDraftChange(settingKey as PromptSettingKey, e.target.value)}
              placeholder={`Edit ${SETTING_TITLES[settingKey]} here — same draft as the Core Prompt card above.`}
              className="min-h-[160px] text-[13px] font-mono"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              Same draft as the {SETTING_TITLES[settingKey]} textarea above — editing either one updates both.
            </p>
          </div>
        </div>

        {loadingSuite ? (
          <div className="flex items-center justify-center h-24">
            <RiLoader4Line size={24} className="animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-foreground mb-2">Test cases</h4>
              <CsvUpload
                ensureSuiteId={async () => (await saveCases()).suite_id}
                onUploaded={(uploadedSuite) => setCases(uploadedSuite.cases)}
              />
              <CaseEditor cases={cases} onChange={setCases} />
            </div>

            {error && <p className="text-xs text-red-500">{error}</p>}

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={running || cases.length === 0 || !currentDraft.trim()}
                onClick={handleRun}
                className="bg-accent-200 text-accent-on hover:bg-accent-300"
              >
                {running ? (
                  <>
                    <RiLoader4Line size={14} className="mr-1 animate-spin" /> Running…
                  </>
                ) : (
                  <>
                    <RiPlayLine size={14} className="mr-1" /> Run evaluation
                  </>
                )}
              </Button>
              {run && (
                <>
                  {hadPriorEval === false && (
                    <span className="text-[11px] text-amber-600">
                      No eval has been run for this section before.
                    </span>
                  )}
                  <Button size="sm" variant="outline" disabled={promoting} onClick={handlePromote}>
                    {promoting ? "Promoting…" : "Promote draft → live"}
                  </Button>
                </>
              )}
            </div>

            {run && (
              <div className="pt-2 border-t border-border">
                <ResultsTable run={run} />
              </div>
            )}

            <HistoryPanel settingKey={settingKey} />
          </>
        )}
      </CardContent>
    </Card>
  );
}
