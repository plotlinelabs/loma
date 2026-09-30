# Optional Google Docs-linked skills

Regular skills retain the existing local storage and edit paths. This integration is
opt-in per new skill and available by default. Set
`LOMA_GOOGLE_DOCS_SKILLS_ENABLED=false` to disable the integration as an emergency
off switch. No migration of existing skills is required.

## Behaviour

- Import a document URL, select a tab, preview it, then confirm. Import never writes
  to Google. Name, description and tags are Loma metadata; only instructions sync.
- Personal visibility is the default. Workspace import requires explicit confirmation.
  Sharing in Loma does not change Google sharing. Linking requires Google edit access.
- One tab's body maps to the instruction body of `SKILL.md`. Supporting files remain
  local and the slug remains stable when the Google document is renamed.
- Dashboard/CLI edits write to Google first using the **acting user's** Google
  connection, never another editor's credentials. Polling uses the connection the
  owner authorized on import. No service accounts are used.
- Linked metadata/frontmatter cannot be changed through instruction editing. Scope
  and folder operations remain Loma-owned, owner-only operations for linked skills.
- Auto-sync normally checks every five minutes; a bounded dispatcher runs every
  30 seconds. Manual sync works while paused. Pending saves reconcile even if paused.
- Disconnect keeps the last validated instructions and enforced access rules, but
  returns content editing to the regular Loma path. Delete never deletes the Google Doc.

## Formatting contract

Supports paragraphs, headings 1-6, **single-level** bullets and decimal numbered lists,
ordinary HTTP/HTTPS/mailto links, bold, italic and literal code/indentation. Code is
literal text, not a conversion to Google's code-block UI. Blank lines are retained.

Tables, drawings, images, smart chips, footnotes, nested lists, internal links,
section breaks within the body, soft line breaks, title/subtitle styles, superscripts,
subscripts and strikethrough are rejected. Unresolved suggestions block publication.
Comments are not executable content. A structural round-trip check also rejects text
and formatting combinations that cannot safely map back to the supported format.
No source text is silently truncated or omitted. Other tabs are never changed.

## Consistency and recovery

The Google Doc is authoritative for linked instructions. The atomic
`skills.source.published_content` snapshot is the execution copy. Main-file reads
and version snapshots overlay this value; publication does not require atomically
updating three MongoDB collections. Regular skills still read from `skill_files`.

A 120-second lease fences per-skill work; operations have a shorter 110-second
deadline. Google writes use a single batch guarded by `requiredRevisionId` and update
changed paragraph spans back-to-front using UTF-16 indexes. Unchanged paragraphs
and other tabs are untouched. Supporting-file/lifecycle mutations share the lease.

The editor must supply the last-read `source.hash`. A changed source returns a
conflict rather than merging or overwriting. The browser retains the draft and
provides the latest published source for comparison. Revision restoration through
instruction editing obeys the same rules. Package replacement/import over a linked
skill is blocked to prevent bypassing source ownership.

Before writing, the selected source snapshot and submitted draft are durably saved
in `skill_sync_history`. Recovery data is never exposed by the history endpoint.
A timeout never causes a blind replay. An unresolved pending write blocks another
write; the next sync reads the current Google source and reconciles. Version repairs
are idempotent using the publication's version ID. Read-back must match the submitted
structural content before the interactive save is reported as successful.

Invalid content and temporary outages retain the last good execution copy. Confirmed
access loss, a missing source tab/document, or a missing OAuth connection suspend
execution until reauthorization or an owner-authorized disconnect. Errors and retry
backoff are visible. No-change polls do not create new instruction versions.

## Access boundaries

API identity is established by the existing authentication middleware. A scoped
context passes that identity to skill reads. CLI identity is verified using the
personal auth token. Linked access is enforced for list/search/get/file/asset,
history, version, diff and mutations. A shared prompt cache deliberately excludes
linked skills; agents discover them using authenticated CLI list/search. This avoids
putting personal skill descriptions in another user's prompt. Suspended skills are
not returned to runtime CLI reads. The dashboard can inspect the retained snapshot.

Regular skills retain their existing authorization behaviour. Disconnect retains
an `access_controlled` marker so it cannot accidentally make private content public.

## API and CLI

- `GET /api/skill-sources/google-docs`: feature availability
- `POST /api/skill-sources/google-docs/preview`: URL and optional tab ID
- `POST /api/skill-sources/google-docs/import`: preview hash, selected tab, metadata,
  visibility and explicit workspace confirmation
- `POST /api/skills/{slug}/source`: `sync`, `pause`, `resume`, `disconnect`
- `GET /api/skills/{slug}/source/history`: sanitized sync audit history
- Existing file edits accept `base_hash` for linked instructions.

```bash
python3 tools/loma_skills.py --user-email "$USER_EMAIL" --auth-token "$TOKEN" get --slug my-skill
python3 tools/loma_skills.py --user-email "$USER_EMAIL" --auth-token "$TOKEN" update-file \
  --slug my-skill --path SKILL.md --content-file /tmp/SKILL.md --base-hash "$BASE_HASH"
python3 tools/loma_skills.py --user-email "$USER_EMAIL" --auth-token "$TOKEN" sync --slug my-skill
```

## Verification and rollout

```bash
python -m pip install -r requirements-test.txt
OBSERVABILITY_MONGODB_URI='' AGENT_DEFAULT_MODEL='opencode-go/deepseek-v4-flash' \
  python -m pytest tests -q --asyncio-mode=auto
cd dashboard && npm ci && npx tsc --noEmit
```

The default-model override prevents the host's production Claude model from altering
an existing test that explicitly expects the OSS OpenCode default.

The opt-in personal CLI below creates, tests and trashes its **own disposable Doc**.
It cannot target an existing document and does not create skill records:

```bash
python3 tools/verify_google_skill_source.py --user-email "$USER_EMAIL" --auth-token "$TOKEN" --run
```

It checks rich formatting, Unicode/whitespace, other-tab isolation, no-op conversion,
stale revision rejection and terminal paragraph edits against the live Google API.
Run it before expanding the supported document subset.

Browser validation uses an isolated `loma_local_*` DB, local-login setup-token flow,
offset ports and disabled Slack/scheduler. An in-memory Google upstream exercises
import, preview, save, pause/manual sync, history and regular-skill regression through
the actual Loma API; the live CLI separately validates Google's write semantics.

The feature is available by default after deployment, while linking remains an
explicit per-skill action. Monitor `source.status`, `source.next_check`,
`source.failures`, pending operations and `runtime_refresh_pending`. Existing
deployments with an explicit false override remain disabled until that override is
removed. Retain the emergency off switch and verify concurrency, access and recovery
before merging.

## Other linked sources

Google Sheets uses the shared lifecycle and privacy protections but is pull-only.
See [Google Sheets linked skills](google-sheets-skills.md) for its independent
feature flag, conversion contract, limits and validation.
