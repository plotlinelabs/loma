#!/usr/bin/env bash
# Build and (re)start the Loma stack, then verify both services respond.
# Generic by design: contains no host/URL/secrets. Invoked by CI after the repo
# has been fast-forwarded to origin/main on the server.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "Deploying $(git rev-parse --short HEAD)"

docker compose up -d --build

# Liveness: backend (:3000, any HTTP code proves the aiohttp server is up) and
# dashboard (:3001) must respond. 000 means no connection.
ok=0
for _ in $(seq 1 40); do
  b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:3000/ || echo 000)
  d=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:3001/ || echo 000)
  if [ "$b" != "000" ] && [ "$d" != "000" ]; then ok=1; break; fi
  sleep 3
done

if [ "$ok" != 1 ]; then
  echo "health check failed (backend=$b dashboard=$d)"
  docker compose ps
  exit 1
fi
echo "healthy (backend=$b dashboard=$d)"
