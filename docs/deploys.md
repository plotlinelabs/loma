# Deploys drain in-flight agent runs first

`scripts/deploy.sh` (run by CI on every push to `main`) no longer restarts the
backend under whatever tasks happen to be running. It builds the new images,
puts the running backend into **drain mode**, waits for in-flight agent runs to
finish, and only then swaps the containers.

## What drain mode does

- New dashboard chats and quick-added board tasks are refused with HTTP 503 and
  the message *"Loma is restarting for a deploy. Please try again in a minute."*
  Staged drafts are still allowed (nothing runs until they are sent).
- Slack mentions/DMs get the same message as a thread reply instead of a run.
- Scheduled flows that fire while draining are **deferred**, not skipped: the
  new server runs them right after it boots (`flows.deferred_run_at`).
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
| `DRAIN_MAX_WAIT` | `600` | Seconds to wait for `running == 0`. `0` skips draining. |
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
