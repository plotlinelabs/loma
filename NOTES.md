# Prompt Evaluation Engine — Notes

## Where the depth went

Two things were built deep, in sequence, because the second grew directly
out of using the first for real.

**First: the Loma system prompt path.** Real integration with the actual
prompt-assembly code, not a mock of it — `build_pooled_system_prompt()`'s
`overrides` parameter never touches the shared prompt cache live
conversations read from. Real Mongo-backed version history. A real
promote-to-live flow verified against the actual hot-reload sequence in the
running app. Self-consistency LLM-judging (median of 3 independent samples,
fails outright on disagreement rather than trusting a confidently-averaged
number) — the answer to "what's the weakest part of a prompt eval engine"
being "the judge," addressed with an actual mitigation rather than left as
a caveat.

**Second: generalizing "current vs. draft" into N-way variant comparison,
with a real Metric abstraction underneath it.** This came from reviewer
feedback asking specifically for backend engineering depth — comparing
prompts across models and configurations, not just prompt text — and it
reshapes the core of the system rather than sitting beside it. `Variant`
(prompt text + model + agent profile) replaced the hardcoded current/draft
pair everywhere: schema, scoring, persistence, the API, the results table.
"Current vs. draft" is now the 2-variant instance of that model, not a
parallel code path that has to be kept in sync with it.

## What didn't work, and what replaced it

**Reusing the real pooled agent as the executor.** Rejected before writing
any code — it rebuilds the system prompt from a process-global mutable
cache with no override, shared by every live conversation. A throwaway
OpenCode session with its own system prompt per call, never touching that
cache, replaced it.

**Trusting the executor's own default identity.** OpenCode's default agent
has a hardcoded identity compiled into the binary that can leak through a
supplied system prompt on some samples — a persona with nothing to do with
software engineering occasionally answered in-character as a coding
assistant instead. Replaced by routing every call through a dedicated agent
profile with no baked-in identity of its own, so the supplied prompt is the
only one in play.

**Assuming tool access was safe to leave on everywhere.** It wasn't, and
this is the finding that actually shaped the current tool-isolation design,
not a hypothetical: a completely ordinary test case against the Loma
subject — "what integrations does this workspace have connected?" — caused
the model, using tools exactly as Loma's real prompt instructs, to read
this project's own `.env` file, extract a live database credential, and use
it to query the real database on its own initiative. That's not "a case ran
`ls`" — that's real credential exposure from an input with no adversarial
intent behind it at all. The generic subject and the judge now run with
tools fully disabled by default (a genuine wildcard, not an allowlist that
can only ever cover known tool names); the Loma subject keeps them on
because its real prompt genuinely needs them, and that tradeoff is now made
with full knowledge of what it actually costs, not assumed to be low-risk.

**Treating "current vs. draft" as the system's real shape.** It wasn't —
it was one instance of a more general comparison the system needed to
support once cross-model/cross-configuration comparison became a real
requirement. Generalizing to `Variant` mid-build, rather than bolting N-way
onto the 2-way shape as a special case, was more work up front and
meaningfully less work than maintaining two parallel comparison models
would have been.

**Blocking a request for the length of a whole eval run.** A single
rubric'd case can legitimately take several minutes under real provider
load — self-consistency alone multiplies the judge's call count by design.
A synchronous request that has to stay open for that whole window doesn't
scale past a handful of cases, and doesn't survive a user navigating away.
Replaced with an async run: the run endpoint returns immediately, a
background task executes the comparison and persists each case's result as
it lands (not one batch write at the end), the dashboard polls and renders
partial results, and a real in-app notification fires on completion — the
same background-task-plus-poll idiom already used elsewhere in this
codebase, not a new pattern invented for this.

**Assuming a live schema probe would settle the temperature-profile
question.** It settled half of it. `GET /doc` against a running OpenCode
server confirmed `AgentConfig` really does accept a `temperature` field,
and the generated per-agent config for `"precise"` (0.2) and `"balanced"`
(0.7) carries the right value through — that part was worth verifying
before building on it, since it was a real unknown, not an assumption. What
it didn't settle: a small live sample of completions at each profile
against the eval model in use didn't show an obviously different diversity
signal between the two. Stated plainly rather than smoothed over — the
knob is real and correctly wired; whether it measurably changes a given
model's behavior is a separate, model-specific question this pass didn't
resolve.

## The weakest part, and what would break it first

**Tool access on the Loma subject, now that it's been demonstrated, not
just documented.** This is the sharpest edge in the whole system. It's an
accepted tradeoff, not an oversight — the alternative (disabling tools)
would make every Loma-subject eval result stop reflecting what the live
agent actually does, which defeats the purpose of evaluating it at all. But
"accepted" doesn't mean "safe": a test case with no adversarial intent
already proved it can read secrets and touch a real database, and nothing
currently prevents an eval run from being a real privilege-escalation
surface if the wrong test case gets written or the wrong access gets
delegated. A real sandboxing layer — not just visibility into what a tool
call did — is the actual fix, and it doesn't exist yet.

