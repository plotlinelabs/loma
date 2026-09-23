#!/usr/bin/env bash
# Fresh, project-scoped Docker smoke test. No operator .env or existing volumes.
set -euo pipefail
cd "$(dirname "$0")/.."
work=$(mktemp -d)
project="loma-login-test-$(date +%s)-$$"
cleanup() {
  docker compose --project-directory "$work" -p "$project" logs --no-color loma-login loma-login-proxy || true
  docker compose --project-directory "$work" -p "$project" down -v || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
# Committed sources only; deliberately excludes local secrets and working files.
git archive HEAD | tar -x -C "$work"
touch "$work/.env" "$work/dashboard/.env"
docker compose --project-directory "$work" -p "$project" up -d --build --wait loma-login loma-login-proxy
docker compose --project-directory "$work" -p "$project" exec -T loma-login python < "$work/scripts/test-bundled-login.py"
