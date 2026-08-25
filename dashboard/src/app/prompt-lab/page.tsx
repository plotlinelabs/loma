"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { RiLoader4Line, RiPlayLine, RiAddLine, RiCloseLine } from "@remixicon/react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CaseEditor, ResultsTable } from "@/components/prompt-eval/shared";
import { CsvUpload } from "@/components/prompt-eval/CsvUpload";
import { useEvalRunPolling } from "@/lib/useEvalRunPolling";
import { useUser } from "@/lib/UserContext";
import { type AgentModel, fetchAgentModels } from "@/lib/api";
import { type EvalTestCase, createSuite, updateSuiteCases, runSuite, getLatestRunForSuite } from "@/lib/prompt-eval-api";

const SCRATCH_KEY = "loma:prompt-eval:generic-scratch-v2";
const SUITE_DEFAULT_MODEL_VALUE = "__suite_default__";

// Mirrors agent.opencode_runtime.AGENT_PROFILES — the only way to vary
// temperature here, since OpenCode has no per-request temperature
// parameter (it's a static property of a pre-registered agent config).
// Keep this list in sync with that dict if a profile is added/removed.
const AGENT_PROFILE_OPTIONS = [
  { id: "default", label: "Default", description: "No explicit temperature override" },
  { id: "precise", label: "Precise", description: "Lower temperature (0.2) — more deterministic" },
  { id: "balanced", label: "Balanced", description: "Higher temperature (0.7) — more varied" },
];

interface LocalVariant {
  id: string;
  label: string;
  prompt_text: string;
  model: string;
  agent_profile: string;
}

function newVariant(label: string): LocalVariant {
  return {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    label,
    prompt_text: "",
    model: "",
    agent_profile: "default",
  };
}

