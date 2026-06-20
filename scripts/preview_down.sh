#!/usr/bin/env bash
# Tear down a PR's preview stack on the preview box: stop containers + volumes,
# drop the isolated Mongo database, remove the front-proxy vhost, and clean up.
#
#   bash scripts/preview_down.sh <PR_NUMBER>
set -euo pipefail

PR="${1:?usage: preview_down.sh <PR_NUMBER>}"
[[ "$PR" =~ ^[0-9]+$ ]] || { echo "PR number must be numeric: $PR" >&2; exit 2; }

STATE_DIR="${PREVIEW_ROOT:-$HOME/previews}"
VHOST_DIR="${PREVIEW_VHOST_DIR:-$HOME/preview-proxy/conf.d}"
FRONT_PROXY_CONTAINER="${PREVIEW_FRONT_PROXY:-preview-proxy}"
SECRETS_FILE="${PREVIEW_SECRETS_FILE:-$HOME/preview-secrets.env}"
DIR="${STATE_DIR}/pr-${PR}"

export COMPOSE_PROJECT_NAME="loma-pr-${PR}"

if [ -d "$DIR" ]; then
  ( cd "$DIR" && docker compose down -v --remove-orphans ) || true
fi

# Drop the isolated Mongo database (best effort).
if [ -f "$SECRETS_FILE" ]; then
  URI="$(grep -E '^OBSERVABILITY_MONGODB_URI=' "$SECRETS_FILE" | head -1 | cut -d= -f2-)"
  if [ -n "${URI:-}" ] && command -v mongosh >/dev/null 2>&1; then
    mongosh "$URI" --quiet --eval "db.getSiblingDB('loma_pr_${PR}').dropDatabase()" || true
  fi
fi

rm -f "${VHOST_DIR}/pr-${PR}.conf"
docker exec "$FRONT_PROXY_CONTAINER" nginx -s reload 2>/dev/null \
  || echo "[preview] warning: could not reload front proxy '${FRONT_PROXY_CONTAINER}'" >&2

rm -rf "$DIR"
rm -f "${STATE_DIR}/.auth-secret-pr-${PR}"
echo "[preview] torn down pr-${PR}"
