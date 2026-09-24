# Google Sheets linked skills

## Rollout and user flow

Set `LOMA_GOOGLE_SHEETS_SKILLS_ENABLED=true` to enable the independent Sheets
feature flag. It defaults off; Google Docs behavior remains enabled by default.
No data migration is required. Import creates the partial unique source indexes.

In Skills, choose **Import from Google**, paste a native spreadsheet URL, choose
one tab and optionally use the first nonempty row as headers. A `gid` in either
the query or fragment preselects the tab, including `gid=0`. Review the exact
rendered content before importing. Read access to the spreadsheet is sufficient.
The linked tab remains identified by numeric `sheetId` after a rename/reorder.
Deleting/recreating a same-named tab suspends the link instead of rebinding it.

Imports are personal by default. Publishing to the workspace requires explicit
confirmation and does not alter Google's sharing. Only the owner can change
source configuration/sharing. Existing maintainer controls apply to mutations.

## Content contract

The instruction body is deterministic Markdown containing the source link and
JSON-formatted row records (original row numbers and column letters). This keeps
multiline values, Unicode, duplicate/blank headers, blank cells and literal
Markdown unambiguous. Column letters distinguish a blank cell from a column that
isn't present in the rendered grid. Leading/trailing empty rows and trailing empty
columns are omitted; interior empty rows remain. The optional header record is
separate from data rows. The renderer marks all rows as reference material, not
authorization to perform actions. It does not execute or rewrite cell contents.

- Import `formattedValue`, including calculated formula results, not formula text.
- Include underlying rows hidden by filters and disclose this in the preview.
- Block user-hidden rows/columns, hidden/non-grid tabs, merges, charts, slicers,
  formula errors (with cell address), smart chips, pivots, data-source cells,
  and IMAGE/SPARKLINE formulas. Invalid content never replaces the last good copy.
- Exclude comments, notes, styling and floating images/drawings. **The Sheets API
  does not reliably expose floating images/drawings**, so these cannot be rejected
  automatically. The UI explicitly requires a dedicated text-only source tab.
- Maximum rendered content: 200,000 UTF-8 bytes. Maximum response: 8 MB.
- Maximum **allocated grid**: 50,000 cells (configurable via
  `LOMA_SHEETS_SKILL_MAX_CELLS`). This includes blank rows/columns and is checked
  before fetching grid data, then checked again on the returned snapshot. Large
  sparse tabs must have unused rows/columns removed or use a smaller source tab.
  Nothing is silently truncated. HTTP reads time out after 25 seconds.

## Synchronization and editing

One selected tab maps to one skill. Sheets is **pull-only**. `SKILL.md` editing,
package replacement, directory imports and version-restoration through package
replacement are blocked server-side while linked. Supporting files remain editable.
The source panel links to Google Sheets and offers sync, pause/resume, history and
disconnect. Disconnect preserves the last good content and access controls;
deleting a skill never deletes the Google source.

Reuse the existing lease, fenced atomic publication, version repair, runtime
refresh and retry machinery. Polling normally runs every 5 to 5.5 minutes, with
jitter. Pausing disables automatic reads, not manual sync. Formula-result changes
are detected by content reads, never solely by Drive modified time. An unchanged
hash does not add a version. Renames update metadata without rewriting content.
The hash binds spreadsheet ID, tab ID, header setting, renderer version and body,
so an old preview cannot authorize changed settings or source content.

Confirmed access loss/deletion or a missing connection suspends runtime reads;
invalid content and temporary failures retain the last good snapshot. Rate-limit
403/429 errors are transient, not revocation. Missing OAuth scopes request
reconnection. Sync always uses the source owner's personal Google connection.
No service-account fallback or Sheets write operation exists.

The dispatcher selects each provider independently and processes oldest due items
first, with four concurrent workers. Sheets requests use shared Mongo minute
buckets (`skill_source_quotas`, TTL expiry) across scheduler processes and manual
previews: 25 requests/account/minute, 140/project/minute, conservatively reserving
quota headroom. Quota exhaustion defers a sync with exponential backoff and jitter.
These buckets cover this integration, not unrelated Google API consumers.

## API

- `GET /api/skill-sources/google-sheets`: feature availability.
- `POST /api/skill-sources/google-sheets/preview`: `url`, optional `tab_id`, boolean
  `header_row` (default false).
- `POST /api/skill-sources/google-sheets/import`: same selection/settings plus
  `slug`, `name`, `description`, `preview_hash`, optional `scope`,
  `confirm_workspace`. The tab ID is a string in HTTP requests, numeric in storage.
- Existing `/api/skills/{slug}/source` and `/source/history` manage either provider.

Personal runtime discovery still requires an authenticated actor. Shared prompt
caches exclude linked skills; private content stays private across file/version/
history/search endpoints, supporting files, disconnect and deletion.

## Validation

```sh
python -m pytest tests/test_google_sheets_skills.py tests/test_google_docs_skills.py \
  tests/test_skill_service.py tests/test_loma_skills_cli.py -q
cd dashboard && npx tsc --noEmit
```

Tests use mocked Google responses and an in-memory MongoDB. Browser verification
uses the real local app, authenticated API and a throwaway Mongo DB, with only the
Google Sheets adapter replaced by an offline fixture. No customer spreadsheet or
OAuth connection is needed or changed. Live Google OAuth/API verification remains
a rollout smoke test; offline tests cannot certify the deployed Google API scope.

API references:
- [Selecting a tab by stable ID](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/getByDataFilter)
- [Displayed values and unsupported cell structures](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells)
