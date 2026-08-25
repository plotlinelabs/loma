# Prompt Evaluation Engine — Design

## The problem

Loma's system prompt is assembled from a few Mongo-backed fields
(`identity_guidelines`, `company_information`, `dictation_vocabulary`) edited
under **Admin → Settings**. A save writes straight to `db.prompt_settings` and
hot-reloads the live agent pool immediately, for everyone. There is no draft,
no history, and no way to see what a change actually does to the agent's
behavior before it ships — and no way to know whether one model/prompt
configuration actually outperforms another before committing to it.

## What this system is

A backend service for comparing **N prompt variants** — each its own prompt
text, model, and sampling profile — against a shared set of test cases,
scored by a pluggable set of metrics, with the results persisted and
promotable to production. It treats Loma's own system prompt as one
evaluable subject among others, not the only one a general-purpose
comparison engine happens to be wired to.

## The core abstraction: Variant

```
eval/schema.py:
  Variant       = {variant_id, label, prompt_text, model, agent_profile}
  TestCase      = {case_id, input, expected_contains, expected_not_contains, rubric}

eval/runner.py:
  run_eval(variants: list[Variant], cases: list[TestCase], disable_tools, metrics) -> RunResult
```

A run compares an arbitrary list of variants against every case — "current
vs. draft" is the 2-variant instance of this model, not a separate code
path. `run_eval()` has no idea whether a variant's prompt text came from
Loma's Mongo settings or was typed by hand, and no idea whether two variants
share a model or not. Two adapters produce variants for the two subjects:

- **Loma subject** — the route layer builds exactly 2 variants:
  `current` from `build_pooled_system_prompt()` (unmodified, reads the live
  cache) and `draft` from the same assembly with one `RULEBOOK_KEYS` section
  swapped (`build_pooled_system_prompt(overrides=...)`). Only
  `identity_guidelines`/`company_information` are eval-able — the third
  Settings-screen field, `dictation_vocabulary`, never enters the system
  prompt, so evaluating it would silently produce two identical prompts.
  Not yet exposed to N-way on this subject's admin panel; that stays a
  2-variant comparison for now, by design (see below).
- **Generic subject** ("Prompt Lab") — the user defines any number of
  variants directly: prompt text, model, and profile per row. No settings
  key, no persistence beyond the run itself, no promote action. This is
  what actually exercises N-way comparison, and the shared component file
  between the two subjects (`prompt-eval/shared.tsx`) is the proof the
  underlying engine doesn't care which subject produced its input.

## Scoring: a pluggable Metric pipeline

```
eval/metrics.py:
  Metric.evaluate(case, MetricInputs) -> MetricResult
  MetricResult = {metric, passed: bool | None, score, detail, failures}
```

