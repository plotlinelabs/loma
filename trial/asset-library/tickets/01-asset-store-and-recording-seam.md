# 01: Asset store + recording seam

**What to build:** When any provider registers a file-backed output, the system durably
records exactly one owner-scoped **Asset**, so that output can later be listed in the
Library. Introduce a dedicated `assets` collection and an Asset-store module that owns
recording, listing, and availability. Tap the single registration function that every
provider funnels through with a **one-line** call that passes a typed descriptor
`(file_id / path, owner_email, conversation_id, source)`. No UI in this ticket — this is
the backbone the rest ride on.

Read first: `trial/asset-library/DESIGN.md` (Data model, Recording seam, Modularity) and
`CONTEXT.md`. Background on the registration function and the process-local served-file
registry is in `knowledge.md`.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A new `assets` collection exists with the eight fields from DESIGN (`file_id`
      unique, `owner_email`, `conversation_id`, `name`, `mime_type`, `size_bytes`,
      `created_at`, `source`) and indexes `(owner_email, created_at desc)` and unique
      `file_id`.
- [ ] An Asset-store module exposes `record_asset(descriptor)`,
      `list_assets(owner_email)` (newest-first), and `resolve_asset_availability(file_id)`
      (stub in this ticket: available when servable, otherwise a neutral unknown — full
      reasons land in ticket 04).
- [ ] The existing registration function records an Asset via **one added line**; its
      current return value and serving behaviour are unchanged.
- [ ] Registering a file writes exactly one Asset row with correct provenance
      (`source = opencode` on the local path).
- [ ] A single turn that produces two files yields two Asset rows sharing one
      `conversation_id`.
- [ ] Recording is **fail-soft**: if the durable write fails, serving still succeeds and
      the failure is logged, never raised into the request path.

**Testing plan** (mirror `tests/test_phase4_file_delivery.py` and
`tests/test_security_containment.py`):

- Unit — `record_asset` writes the expected document; `list_assets` returns only the
  owner's rows, newest-first; the unique `file_id` constraint holds on a duplicate.
- Seam — driving the real registration path creates exactly one row with correct fields
  and provenance; a dual-file turn creates two rows with a shared `conversation_id`.
- Fail-soft — a simulated durable-write error does not propagate into the serving path.
