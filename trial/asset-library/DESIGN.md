# Asset Library — DESIGN

Status: pre-implementation spec. Committed before feature code, per the trial process.
Vocabulary follows `CONTEXT.md` in this directory.

> Scope note (deliberate): the local stack runs a single provider (OpenCode). This spec
> implements the OpenCode path end-to-end and models everything so a second provider
> (Claude, workers) plugs in without touching the library. That tradeoff, and the
> ephemeral-registry tradeoff below, are logged in `NOTES.md` at the end.

---

## Problem Statement

Agents produce files while working — reports, spreadsheets, images. Today those outputs
live inside conversations. Finding one again means remembering which conversation
produced it and scrolling its messages. There is no place that answers "show me the files
I've generated," and no way to open one and jump back to where it came from.

Two facts make this sharper than a list view:

1. **The outputs are not durably recorded.** On the local (OpenCode) path, a produced
   file surfaces as a live chip and is served from a process-local registry. It is never
   written to the durable store, and reopening the conversation does not bring it back.
   Without a durable record, a library has nothing to list after a refresh or restart.
2. **Access must be enforced, not implied.** Appearing in a list must not make a file
   openable. The owner check that guards serving today must guard the library too.

## Solution

A first-class **Library** surface in the dashboard where a user sees the file-backed
outputs **they** generated, opens or downloads them, and returns to the source
conversation. Underneath, an **Asset** is recorded durably at the moment a file is
registered — the one point every provider funnels through — so the library survives
restarts and is provider-agnostic by construction. When an Asset's bytes are gone, the
library still lists it and states, from the actual failing condition, why it cannot be
opened.

## User Stories

1. As a user, I want a Library in the dashboard nav, so that I can reach my generated
   files without opening a conversation.
2. As a user, I want the Library to list the file outputs I have generated, so that I can
   find a file by browsing instead of remembering a conversation.
3. As a user, I want each item to show its name, type, size, and when it was created, so
   that I can recognise the file I want.
4. As a user, I want the newest outputs first, so that the file I most likely want is at
   the top.
5. As a user, I want to open a file to preview it in place, so that I can confirm it is
   the right one before downloading.
6. As a user, I want to download a file from the Library, so that I can save it locally.
7. As a user, I want to jump from a file to the conversation that produced it and resume
   it, so that I can continue the work that created it.
8. As a user, I want a secondary way to view that conversation read-only, so that I can
   inspect its history without resuming it.
9. As a user, I want to see only my own outputs, so that my files stay private.
10. As a user, I must not be able to open another user's file even if I learn its
    identifier, so that access is genuinely enforced on the backend.
11. As a user, I want a file whose bytes are no longer available to still appear, marked
    unavailable, so that I understand it existed rather than silently losing it.
12. As a user, I want an unavailable file to tell me why it cannot be opened, so that I
    know whether it is recoverable.
13. As a user generating two files in one turn, I want both to appear as separate items,
    so that each is independently openable.
14. As a reviewer, I want a fixture that populates the Library through the real code path,
    so that I can reproduce the demo without a live model.
15. As a reviewer, I want an unavailable item present in the fixture, so that I can see the
    degraded state without waiting for a restart.
16. As a maintainer, I want a new provider's files to appear in the Library automatically,
    so that adding a provider never requires touching library code.
17. As a maintainer, I want the list endpoint and client shaped so that search can be
    added later without reworking them, so that the minimal v1 does not paint us into a
    corner.
18. As a maintainer, I want the additions to be isolated, testable modules rather than
    edits buried inside existing functions, so that each piece can be tested and reasoned
    about on its own.

## Implementation Decisions

### Data model — a dedicated `assets` collection
An Asset is a purpose-built record, not a reuse of the existing conversation-output
records (which carry no owner, are unindexed, and are empty on the local path). Fields,
kept minimal with ownership self-contained:

| Field | Meaning |
|-------|---------|
| `file_id` | Identity of the file-backed output (unique). |
| `owner_email` | The generating user; the only permitted reader. |
| `conversation_id` | Source conversation, for return-to-source. |
| `name` | Display / download name. |
| `mime_type` | For preview routing. |
| `size_bytes` | For display. |
| `created_at` | For newest-first ordering. |
| `source` | Producing provider (`opencode` \| `claude` \| `worker`). |

Indexes: `(owner_email, created_at desc)` for the owner-scoped, ordered list; unique
`file_id`. **Availability and its reason are never stored** — they are a function of live
process state and are computed at read time.

- *Why:* one indexed lookup answers "this user's files, newest first," with the owner on
  the row so no join is needed for the access decision.
