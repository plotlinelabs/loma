# Human task setup

Deploy the backend and dashboard from this PR before setup. They must use the same
existing Loma Mongo database (the same requirement as dashboard authentication).
No new database, service, or manually shared secret is required.

1. Sign in as an active admin or maintainer.
2. Open **Admin > Environment > Human task approvals**.
3. Click **Set up approvals**. The server generates a key and verifies a signed
   request to the backend. A connected status proves both services used that key.
4. Refresh status. Both the gateway and agent continuation should be running.
   An existing `LOMA_ENABLE_SCHEDULER=false` remains an explicit deployment
   kill switch, including on isolated test stacks. Ask the deployment owner to
   investigate if continuation is unavailable; setup does not edit that setting.
5. Admins can change **Agent settings** on the same card. Bounded work defaults
   off; Ashby defaults to no access. Only active account emails are accepted.
   Changes take effect without a restart; in-flight work is not interrupted.
   Legacy environment overrides take precedence and are clearly shown.


`BACKEND_URL` remains the dashboard's existing backend connection; this feature
adds no dashboard deployment variables. Existing `LOMA_WORK_GATEWAY_SECRET`
environments continue working until managed setup is performed. The managed key
then takes precedence on both services. It does not require a restart.

## Security and recovery

- Setup requires a current, active admin/maintainer record and an authenticated
  dashboard session. Client-supplied identity/role headers cannot authorize it.
- POST requires the same origin. Keys never appear in API responses, the browser,
  screenshots or logs. The record contains creator and creation time for audit.
- Repeated/concurrent setup uses the unique record ID and `$setOnInsert`; it does
  not rotate an existing key. “Reconnect gateway” safely retries setup/verification.
- The key lives in the server-only `gateway_config` collection in the existing DB.
  This retains the existing server trust boundary: deployment operators and DB
  administrators are trusted. Do not expose this collection through generic tools
  or give untrusted agents direct infrastructure/database credentials.
- Failed configuration reads fail closed. A saved key is not shown as connected
  unless a signed backend check succeeds. Mixed versions/different DBs require
  deployment configuration to be corrected; never weaken signature validation.
- Completing a task still does not imply financial approval. This change does not
  alter decisions, task permissions, or agent-resume behavior.

## Validation

Python: `LOMA_LOCAL_E2E=1 .venv/bin/python -m pytest tests/test_gateway_setup.py tests/test_human_tasks.py tests/test_human_tasks_integration.py tests/test_bounded_work.py tests/test_worker_proposals.py tests/test_worker_account_reporting.py -q`
(use supported Python 3.12 and an isolated `loma_local_*` database).

Dashboard: `node --test tests/gateway-setup.test.cjs` and `npx tsc --noEmit`.

Browser: `tests/gateway-setup.browser.cjs` against the isolated local stack with a
fresh DB, both services' gateway env values empty, Slack and scheduler disabled.
Use the isolated dashboard process environment; set `NODE_PATH` for Playwright and optionally
`CHROMIUM_PATH`. Exercises real login, setup, backend verification, concurrent
retry, role revocation, cross-origin rejection, and desktop/mobile screenshots.
