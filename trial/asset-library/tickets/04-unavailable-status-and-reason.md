# 04: Unavailable status + dynamic reason

**What to build:** An Asset whose bytes are no longer available still appears in the
Library, marked **unavailable**, and states **why** — a reason derived from the actual
failing condition, never a hardcoded string. This is the degraded-state behaviour that
makes the ephemeral-registry tradeoff honest and visible.

Read first: `trial/asset-library/DESIGN.md` (Availability, Further Notes). The real
conditions a file can be in — present in the registry, bytes missing on disk, durable
worker record expired — are described in `knowledge.md` (serve path 404/410 and the worker
store).

**Blocked by:** 02 (Library list endpoint + page).

**Status:** ready-for-agent

- [ ] `resolve_asset_availability` (stubbed in ticket 01) now probes the same sources the
      serve path trusts — registry, then disk, then the durable worker record — and
      returns `(status, reason)`.
- [ ] The list surfaces `status` and `reason` per item; unavailable items render
      distinctly and disable open/download.
- [ ] The reason reflects the real condition (registry cleared on restart vs bytes missing
      at their path vs worker record expired) and is not a fixed string.
- [ ] An intact Asset reports available and opens normally.

**Testing plan:**

- Backend — register a file, then remove its bytes: the resolver returns `unavailable`
  with a condition-specific reason; an intact Asset returns `available`. Distinct
  conditions produce distinct reasons.
- Frontend — an unavailable row renders its reason and disables open/download; an available
  row enables them.
