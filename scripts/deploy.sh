#!/usr/bin/env bash
# Build and (re)start the Loma stack, then verify both services respond.
# Generic by design: contains no host/URL/secrets. Invoked by CI after the repo
# has been fast-forwarded to origin/main on the server.
#
# Deploys drain first (see api/drain.py): the running backend stops accepting
# new agent runs and we wait, bounded, for in-flight ones to finish, so the
# container swap doesn't kill someone's task or chat halfway through.
#   DRAIN_MAX_WAIT    seconds to wait for running=0 (default 600; 0 skips drain)
#   DRAIN_ON_TIMEOUT  "proceed" (default) or "fail" if runs are still active
set -euo pipefail

cd "$(dirname "$0")/.."
SHA=$(git rev-parse --short HEAD)
echo "Deploying $SHA"

# Build while the old stack keeps serving so the swap below is quick.
docker compose build

DRAIN_MAX_WAIT="${DRAIN_MAX_WAIT:-600}"
DRAIN_ON_TIMEOUT="${DRAIN_ON_TIMEOUT:-proceed}"

# Talk to the running backend from inside its container: the drain toggles are
# loopback-only, and this works whether or not :3000 is published on the host.
drain_api() {
  docker compose exec -T loma-backend curl -sf --max-time 5 -X "$1" \
    -H 'Content-Type: application/json' "${@:2}" http://127.0.0.1:3000/health/drain
}
# Pull N out of {"running": N, ...} without needing jq/python on the host.
running_count() {
  printf '%s' "$1" | grep -o '"running": *[0-9]*' | grep -o '[0-9]*$' || true
}

running=""
if [ "$DRAIN_MAX_WAIT" -gt 0 ] && drain_api POST -d "{\"reason\":\"deploy $SHA\"}" >/dev/null 2>&1; then
  echo "Draining: waiting up to ${DRAIN_MAX_WAIT}s for in-flight agent runs to finish"
  deadline=$((SECONDS + DRAIN_MAX_WAIT))
  drained=0
  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(drain_api GET 2>/dev/null || true)
    running=$(running_count "$status")
    if [ -z "$running" ]; then
      echo "  drain status unavailable; not waiting"
      drained=1
      break
    fi
    if [ "$running" -eq 0 ]; then
      drained=1
      break
    fi
    echo "  $running run(s) still active ($((deadline - SECONDS))s left)"
    sleep 10
  done
  if [ "$drained" = 1 ]; then
    echo "Drained: no agent runs in flight"
  elif [ "$DRAIN_ON_TIMEOUT" = "fail" ]; then
    echo "Drain timed out with $running run(s) still active; aborting (DRAIN_ON_TIMEOUT=fail)"
    drain_api DELETE >/dev/null 2>&1 || true
    exit 1
  else
    echo "Drain timed out with $running run(s) still active; proceeding (owners are notified on shutdown)"
  fi
else
  echo "Backend not reachable for drain (first deploy, stack down, or DRAIN_MAX_WAIT=0); proceeding"
fi

docker compose up -d

# The nginx reverse-proxy config is bind-mounted. In TLS mode the nginx image
# renders /etc/nginx/templates/*.template into /etc/nginx/conf.d/ at container
# start, so a reload is not enough for template-only changes. Force-recreate the
# proxy after the stack is up; this is quick and preserves backend/dashboard.
docker compose up -d --force-recreate --no-deps nginx

# Reload after validation as a cheap safety net for plain bind-mounted config
# changes and to fail loudly before the health check if the config is invalid.
if docker compose exec -T nginx nginx -t >/dev/null 2>&1; then
  docker compose exec -T nginx nginx -s reload || true
fi

# Liveness through the nginx proxy (:80): the dashboard ("/") and the backend
# (a webhook path, which nginx routes straight to the backend) must both respond.
# Any non-000 code proves the path is routed and the upstream is up. We probe a
# backend-routed path rather than /api/* (which now goes via the dashboard).
ok=0
for _ in $(seq 1 40); do
  d=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost/ || echo 000)
  b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST http://localhost/webhooks/github || echo 000)
  if [ "$d" != "000" ] && [ "$b" != "000" ]; then ok=1; break; fi
  sleep 3
done

if [ "$ok" != 1 ]; then
  echo "health check failed (dashboard=$d backend=$b)"
  docker compose ps
  exit 1
fi
echo "healthy (dashboard=$d backend=$b via :80)"
