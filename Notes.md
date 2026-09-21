# Notes — Asset Library

What this change is, how it is built, what it costs, and how well it answers the trial in
`assessment.md`. Written after the code, as the trial asks. Vocabulary follows
`trial/asset-library/CONTEXT.md`.

The trial brief is in `assessment.md`. The pre-implementation spec is in
`trial/asset-library/DESIGN.md`. This file is the retrospective.

---

## High-level design

The feature adds one surface and one record, and reuses everything else.

- **The surface** is a `/library` page in the dashboard. It lists the file-backed outputs
  the signed-in user generated, newest first, and shows a preview or download pane beside
  the list. From an item you resume the source conversation or open it read-only.
- **The record** is an `Asset`: a durable, owner-scoped pointer to one file-backed output.
  It is written the moment a file is registered for serving, at the one function every
  provider already funnels through.

The design rests on three decisions.

1. **Record a pointer, not bytes.** An `Asset` stores identity, owner, source
   conversation, and display metadata. The bytes stay on the existing serve path. The
   library survives a restart; the bytes may not, and the list says so.
2. **Tap the highest seam.** `register_served_file` in `api/routes.py` is the single point
   every produced file passes through. One call there records an `Asset` for OpenCode
   today and for any future provider with no library change.
3. **Enforce access twice on the backend, trust the UI nowhere.** The list endpoint
   filters by the authenticated caller. Opening and downloading reuse the existing,
   already-owner-checked `GET /api/files/{id}` path, unchanged.

```
produce file ──▶ register_served_file()  ── one-line tap ──▶ record_asset()
                        │                                          │
                   serve path (bytes)                      assets collection (pointer)
                        │                                          │
   GET /api/files/{id}  ◀── open / download ──  /library ──▶  GET /api/assets
        owner check                              (owner-filtered list)
```

### What each moving part does

| Part | File | Job |
|------|------|-----|
| Asset store | `observability/assets.py` | Record, list, and probe availability of `Asset` rows. |
| Recording tap | `api/routes.py` (`register_served_file`) | One call that records an `Asset` on every registration. |
| Provenance binding | `api/routes.py` (chat handler) | Binds source conversation and provider for the turn. |
| List endpoint | `api/asset_routes.py` (`GET /api/assets`) | Returns the caller's Assets, newest first. |
| Library page | `dashboard/src/app/library/*` | Split-pane list and detail. |
| Assets client | `dashboard/src/lib/assets-api.ts` | Typed fetch, shaped for a later search filter. |
| Fixture | `scripts/asset_library_fixture.py` | Populates sample Assets through the real seam. |

---

## Low-level design

### The recording seam

`register_served_file` gains two lines. It reads the turn's provenance from a
`contextvars` binding and records the `Asset`:

```1151:1154:api/routes.py
    keepalive_task = asyncio.create_task(_send_keepalive())
    recording_token = bind_asset_recording(
        observer.conversation_id if observer else None,
    )
```

```84:85:api/routes.py
    conversation_id, source = current_recording()
    record_asset(AssetDescriptor(file_id, owner_email, conversation_id, source))
```

The `contextvars` binding carries the source conversation without threading a parameter
through every caller of `register_served_file`. The chat handler sets it for the turn and
resets it in a `finally` block.

`record_asset` is fail-soft. It schedules the durable write as a background task and
swallows every error, so a Mongo outage never breaks serving:

```92:99:observability/assets.py
def record_asset(descriptor: AssetDescriptor) -> None:
    """Fail-soft durable write. Safe to call from synchronous registration."""
    try:
        meta = _served_meta(descriptor.file_id)
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_asset(descriptor, meta))
    except Exception:
        logger.exception("Asset recording failed for file_id=%s", descriptor.file_id)
```

### The data model

One `assets` collection. Ownership sits on the row, so the access check needs no join.

| Field | Meaning |
|-------|---------|
| `file_id` | Identity of the file-backed output (unique). |
| `owner_email` | The generating user; the only permitted reader. |
| `conversation_id` | Source conversation, for return-to-source. |
| `name`, `mime_type`, `size_bytes` | Display and preview routing. |
| `created_at` | Newest-first ordering. |
| `source` | Producing provider (`opencode`, `claude`, `worker`). |

