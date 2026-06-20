#!/usr/bin/env bash
# Build and (re)start the Loma stack, then verify both services respond.
# Generic by design: contains no host/URL/secrets. Invoked by CI after the repo
# has been fast-forwarded to origin/main on the server.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "Deploying $(git rev-parse --short HEAD)"

docker compose up -d --build

# Liveness through the nginx proxy (:80): the dashboard ("/") and the backend
# ("/api/...") must both respond. Any non-000 code proves the path is routed and
# the upstream is up. This also validates the reverse-proxy routing itself.
ok=0
for _ in $(seq 1 40); do
  d=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost/ || echo 000)
  b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost/api/skills || echo 000)
  if [ "$d" != "000" ] && [ "$b" != "000" ]; then ok=1; break; fi
  sleep 3
done

if [ "$ok" != 1 ]; then
  echo "health check failed (dashboard=$d backend=$b)"
  docker compose ps
  exit 1
fi
echo "healthy (dashboard=$d backend=$b via :80)"