export default function PromptLabPage() {
  const { hasRole, loading: userLoading } = useUser();
  const isMaintainerOrAbove = hasRole("maintainer");
  const router = useRouter();

  const [suiteId, setSuiteId] = useState<string | null>(null);
  const [variants, setVariants] = useState<LocalVariant[]>([
    newVariant("Current"),
    newVariant("Draft"),
  ]);
  const [cases, setCases] = useState<EvalTestCase[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { run, start: startPolling, isPolling } = useEvalRunPolling();
  const running = submitting || isPolling;

  // Every variant runs through agent.opencode_runtime.run_opencode_oneshot(),
  // which only understands OpenCode-routable models — filter out the
  // Claude Agent SDK ("anthropic") and Codex entries /api/agent-models also
  // returns for the chat composer, since those run through a completely
  // different runtime and would just fail live if picked here.
  const [agentModels, setAgentModels] = useState<AgentModel[]>([]);
  const [modelsLoadState, setModelsLoadState] = useState<"loading" | "ready" | "error">("loading");
  const opencodeModels = agentModels.filter((m) => m.provider_id !== "anthropic" && m.provider_id !== "codex");
  const groupedOpencodeModels = Object.values(
    opencodeModels.reduce<Record<string, { providerId: string; models: AgentModel[] }>>((groups, m) => {
      (groups[m.provider_id] ||= { providerId: m.provider_id, models: [] }).models.push(m);
      return groups;
    }, {}),
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const catalog = await fetchAgentModels();
        if (!cancelled) {
          setAgentModels(catalog.models || []);
          setModelsLoadState("ready");
        }
      } catch (e) {
        console.warn("Failed to load agent models", e);
        if (!cancelled) setModelsLoadState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!userLoading && !isMaintainerOrAbove) {
      router.replace("/");
    }
  }, [userLoading, isMaintainerOrAbove, router]);

  // Hydrate the scratch slot once on mount — pure client-side resilience
  // against an accidental refresh, nothing here is ever persisted
  // server-side beyond an individual run's own history. See DESIGN.md.
  // Bumped to a new storage key (-v2) since the shape changed from two
  // fixed fields to a variant list — an old payload just won't match and
  // this starts fresh rather than trying to migrate localStorage content.
  useEffect(() => {
    (async () => {
      try {
        const raw = window.localStorage.getItem(SCRATCH_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        if (Array.isArray(saved.variants) && saved.variants.length >= 2) {
          setVariants(saved.variants);
        }
        setCases(saved.cases || []);
        setSuiteId(saved.suiteId || null);
        // Re-hydrate the last run for this scratch suite too — otherwise a
        // reload (or, on other pages using this same pattern, a remount —
        // see PromptEvalPanel.tsx) silently discards a just-finished run's
        // results even though the suite/cases themselves survive.
        if (saved.suiteId) {
          const latestRun = await getLatestRunForSuite(saved.suiteId);
          if (latestRun) startPolling(latestRun);
        }
      } catch {
        // corrupt/old payload — start fresh
      }
    })();
  }, []);

  useEffect(() => {
    window.localStorage.setItem(SCRATCH_KEY, JSON.stringify({ variants, cases, suiteId }));
  }, [variants, cases, suiteId]);

  const updateVariant = (id: string, patch: Partial<LocalVariant>) => {
    setVariants((prev) => prev.map((v) => (v.id === id ? { ...v, ...patch } : v)));
  };
  const removeVariant = (id: string) => setVariants((prev) => prev.filter((v) => v.id !== id));
  const addVariant = () => setVariants((prev) => [...prev, newVariant(`Variant ${prev.length + 1}`)]);

  const ensureSuite = async () => {
    // Drop the still-empty placeholder row CaseEditor always keeps around —
    // otherwise it gets persisted here and CSV upload's server-side append
    // (suite_cases(suite) + new_cases) leaves it sitting in front of every
    // uploaded row.
    const persistedCases = cases.filter((c) => c.input.trim() !== "");
    if (suiteId) {
      try {
        return await updateSuiteCases(suiteId, persistedCases);
      } catch {
        // saved id is stale (deleted, or from a different deployment) — fall
        // through and create a fresh one
      }
    }
    const created = await createSuite({ subject_type: "generic", label: "Prompt Lab scratch", cases: persistedCases });
    setSuiteId(created.suite_id);
    return created;
  };

  const handleRun = async () => {
    setSubmitting(true);
    setError(null);
    try {
      if (variants.length < 2) {
        throw new Error("At least 2 variants are required to compare.");
      }
      const emptyPrompt = variants.find((v) => !v.prompt_text.trim());
      if (emptyPrompt) {
        throw new Error(`"${emptyPrompt.label}" needs prompt text.`);
      }
      const suite = await ensureSuite();
      const pendingRun = await runSuite(suite.suite_id, {
        variants: variants.map((v) => ({
          label: v.label,
          prompt_text: v.prompt_text,
          model: v.model.trim() || undefined,
          agent_profile: v.agent_profile.trim() || undefined,
        })),
      });
      startPolling(pendingRun); // 202 — poll from here until completed/failed
    } catch (e) {
      setError(e instanceof Error ? e.message : "Eval run failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (userLoading || !isMaintainerOrAbove) {
    return (
      <div className="flex items-center justify-center h-64">
        <RiLoader4Line size={24} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-2 animate-fade-in-up">
      <div>
        <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Prompt Lab</h1>
        <p className="text-[13px] text-muted-foreground mt-1">
          Evaluate any prompt — nothing to do with Loma&apos;s configuration. Compare 2 or more
          variants, each its own prompt text, model, and agent profile. Nothing here can be
          promoted; it&apos;s a one-off comparison, kept only in this run&apos;s history.
        </p>
      </div>

      <Card className="overflow-hidden">
        <CardContent className="p-4 space-y-4">
          <div className="space-y-3">
            {variants.map((v) => (
              <div key={v.id} className="border border-border rounded-lg p-3 space-y-2 bg-card">
                <div className="flex items-center gap-2">
                  <Input
                    value={v.label}
                    onChange={(e) => updateVariant(v.id, { label: e.target.value })}
                    placeholder="Label"
                    className="text-xs font-semibold flex-1"
                  />
                  {variants.length > 2 && (
                    <Button variant="ghost" size="xs" onClick={() => removeVariant(v.id)}>
                      <RiCloseLine size={14} />
                    </Button>
                  )}
                </div>
                <Textarea
                  value={v.prompt_text}
                  onChange={(e) => updateVariant(v.id, { prompt_text: e.target.value })}
                  placeholder="Paste this variant's system prompt…"
                  className="min-h-[120px] text-[13px] font-mono"
                />
                <div className="grid grid-cols-2 gap-2">
                  <Select
                    value={v.model || SUITE_DEFAULT_MODEL_VALUE}
                    onValueChange={(val) =>
                      updateVariant(v.id, { model: val === SUITE_DEFAULT_MODEL_VALUE ? "" : val })
                    }
                    disabled={modelsLoadState === "loading"}
                  >
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder={modelsLoadState === "loading" ? "Loading models…" : "Choose model"} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={SUITE_DEFAULT_MODEL_VALUE}>Suite default</SelectItem>
                      {groupedOpencodeModels.map(({ providerId, models }) => (
                        <SelectGroup key={providerId}>
                          <SelectLabel>{providerId}</SelectLabel>
                          {models.map((m) => (
                            <SelectItem key={m.id} value={m.id}>
                              {m.label}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={v.agent_profile || "default"}
                    onValueChange={(val) => updateVariant(v.id, { agent_profile: val })}
                  >
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="Agent profile" />
                    </SelectTrigger>
                    <SelectContent>
                      {AGENT_PROFILE_OPTIONS.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.label} — {p.description}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={addVariant}>
              <RiAddLine size={14} className="mr-1" /> Add variant
            </Button>
          </div>

          <div className="space-y-2">
            <h4 className="text-xs font-semibold text-foreground mb-2">Test cases</h4>
            <CsvUpload
              ensureSuiteId={async () => (await ensureSuite()).suite_id}
              onUploaded={(suite) => setCases(suite.cases)}
            />
            <CaseEditor cases={cases} onChange={setCases} />
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}

          <Button
            size="sm"
            disabled={running || cases.length === 0}
            onClick={handleRun}
            className="bg-accent-200 text-accent-on hover:bg-accent-300"
          >
            {running ? (
              <>
                <RiLoader4Line size={14} className="mr-1 animate-spin" /> Running…
              </>
            ) : (
              <>
                <RiPlayLine size={14} className="mr-1" /> Run comparison
              </>
            )}
          </Button>

          {run && (
            <div className="pt-2 border-t border-border">
              <ResultsTable run={run} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