Indexes: unique `file_id`, and `(owner_email, created_at desc)` for the owner-scoped,
ordered list. Availability is never stored. It is a function of live process state,
computed at read time.

### The list path

`GET /api/assets` filters by the authenticated caller, drops restart leftovers, then
attaches a live availability verdict per item:

```52:59:api/asset_routes.py
    rows = await list_assets(user_email)
    assets = []
    for row in rows:
        file_id = row.get("file_id") or ""
        if not has_live_serve_source(file_id):
            continue
        item = _serialize_asset(row)
        status, reason = await resolve_asset_availability(file_id)
```

### The availability resolver

One resolver probes the same sources the serve path trusts and returns
`(status, reason)`. The reason is read off the actual failing condition — registry
cleared, bytes missing at their path, worker record expired — never a hardcoded string.
Local files and worker records take separate branches, because their durability differs.

### The frontend

The library reuses the existing split-pane list/detail shape and the existing
`ArtifactViewer`. `LibraryDetail` builds the viewer's `artifact` prop from an `Asset` and
points its `file_url` at `GET /api/files/{id}`, so open and download run the same
owner-checked path the rest of the app uses. `ArtifactViewer` gained a PDF.js canvas
renderer, because the old `<iframe>` PDF preview renders blank under the serve path's CSP
sandbox and Electron.

---

## Pros

- **One seam, every provider.** Recording at `register_served_file` means a new provider
  contributes Assets the day it is wired in, with no library edit. This is the strongest
  property of the design.
- **Access is enforced, not implied.** The list filters by identity; opening reuses the
  existing owner check. Appearing in the list does not make a file openable. The
  containment tests prove a second user cannot list or open the first user's file.
- **Honest degraded state.** An unavailable item states why from the real condition, so a
  reviewer sees the failure mode instead of a silent gap.
- **Small, testable modules.** The additions are new files with one-line taps, not logic
  buried inside existing functions. Every seam is covered at the HTTP or store boundary.
- **Search-ready without a search UI.** The endpoint and client accept a query object, so
  a filter is an addition, not a rewrite. v1 sends none.
- **Reuse over rebuild.** The viewer, the serve path, and the split-pane pattern are
  reused, which keeps the diff proportional to the feature.
- **Reproducible demo.** `scripts/asset_library_fixture.py` drives the real seam, is
  idempotent, and plants one deliberately unavailable Asset.

## Cons

