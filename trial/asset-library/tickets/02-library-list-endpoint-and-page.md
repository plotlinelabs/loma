# 02: Library list endpoint + page

**What to build:** A **Library** in the dashboard nav where a signed-in user sees only the
file-backed outputs **they** generated — newest first, each showing name, type, size, and
when it was created. A new backend list endpoint returns only the authenticated user's
Assets, filtered server-side. This is the first end-to-end demoable slice: sign in, open
the Library, see your Assets.

The endpoint and its client are shaped so a future **search** filter slots in without a
rewrite (accept a query object even though v1 sends none).

Read first: `trial/asset-library/DESIGN.md` (Access, UX, Search-ready minimalism). Use the
existing split-pane list/detail page (the Skills surface) as the layout prior art, and the
existing typed API-client + nav patterns.

**Blocked by:** 01 (Asset store + recording seam).

**Status:** ready-for-agent

- [ ] A `GET /api/assets` endpoint returns only the caller's Assets, owner taken from the
      authenticated identity, newest-first; unauthenticated requests are rejected (401).
- [ ] The response shape accommodates a future query/search parameter without a breaking
      change.
- [ ] A `/library` route renders a split-pane surface — list populated on one side, detail
      pane a placeholder for now — and is added to the nav for all authenticated users.
- [ ] A user with no Assets sees a clear empty state.
- [ ] Another user's Assets never appear in the list.

**Testing plan** (backend mirrors `tests/test_security_containment.py` HTTP patterns):

- Owner-only — the list returns the caller's Assets and none belonging to a second user.
- Empty vs populated — a user with no Assets gets an empty list; a user with Assets gets
  them.
- Ordering — results are newest-first.
- Auth — an unauthenticated request gets 401.
- Frontend — a light render smoke: rows render from a mocked fetch; the empty state
  renders when there are none.