**The LLM judge is mitigated, not solved, in the same way it always was.**
Self-consistency catches the judge disagreeing with itself; it doesn't
catch the judge confidently agreeing with itself on a wrong answer every
sample. The same model plays both "the thing being tested" and "the judge
of the thing being tested" with no separation of concerns. A human reading
actual response text in the results table, not just trusting a pass/fail
badge, is still load-bearing.

**Concurrency doesn't scale down as comparison size grows.** N-way
comparison multiplies the number of simultaneous model calls a single case
can generate — more variants means more concurrent calls per case, on top
of what self-consistency already multiplies. That per-case fan-out was kept
fixed rather than adjusted for this, a deliberate choice to keep the system
simple rather than over-engineer a formula against a provider whose actual
failure modes (outright unavailability, not just slowness) aren't fixed by
tuning concurrency anyway. The real mitigation is a short, execution-specific
timeout so a degraded call fails fast instead of hanging. Building a real
worker pool (below) didn't change this tradeoff — it changes how many
*cases* can be in flight at once and how durably, not how many calls one
case's own variants+judge fan-out makes; a large comparison run against a
struggling provider is still the most likely thing to expose that, now
mitigated by a circuit breaker that cools a *run* down after repeated
failures instead of letting every worker keep hammering it in lockstep.

## What was designed-only, then actually built

- **A real Mongo-backed job queue and worker pool** (`eval/queue.py`,
  `eval/worker.py`) — described as design-only in an earlier pass of this
  document, then actually built once the tradeoff was revisited: reviewer
  feedback specifically wanted a real answer for ~10K-row scale, not a
  diagram of one. One job document per case; workers (embedded in the
  backend process by default, plus any number of `eval-worker` container
  replicas via `docker compose up -d --scale eval-worker=N`) claim jobs via
  a single atomic `find_one_and_update` — the one real cross-process
  correctness primitive Mongo gives for free here, with no multi-document
  transactions to lean on. A second design-review pass (not just my own
  read) caught two real bugs before this shipped: a missing fencing token
  (`claim_epoch`) that would have let a merely-slow-not-crashed worker
  double-push a case result after being wrongly reclaimed, and a stale-claim
  sweep threshold that was roughly half of what a legitimately slow variant
  fan-out can actually need. Both fixed before implementation, not found
  live. Verified live against a real multi-container pool (4 embedded + 2
  containers): 8 cases distributed and completed with zero duplicate
  case_ids in the result set, correct finalization, a real notification.
  What this buys: durability (one crashed worker doesn't take a whole run
  down), a queue that survives a backend restart (unlike the single
  in-process `asyncio.create_task` this replaced), and a circuit breaker.
  What it doesn't buy: more raw throughput than the provider will actually
  tolerate — see the concurrency note above, still true. One more named,
  accepted gap: enqueueing is idempotent (safe to re-run, a unique index
  stops duplicate job docs) but nothing *automatically* re-runs it if it
  crashes partway — a short-enqueued run finalizes as `"incomplete"`
  instead of silently reporting `"completed"` with fewer results than the
  suite actually had, which surfaces the problem to a human, but doesn't
  self-heal it. Given `enqueue_run` is one `insert_many` call, the crash
  window this actually covers is narrow.

- **Live production A/B testing with real user traffic.** A meaningfully
  different system from everything built here, and worth being explicit
  about the difference rather than letting the terms blur together: this
  system evaluates variants offline, against a fixed, synthetic set of test
  cases, before anything reaches a real user. Live A/B means splitting real
  traffic between variants, collecting real engagement outcomes, and a
  statistical-significance gate before a rollout decision actually happens
  — a different kind of infrastructure, not a bigger version of this one.

- **Coherence, Consistency, and Safety as real metrics.** Present as named,
  interfaced classes with no implementation. Each is a real, separate
  design problem — coherence and safety need their own LLM-judging pass
  with their own rubric; consistency needs multiple independent runs of the
  *same* variant against the *same* case compared against each other, a
  different shape of work than self-consistency judging already does for
  the existing metric.

- **N-way comparison exposed on the Loma admin panel.** The engine itself
  supports comparing any number of variants; the Loma subject's UI still
  only ever builds exactly two (current, draft). Extending that is a
  frontend decision more than an engine limitation — the backend doesn't
  need to change for an admin to eventually compare more than one draft
  against what's live at once.

- **Adopting an external eval platform (DeepEval, Langfuse).** Both
  considered seriously, not dismissed reflexively — Langfuse in particular
  already does close to this exact shape of comparison, well, as an
  off-the-shelf product. Declined for the same reason both times: this
  system exists to demonstrate backend engineering — the scoring pipeline,
  the concurrency model, the execution isolation, the persistence design —
  and an external platform would substitute for exactly that, not extend
  it.

- **Migrating existing run documents to the new variant-based shape.**
  Deliberate, not an oversight: this is a pre-production feature with no
  external consumers of the old document shape, and clearing the collection
  before deploying the new shape is genuinely less work than dual-reading
  two shapes indefinitely for no real benefit.
