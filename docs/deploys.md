# Deploys drain in-flight agent runs first

`scripts/deploy.sh` (run by CI on every push to `main`) no longer restarts the
backend under whatever tasks happen to be running. It builds the new images,
puts the running backend into **drain mode**, waits for in-flight agent runs to
finish, and only then swaps the containers.

## What drain mode does

New work is **queued, not refused** (`api/pending_runs.py`, Mongo collection
`pending_runs`):

- Dashboard chat messages return HTTP 202 `{"queued": true}`. The message is
  saved on the conversation right away (status `queued`), so the chat shows it
  with a "Queued, Loma is updating" tag and nothing typed is lost.
- Quick-added board tasks are created as usual and their first run is queued.
  The card sits in the Working column until it starts.
- Slack mentions/DMs get *"Queued. Loma is updating and will start this as
  soon as it's back."* and run through the normal Slack handler later.
- A run that was waiting behind a busy conversation (webhooks, Linear,
  Telegram) when the drain started is queued too.
- Scheduled flows that fire while draining are **deferred**, not skipped: the
  new server runs them right after it boots (`flows.deferred_run_at`).

The new backend starts everything queued as soon as it boots, then re-checks
every 30s. Several queued messages for one chat run as one merged turn (same as
messages sent while the agent is busy); Slack messages run one at a time per
thread. Each entry is claimed atomically, so nothing runs twice. Pressing Stop
on a queued chat drops it. Entries nobody picked up within 24h are marked
`failed`.

If a deploy is aborted (`DELETE /health/drain`, `DRAIN_ON_TIMEOUT=fail`, or
any `deploy.sh` failure after the drain started), the drain is cleared and the
current backend starts the queue itself. `deploy.sh` also clears the drain after
`docker compose up -d`, in case the backend container wasn't recreated.

The old 503 / "try again in a minute" reply is only used when a run can't be
queued (no database, or attachments over 8MB).
- Webhook-triggered runs (GitHub, Linear, Pylon, incoming webhooks) are not
  gated; the deploy simply waits for them like any other run.

A run counts as "in flight" only while its heartbeat is fresh (60s), so a doc
stuck at `status: running` from a crash can never block a deploy forever.

## If runs are still going when the wait expires

The backend now shuts down gracefully on SIGTERM. Anything still running is
marked `interrupted` with the error `Interrupted by deploy <sha>`, and the
owner is told: dashboard chats/tasks get an inbox notification (plus the
existing "Task was interrupted" push for active board tasks), Slack threads
get a reply asking them to continue in-thread.

## Knobs

| Env (deploy.sh) | Default | Meaning |
|---|---|---|
| `DRAIN_MAX_WAIT` | `300` | Seconds to wait for `running == 0`. `0` skips draining. |
| `DRAIN_ON_TIMEOUT` | `proceed` | `fail` aborts the deploy (and clears drain) instead of restarting over live runs. |

`docker-compose.yml` gives `loma-backend` a `stop_grace_period` of 30s so the
shutdown notifications have time to land before Docker's SIGKILL.

## Endpoints (public prefix, no dashboard session)

```
GET    /health                 -> {"status": "ok", "draining": false}
GET    /health/drain           -> {"draining", "reason", "since", "running", "oldest_started_at"}
POST   /health/drain {"reason": "deploy abc1234"}   # loopback only
DELETE /health/drain                                # loopback only
```

The mutating verbs only accept loopback callers. From the host, go through the
container, which is exactly what `deploy.sh` does:

```bash
docker compose exec -T loma-backend curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"reason":"manual maintenance"}' http://127.0.0.1:3000/health/drain
docker compose exec -T loma-backend curl -s http://127.0.0.1:3000/health/drain
docker compose exec -T loma-backend curl -s -X DELETE http://127.0.0.1:3000/health/drain
```
