# Implement the Asset Library

A recipe for an implementing agent. Paste the block under **Invoke**, then follow the
**Steps**. Spec, tickets, and glossary are the source of truth — reach them; do not
restate them here.

---

## Invoke

Paste this into a **fresh** context window. One ticket per window.

```
Implement ticket NN of the Asset Library.

Read trial/asset-library/implementation.md and follow it in full.
Then read trial/asset-library/CONTEXT.md (glossary) and the ticket at
trial/asset-library/tickets/NN-*.md. Reach DESIGN.md only for the sections the
ticket names.

Do not commit. Stage only files this ticket produced. Leave every other working
tree change untouched.

When the ticket's acceptance criteria are all true, stop and report: files
changed, tests run, what a /code-review against this ticket would pin as the
fixed point.
```

Replace `NN` with `01` … `05`. Start at **01**. Sequence: `01 → 05 → 02`, then `03`
and `04` in either order (both blocked only by `02`).

---

## Steps

### 1. Orient

Read this file, then `CONTEXT.md`, then **only the named ticket**. Open `DESIGN.md`
for the sections that ticket lists under "Read first". Open `knowledge.md` when the
ticket names a live code path (registration, serve, chips). Open
`dashboard/DESIGN.md` when the ticket touches UI.

Done when: the ticket's "What to build" can be restated in one sentence, and every
acceptance checkbox maps to a seam named in **Seams**.

### 2. Pin the review base

Record `git rev-parse HEAD` as `<fixed-point>` for this ticket. Later review diffs
this ticket against that pin (`git diff <fixed-point>` for uncommitted work, or
`git diff <fixed-point>...HEAD` if commits exist).

Done when: the SHA is written in the working notes for this window.

### 3. Red → green at the ticket's seams

Use `/tdd`. One test → one implementation → repeat. Tests assert **external
behaviour** at the seams below, matching the ticket's testing plan. Mirror
`tests/test_phase4_file_delivery.py` and `tests/test_security_containment.py`.

Run the new test file after every green. Typecheck touched packages as you go.

Done when: every acceptance criterion on the ticket is true, and the tests named
in that ticket's testing plan exist and pass.

### 4. Two-axis self-check

Run `/code-review` against `<fixed-point>` from step 2.

- **Spec source:** this ticket + `trial/asset-library/DESIGN.md`.
- **Standards sources:** `CONTRIBUTING.md`; for UI tickets, also
  `dashboard/DESIGN.md`. Plus the smell baseline the code-review skill carries.

Fix anything the Spec axis marks missing, partial, or wrong, and any hard Standards
violation. Judgement-call smells: fix Shotgun Surgery (logic belonging in the new
modules leaked into existing functions) and Speculative Generality (abstractions
the ticket did not ask for). Leave the rest.

Done when: Spec has no missing/wrong items; Standards has no hard violations.

### 5. Hand off

Stage **only** the files this ticket added or needed. Do not commit. Do not touch
unrelated dirty files. Summarise: ticket id, SHA pinned, files staged, tests run
(command + result), leftover risks.

Done when: `git status` shows this ticket's files staged, everything else
unmodified-by-you, and the summary is in the chat.

---

## Seams

These are pre-agreed. Tests live here. No other seams without updating the ticket.

| Seam | Observed behaviour | Tickets |
|------|--------------------|---------|
| Asset-store module | `record_asset` / `list_assets` / `resolve_asset_availability` | 01, 04 |
| Registration function | one-line tap; return value and serving unchanged; fail-soft on durable write | 01 |
| `GET /api/assets` | owner-only, newest-first, 401 if unauthenticated; query-shaped for later search | 02 |
| `GET /api/files/{id}` | existing owner check; Library never replaces this path | 03, 04 |
| `/library` page | list, empty state, preview/download, resume + view-conversation links | 02, 03, 04 |
| Fixture script | through-seam populate; idempotent; one unavailable Asset | 05 |

---

## Frontier

```
01 ──▶ 02 ──▶ 03
   │      └──▶ 04
   └──▶ 05
```

- **01** — Asset store + recording seam. Backbone. No UI.
- **05** — Fixture through the real seam. Run as soon as 01 is green so 02–04 have data.
- **02** — Owner-filtered list endpoint + `/library` page. First demoable slice.
- **03** — Preview, download, return to source.
- **04** — Unavailable status + reason from the actual failing condition.

---

## Guardrails

Positive targets. Each maps to a DESIGN decision.

- **New modules, one-line taps.** Backend: Asset-store module + asset HTTP routes.
  Frontend: assets client + `/library` page + small presentational pieces. Existing
  functions gain a single call (registration) or a single nav entry (sidebar).
- **Owner-only, backend-enforced.** List filters by authenticated identity. Bytes go
  through the existing serve path. Generator is the only reader.
- **Durable pointer, ephemeral bytes.** Record metadata at registration. Availability
  is computed at read time from the same sources serve trusts.
- **Fail-soft recording.** A durable-write failure is logged; serving still succeeds.
- **Search-ready list.** Endpoint and client accept a query object. v1 sends none;
  no search UI.
- **Provider-agnostic seam.** The typed descriptor `(path, owner_email, conversation_id, source)`
  is how a future provider joins. OpenCode is the implemented producer.

Deliberate non-goals (logged in DESIGN Further Notes; a later `NOTES.md` restates
them). Treat building any of these as Spec-axis scope creep:

- Image preview inside the reused viewer
- Sharing, uploads, folders, new blob store, remote workers
- Historical migration, code-artifact indexing, search UI
- Formal `FileOutputAdapter` framework
- Enabling `LOMA_REMOTE_WORKERS`
- `NOTES.md` (written after all five tickets)

---

## Review bar

The implementer is graded the same way the trial is: **depth on a coherent slice**,
and every line of the diff explainable.

`/code-review` reports two axes and **must not merge them**.

| Axis | Question | Spec / standard |
|------|----------|-----------------|
| Spec | Does this ticket + DESIGN get what they asked, only that, correctly? | Ticket acceptance criteria; DESIGN Implementation / Testing / Out of Scope |
| Standards | Does the diff match this repo? | `CONTRIBUTING.md`; UI → `dashboard/DESIGN.md`; smell baseline |

Smell baseline (judgement calls; repo docs win): Mysterious Name, Duplicated Code,
Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun Surgery,
Divergent Change, Speculative Generality, Message Chains, Middle Man, Refused Bequest.

Pointers for the review sub-agents:

- Spec — missing/partial requirement; unasked behaviour; implemented-but-wrong.
  Quote the ticket or DESIGN line.
- Standards — cite `CONTRIBUTING.md` / `dashboard/DESIGN.md` for hard violations;
  name the smell and quote the hunk for judgement calls. Skip anything tooling
  already enforces.