- *Tradeoff:* a small denormalisation (owner duplicated from the conversation) in exchange
  for a self-contained access check. A turn-level reference for pixel-precise deep-linking
  is deferred; `conversation_id` is enough to return to source.

### Recording seam — one point, provider-agnostic
Every provider that produces a file-backed output funnels through a single registration
function. An Asset is recorded there, via a **one-line call** into a dedicated Asset-store
module — the registration function is not rewritten, only tapped. The caller supplies a
typed descriptor `(path, owner_email, conversation_id, source)`.

- *Why:* the highest single seam in the codebase. One tap captures OpenCode today and any
  future provider with no library change — the plug-and-play property.
- *Tradeoff:* the registration function is synchronous while the durable write is async;
  the Asset-store module owns that boundary so the seam stays a single line.

### Modularity — structural contract (chosen), formal framework (deferred)
Chosen: the central seam plus a typed descriptor and a documented contract ("to add a
provider, build the descriptor and call the seam"). Deferred: a formal provider interface
with per-provider detection modules. The chosen approach already delivers plug-and-play;
the formal framework is a broader refactor of the detection layer, out of proportion to
this trial. Both, with pros and cons, are logged in `NOTES.md`.

### Access — a new owner-scoped endpoint; serving reused
A new list endpoint returns only the caller's Assets, filtered server-side by the
authenticated user. Opening and downloading reuse the existing, already-owner-checked
file-serving path unchanged. Enforcement lives in two backend places, neither trusting the
UI. There is no sharing: the generator is the only reader.

### Availability — one resolver, real reasons
A single availability resolver probes the same sources the serving path trusts (the
process registry, then the file on disk, then the durable worker record) and returns
`(status, reason)`. The reason is read off the actual failing condition — registry cleared
on restart, bytes missing at their path, worker record expired — never a hardcoded string.
The list endpoint uses this resolver per item.

### UX — split-pane Library, reused viewer
A `Library` surface modelled on the existing split-pane list/detail pattern: an
owner-scoped, newest-first list on one side; a preview/detail pane on the other reusing the
existing artifact viewer for preview and download. Primary action on an item **resumes the
source conversation**; a secondary action opens it read-only. Nav entry visible to all
authenticated users (personal outputs, every role has them).

### Search-ready minimalism
v1 ships name, type, size, created-at, and a source-conversation link, newest-first — no
search box. The list endpoint and client accept the shape a future query filter will use,
so search is an addition, not a rewrite.

### Fixture
A re-runnable script drives the **real** registration seam to create a source conversation
and a few Assets (pdf, png, csv), plus one deliberately **unavailable** Asset (registered,
then its bytes removed) to demonstrate the degraded state. It carries the documented sample
prompt that reproduces a real dual-file turn.

## Testing Decisions

Good tests here assert **external behaviour at the highest seam** — the HTTP boundary and
the recording seam — not internal shapes. They reuse the existing containment and
file-delivery test patterns. Every addition is a separately testable module so each is
covered in isolation.

- **Recording seam:** registering a file writes exactly one owner-scoped Asset row with the
  right provenance; two files in one turn write two rows.
- **List endpoint:** returns only the caller's Assets; empty for a user with none; ordered
  newest-first.
- **Access isolation:** a second user cannot list or open the first user's file; opening a
  known-but-foreign identifier fails closed.
- **Availability:** an Asset whose bytes are removed surfaces `unavailable` with a reason
  derived from the actual condition; an intact Asset opens.
- **Fixture:** idempotent; populates the expected set including one unavailable item.
- **Frontend:** a light render/smoke check only; depth goes to the backend access edge.

## Out of Scope

Sharing and permissions beyond owner-only; uploads into the library; folders or hierarchy;
a new storage service or durable byte store; remote workers; migration of historical
outputs never recorded; indexing code artifacts or inline outputs; search/filter in v1;
in-library image preview (see Further Notes); pixel-precise deep-link to the exact message.

## Further Notes

- **Ephemerality is accepted, not solved.** The library records a durable *pointer*, not
  durable *bytes*. After a process restart an Asset can be listed yet unopenable; it says
  so, with the real reason. Replacing the byte infrastructure is explicitly not asked for.
- **Image preview is a known, unbuilt gap.** The reused viewer previews documents but sends
  images to download. In-library image preview is sensible but expands scope; logged as a
  deliberate non-goal for v1.
- **Modularity growth path.** The deferred formal provider interface is the natural next
  step once a second provider is wired locally.
- **`NOTES.md`** (written at the end) will carry the retrospective the trial asks for: what
  was tried and abandoned, the weakest part and what breaks it first, and what was
  deliberately not built — including the modularity and image-preview tradeoffs above, and
  the sample prompt used to generate a dual-file turn.