Every check a variant's response goes through is a `Metric` — not a
hardcoded pair of if-branches. `passed=None` means informational (it never
gates a variant's pass/fail on its own); `passed=True/False` means it does.

- **`SubstringMetric`** — deterministic `expected_contains`/
  `expected_not_contains` matching.
- **`LLMJudgeMetric`** — LLM-as-judge rubric grading with self-consistency:
  the judge is sampled 3 times independently (not trusted on one call), and
  the *median* of the samples is the score — median, not mean, so one
  erratic sample can't drag an otherwise-agreeing set around. If the samples
  disagree with each other past a threshold, the case fails outright even
  if the median alone would have passed — a confidently-averaged number
  hiding real disagreement is worse than surfacing that the judge couldn't
  agree with itself. The judge always grades under a fixed "default"
  profile and never touches tools, regardless of which variant or
  temperature produced the response being graded — it has to stay a stable
  measuring instrument, or a score difference between variants could mean
  "the judge sampled differently" instead of "the response was actually
  different."
- **`LatencyMetric` / `CostMetric`** — informational only (no budget exists
  yet to gate against), sourced from real wall-clock timing and whatever
  token/cost data the model provider actually returns.
- **`CoherenceMetric` / `ConsistencyMetric` / `SafetyMetric`** — named,
  intentionally unbuilt. Each would need its own LLM call (coherence,
  safety) or multiple independent runs of the same variant to compare
  against each other (consistency) — real, separate pieces of work, not a
  natural extension of the current synchronous, single-call-per-metric
  shape. See "What's not built" below.

`eval/decision.py` runs the configured metric list per variant per case,
accumulates failures, and aggregates pass rate / average judge score /
average latency / average cost per variant across a whole run. No I/O
anywhere in this module — every metric receives its inputs pre-gathered.

## Executor

Every model call goes through one function, `run_opencode_oneshot()`, a
throwaway single-turn OpenCode session — never the pooled, tool-using agent
that live Slack/dashboard conversations share, and never touching the
process-global mutable prompt cache those conversations read from. Two
properties of that executor are load-bearing enough to call out explicitly:

**Tool access is a per-subject decision, not a default.** A variant's
response call can run with tools fully enabled or fully disabled
(`{"tools": {"*": false}}`, a real wildcard OpenCode's API supports — not an
enumerated allowlist, which can never be complete against runtime-provided
tools). The Loma subject leaves tools **on**, deliberately: its real system
prompt instructs the model to prefer connected tools, and disabling them
would make the eval stop reflecting what the live agent actually does. That
is a real, accepted risk, not an oversight — verified directly, not just
assumed: a completely ordinary test case ("what integrations does this
workspace have connected?") caused the model to autonomously read this
project's own `.env` file, extract a live database credential, and use it
to query the real database, entirely on its own initiative. The generic
subject and the judge both run with tools **off** for exactly this reason —
a pasted persona or a grading call has no legitimate reason to touch a real
tool, ever.

**The executor's own identity is isolated from what it's asked to role-play.**
OpenCode's default agent has a hardcoded identity baked into the binary that
can otherwise leak through a supplied system prompt — a persona with
nothing to do with software engineering can occasionally answer in-character
as a coding CLI tool instead, correctly naming this actual project. Every
oneshot call routes through a dedicated, minimal agent profile
(`agent.opencode_runtime.AGENT_PROFILES`) with no baked-in prompt of its
own, so the supplied `prompt_text` is the only identity in play.

`AGENT_PROFILES` is also the mechanism for the one remaining axis of
"different model configuration" the reviewer asked for: temperature.
OpenCode has no per-request temperature parameter — it can only be set as a
static property of a pre-registered agent config — so varying temperature
means selecting between a small set of named profiles (`"default"`,
`"precise"` at 0.2, `"balanced"` at 0.7), not a free per-run slider.
Verified live against a running OpenCode server's own `GET /doc` schema
before building on top of it: `AgentConfig` has a real `temperature: number`
field, and the generated per-agent config for each profile carries the
right value through. A live sample of completions at each profile didn't
show a clean, obviously-different diversity signal between 0.2 and 0.7 on
the small model this was probed against — stated plainly rather than
assumed away; the knob is real and correctly wired, its practical effect on
a given model's output is a separate, unverified question.

## Persistence

- `prompt_eval_suites` — `{suite_id, subject_type, setting_key (loma only),
  cases: [...], created_by, created_at}`.
- `prompt_eval_runs` — `{run_id, suite_id, variants: [...], status,
  case_results: [...], summary, created_by, created_at, finished_at}`. Every
  variant's full definition (prompt text, model, profile) is stored on the
  run itself, not just referenced — a run is a standalone, complete record
  of the comparison it performed. No migration from the older
  current/draft-shaped documents; this is a pre-production feature, and a
  `deleteMany({})` before deploying a new run shape is real work saved
  versus dual-reading two document shapes for marginal benefit.
- `prompt_settings_versions` — written on every promote, one snapshot per
  write, giving "no history, no way to know what changed" an actual answer
  as a side effect of drafts existing at all.

**Promotion** reuses the exact same write path a plain Settings-tab save
uses (`write_prompt_setting()`) — one code path that ever writes a live
prompt, whether the write came from a human editing a textarea or a
human clicking Promote after a passing eval. The promotion gate itself is
soft: it checks whether *any* eval run has ever been recorded for the
setting being promoted and shows a non-blocking warning if not, rather than
hash-matching a specific draft to a specific run. A nudge, not an approval
workflow nobody asked for.

## Execution model: what's built, what's designed

**Built**: an asynchronous run. `POST /suites/{id}/run` inserts a run
document (`status: "pending"`) and returns it immediately (202); the run is
enqueued onto a real Mongo-backed job queue (`eval/queue.py`) — one job
document per case — and a real worker pool (`eval/worker.py`) claims and
executes jobs, `$push`-ing each case's result onto the run document as it
completes, not one batch write at the end. The dashboard polls the run doc
every 2s and stops on a terminal status, rendering partial results as they
land rather than waiting for the whole run. On completion the user gets a
real in-app notification via `observability.notifications.create_notification()`
— the same mechanism Loma's own agent already uses to tell a user a long
task is done, not new delivery infrastructure.

**The worker pool is real, not an in-process simulation of one.**
`EMBEDDED_WORKER_COUNT` (default 4) worker-loop coroutines start inside the
backend process itself at startup — a plain `docker compose up` with no
extra containers still processes eval runs out of the box, replacing the
role `MAX_CONCURRENT_CASES` used to play. `docker compose up -d --scale
eval-worker=N` adds any number of separate container replicas on top for
real horizontal throughput on a large run, each spawning its own OpenCode
subprocess via the same self-spawn logic the backend already uses (not one
shared OpenCode instance across containers — simpler, and the real
bottleneck is the downstream provider either way, not local OpenCode
capacity). Claiming is a single atomic Mongo `find_one_and_update` — the
one real cross-process correctness guarantee available (no multi-document
transactions here) — and every job carries a `claim_epoch` fencing token:
a worker whose write loses that fence (because a stale-claim sweep decided
it looked dead and reclaimed the job) discards its result instead of
double-pushing it. A crashed worker's claimed job gets swept back to
pending after ~2×`EVAL_ONESHOT_TIMEOUT_SECONDS` (matching that this
codebase's own executor calls are sequential per variant — response, then
judge — so a legitimately slow-but-alive worker can need close to two full
timeouts). Finalization (aggregate + finish + notify) is decided by exactly
one worker via the same atomic-flip pattern, whichever of the embedded or
containerized workers happens to finish the last outstanding job for a run.
**This also fixes the prior inherited limitation**: a server restart mid-run
no longer orphans anything — the queue lives in Mongo, not in one process's
memory, so surviving workers (or the backend's own embedded workers coming
back up) simply keep draining it.

**Named, not solved**: the value here isn't "way more raw concurrency" —
NOTES.md already documents this deployment's provider failing by going
*unavailable*, not by returning clean `429`s, so no worker count fixes
that. The value is durability, a queue that survives a restart, and a
circuit breaker (`consecutive_failures` on the run doc; 5 consecutive job
failures cools a run down for 300s, reusing the exact cooldown window
already established elsewhere in this codebase for the same "this provider
is unhealthy" concept) so a struggling provider degrades a run gracefully
instead of every worker hammering it in lockstep.

Test cases can also be bulk-loaded via CSV (`POST
/suites/{id}/cases/upload`, multipart) instead of hand-typed one at a time
— `eval/csv_import.py`'s `parse_cases_csv()` is all-or-nothing: one bad row
lists every row's problem and rejects the whole file, never a silent
partial import.

## What's deliberately not built

- **Live production A/B testing.** Genuinely different from everything
  this system does: this is offline, pre-promotion evaluation against
  synthetic test cases that never touches a real user. Live A/B means
  splitting real traffic between variants, collecting real engagement
  metrics, and a statistical-significance gate before a rollout decision —
  a different system with different infrastructure, not an extension of
  this one.
- **Coherence, Consistency, and Safety metrics.** Named and interfaced, not
  implemented — each needs either its own LLM call or multiple independent
  runs to compare against each other, a real design problem each, not a
  natural extension of the current shape.
- **N-way comparison on the Loma admin panel.** The generic subject exposes
  real N-way; the Loma subject stays a 2-variant (current/draft) comparison
  for now — there's no UI yet for an admin to compare more than one draft
  at a time against what's live.
- **Adopting an external eval platform** (DeepEval, Langfuse). Both looked
  at seriously — Langfuse in particular runs almost exactly this shape of
  comparison already, well — and both declined for the same reason: an
  external platform would substitute for the backend engineering this
  system exists to demonstrate, not extend it.