- **A restart hides local Assets instead of marking them unavailable.**
  `has_live_serve_source` drops any local `Asset` whose `file_id` is no longer in the
  process registry. After a real restart the registry is empty, so those rows vanish from
  the list rather than showing as unavailable. The unavailable state is only reachable
  while the registry entry survives but the bytes are gone — the fixture's path. This is
  documented as deliberate, but it narrows user story 11 ("a file whose bytes are gone
  should still appear, marked unavailable") to a case a plain restart does not hit. This
  is the weakest part; see below.
- **The list does N+1 async probes.** The endpoint calls `resolve_asset_availability` per
  item in a loop, and worker items open a file descriptor each. It is fine at trial scale
  and there is no pagination, but a large library would feel it.
- **Fire-and-forget recording has no backpressure.** `record_asset` schedules the write
  and returns. A failed write is logged, not retried, so a durable row can be lost while
  serving still succeeds. That is the intended trade, but it is a real hole under load or
  outage.
- **`ensure_indexes` runs on every record and list.** The call is idempotent, but it adds
  a round trip to each write and list.
- **The chat runtime changes travel with the feature.** `agent/opencode_runtime.py` gains
  a `question`-tool-to-clarify-card path, a session abort, and in-flight session tracking.
  These make Chat usable for generating sample files, so they serve the trial's setup
  step, but they are behavior changes beyond the library proper.

---

## The three trial questions

**What did not work, and what I did instead.** The native `<iframe>` PDF preview renders
blank on the serve path: the response carries a CSP `sandbox` directive and runs inside
Electron, both of which disable the browser PDF plugin. I replaced it with a PDF.js canvas
renderer in `ArtifactViewer` and added a test that pins the inline, non-sandboxed serve
headers (`tests/test_security_containment.py::test_pdf_inline_preview_is_not_sandboxed`).
Separately, OpenCode's interactive `question` tool blocks the session waiting for a TUI
answer Loma has no way to give, which hung Chat mid-turn; the runtime now surfaces those
questions as clarify cards, aborts the session, and refuses to reuse an in-flight session.

**Weakest part, and what breaks it first.** The restart behavior above. The first thing to
break it is the obvious test a reviewer runs: generate a file, restart the stack, open
`/library`. The local Asset is gone from the list, not shown unavailable, which reads as
data loss even though the pointer is safe in Mongo. The fix is to stop treating a missing
registry entry as a reason to hide the row: list the row and let `resolve_asset_availability`
return `unavailable` with "registry cleared on restart". The resolver already speaks that
sentence; only the filter in `handle_list_assets` drops the row before it is asked.

**What I deliberately did not build.** In-library image preview (the reused viewer sends
images to download). Sharing, uploads, folders, a new blob store, and remote workers. A
formal per-provider adapter framework — the typed descriptor and single seam already give
plug-and-play, and the framework is a broader refactor out of proportion to the trial.
Historical migration and code-artifact indexing. A search UI, though the endpoint is
shaped for it.

---

## Rating

Reviewed on the two axes the `code-review` skill separates: does the code match the
repo's standards, and does it match what `assessment.md` and `DESIGN.md` asked for.

### Spec — 9 / 10

The trial asked for a place to find file-backed outputs you can access, open or download
them, and return to their source conversation, with access enforced on the backend and
the file infrastructure's limits explained. Every one of those lands:

- **Find, open, download, return to source** — all present and demoable through the
  fixture.
- **Backend-enforced access** — the sharpest requirement, and the implementation nails it:
  filter by identity on the list, reuse the owner-checked serve path for bytes, no UI
  trust. Containment tests prove the foreign-access case fails closed.
- **Explain the file infrastructure's limits** — done thoroughly. The ephemeral-registry
  trade is stated in `DESIGN.md` and here, and the availability resolver reports the real
  reason.
- **Depth over breadth** — the scope is one coherent slice, cut cleanly against a written
  list of non-goals.

The point off is the restart gap: user story 11 is only partly delivered, and the common
reproduction (generate, restart, open) does not show the unavailable state the story
promises. The gap is documented, which is what the trial rewards, but it is still a
divergence between the promise and the behavior.

### Standards — 9 / 10

- **Modularity** — new files with one-line taps, not edits buried in existing functions,
  exactly as `DESIGN.md` set out. No shotgun surgery.
- **Naming and types** — a typed `AssetDescriptor`, honest names, a `CONTEXT.md` glossary
  that the code follows. No mysterious names or primitive obsession at the seam.
- **Tests** — assert external behavior at the HTTP and store seams, reuse the existing
  containment and delivery patterns, and all pass:
  - `pytest tests/test_asset_store.py tests/test_asset_list.py tests/test_asset_availability.py tests/test_asset_library_fixture.py tests/test_observability_db.py` → 25 passed
  - `pytest tests/test_opencode_runtime.py tests/test_security_containment.py` → 65 passed
  - `node dashboard/tests/library-list.test.cjs` and `library-detail.test.cjs` → 10 passed
- **Docs** — `DESIGN.md`, `CONTEXT.md`, tickets, and an implementation recipe make the
  change easy to follow and review.

The point off is the two small performance smells (per-item availability probes,
`ensure_indexes` on every call) and the runtime changes riding along in the same diff,
which mix a second concern into a library change.

### Overall — 9 / 10

A coherent, well-tested slice that answers the brief's core — findable outputs, real
backend access control, an honest degraded state — through the highest available seam, and
is candid about its one real gap. Fixing the restart filter so local Assets list as
unavailable would close the only meaningful distance between the spec and the code.
