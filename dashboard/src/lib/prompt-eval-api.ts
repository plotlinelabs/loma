const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type SubjectType = "loma" | "generic";

// Mirrors agent.prompt.RULEBOOK_KEYS on the backend, not the full
// PROMPT_SETTING_KEYS shown on the Settings screen — dictation_vocabulary
// never enters the system prompt, so it isn't eval-able through this engine.
// See DESIGN.md.
export const EVALUABLE_SETTING_KEYS = ["identity_guidelines", "company_information"] as const;
export type EvaluableSettingKey = (typeof EVALUABLE_SETTING_KEYS)[number];

export interface EvalTestCase {
  case_id: string;
  input: string;
  expected_contains: string[];
  expected_not_contains: string[];
  rubric: string;
}

export interface EvalSuite {
  suite_id: string;
  subject_type: SubjectType;
  setting_key: string | null;
  label: string;
  cases: EvalTestCase[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface JudgeResult {
  score: number | null;
  reasoning: string;
  // Self-consistency: the individual sample scores the median was computed
  // from. Empty/single-element when every sample but one failed to parse.
  samples: number[];
}

export interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output: string;
  status: string;
}

// A pre-registered OpenCode agent name, e.g. "default" — the only way to
// vary temperature, since OpenCode has no per-request temperature
// parameter. See agent.opencode_runtime.AGENT_PROFILES on the backend.
export type AgentProfile = string;

export interface EvalVariant {
  variant_id: string;
  label: string;
  prompt_text: string;
  model: string;
  agent_profile: AgentProfile;
}

export interface MetricResult {
  metric: string;
  passed: boolean | null;
  score: number | null;
  detail: string;
  failures: string[];
}

export interface EvalVariantResult {
  variant_id: string;
  label: string;
  response: string;
  passed: boolean;
  judge: JudgeResult | null;
  failures: string[];
  // Captured, not scored — see eval.schema.ToolCall. Makes the documented
  // tool-permission risk visible per run instead of only documented.
  tool_calls: ToolCall[];
  latency_ms: number | null;
  cost_usd: number | null;
  metric_results: Record<string, MetricResult>;
}

export interface EvalCaseResult {
  case_id: string;
  input: string;
  variant_results: EvalVariantResult[];
}

export interface EvalVariantSummary {
  variant_id: string;
  label: string;
  pass_count: number;
  pass_rate: number;
  avg_judge_score: number | null;
  // Blends the judge score with latency into one comparable 0-1 number,
  // averaged across every case (not just rubric'd ones like
  // avg_judge_score) — see eval.metrics.CompositeScoreMetric.
  avg_composite_score: number | null;
  avg_latency_ms: number | null;
  avg_cost_usd: number | null;
}

export interface EvalRunSummary {
  total: number;
  variants: EvalVariantSummary[];
}

export interface EvalRun {
  run_id: string;
  suite_id: string;
  variants: EvalVariant[];
  status: "pending" | "running" | "completed" | "failed";
  total_cases?: number;
  case_results: EvalCaseResult[];
  summary: EvalRunSummary | null;
  created_by: string;
  created_at: string;
  finished_at: string | null;
}

export interface PromptVersion {
  version_id: string;
  setting_key: string;
  content: string;
  actor_email: string;
  message: string;
  eval_run_id: string | null;
  created_at: string;
}

async function handle<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: fallback }));
    throw new Error(err.error || `${fallback}: ${res.status}`);
  }
  return res.json();
}

export async function createSuite(params: {
  subject_type: SubjectType;
  label: string;
  setting_key?: string | null;
  cases: Partial<EvalTestCase>[];
}): Promise<EvalSuite> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const data = await handle<{ suite: EvalSuite }>(res, "Failed to create suite");
  return data.suite;
}

export async function listSuites(params?: {
  subject_type?: SubjectType;
  setting_key?: string;
}): Promise<EvalSuite[]> {
  const qs = new URLSearchParams();
  if (params?.subject_type) qs.set("subject_type", params.subject_type);
  if (params?.setting_key) qs.set("setting_key", params.setting_key);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites${suffix}`);
  const data = await handle<{ suites: EvalSuite[] }>(res, "Failed to list suites");
  return data.suites;
}

export async function getSuite(
  suiteId: string,
): Promise<{ suite: EvalSuite; had_prior_eval: boolean | null }> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites/${suiteId}`);
  return handle(res, "Failed to load suite");
}

export async function updateSuiteCases(
  suiteId: string,
  cases: Partial<EvalTestCase>[],
): Promise<EvalSuite> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites/${suiteId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cases }),
  });
  const data = await handle<{ suite: EvalSuite }>(res, "Failed to save test cases");
  return data.suite;
}

// Bulk-adds test cases from a CSV file (input, expected_contains,
// expected_not_contains, rubric columns) — appends to whatever the suite
// already has, no dedup. All-or-nothing server-side: a single bad row
// rejects the whole file with every row's problem listed, not a silent
// partial import.
export async function uploadSuiteCasesCsv(
  suiteId: string,
  file: File,
): Promise<{ suite: EvalSuite; added: number }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites/${suiteId}/cases/upload`, {
    method: "POST",
    body: formData,
  });
  return handle(res, "CSV upload failed");
}

// Loma subject: {draft_text, model?, agent_profile?} — builds exactly 2
// variants server-side (current live vs. draft). Generic subject:
// {variants: [...]} — true N-way, >= 2 entries.
export type RunSuiteParams =
  | { draft_text: string; model?: string; agent_profile?: AgentProfile }
  | { variants: Array<{ label?: string; prompt_text: string; model?: string; agent_profile?: AgentProfile }> };

export async function runSuite(suiteId: string, params: RunSuiteParams): Promise<EvalRun> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites/${suiteId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const data = await handle<{ run: EvalRun }>(res, "Eval run failed");
  return data.run;
}

export async function getRun(runId: string): Promise<EvalRun> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/runs/${runId}`);
  const data = await handle<{ run: EvalRun }>(res, "Failed to load run");
  return data.run;
}

export async function getLatestRunForSuite(suiteId: string): Promise<EvalRun | null> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/suites/${suiteId}/latest-run`);
  const data = await handle<{ run: EvalRun | null }>(res, "Failed to load latest run");
  return data.run;
}

export async function promoteDraft(params: {
  setting_key: string;
  content: string;
  run_id?: string;
}): Promise<{ had_prior_eval: boolean }> {
  const res = await fetch(`${API_BASE}/api/prompt-eval/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return handle(res, "Promote failed");
}

export async function fetchPromptHistory(settingKey: string): Promise<PromptVersion[]> {
  const res = await fetch(`${API_BASE}/api/prompt-settings/${settingKey}/history`);
  const data = await handle<{ history: PromptVersion[] }>(res, "Failed to load history");
  return data.history;
}

export async function fetchPromptDiff(
  settingKey: string,
  fromVersion: string,
  toVersion: string = "HEAD",
): Promise<string> {
  const qs = new URLSearchParams({ from: fromVersion, to: toVersion });
  const res = await fetch(`${API_BASE}/api/prompt-settings/${settingKey}/diff?${qs.toString()}`);
  const data = await handle<{ diff: string }>(res, "Failed to load diff");
  return data.diff;
}
