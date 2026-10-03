#!/usr/bin/env bash
# Pre-deploy guard for the project .env. Containers keep the env they started
# with, so a damaged .env only surfaces when a deploy recreates them; this makes
# that deploy fail instead, leaving the running stack up.
#
#   check_env.sh           back up .env, then fail if it lost keys since the
#                          last recorded deploy (prints key names, never values)
#   check_env.sh --record  record the current key set (run after a healthy deploy)
#
#   ALLOW_ENV_KEY_REMOVAL=1  accept removed keys for this deploy
#   ENV_FILE, ENV_BASELINE, ENV_BACKUP_DIR, ENV_BACKUP_KEEP  override defaults
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
ENV_BASELINE="${ENV_BASELINE:-.env.deployed-keys}"
ENV_BACKUP_DIR="${ENV_BACKUP_DIR:-.env.backups}"
ENV_BACKUP_KEEP="${ENV_BACKUP_KEEP:-30}"
umask 077

keys() {
  sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=.*/\2/p' "$1" | LC_ALL=C sort -u
}

if [ ! -f "$ENV_FILE" ]; then
  echo "env guard: $ENV_FILE not found; refusing to deploy"
  exit 1
fi

if [ "${1:-}" = "--record" ]; then
  keys "$ENV_FILE" > "$ENV_BASELINE"
  echo "env guard: recorded $(wc -l < "$ENV_BASELINE" | tr -d ' ') keys"
  exit 0
fi

# Back up only when the content changed since the newest backup, so repeated
# failing deploys cannot rotate the last good copy out.
mkdir -p "$ENV_BACKUP_DIR"
latest=$(ls -1t "$ENV_BACKUP_DIR"/env.* 2>/dev/null | head -n 1 || true)
if [ -z "$latest" ] || ! cmp -s "$ENV_FILE" "$latest"; then
  cp "$ENV_FILE" "$ENV_BACKUP_DIR/env.$(date -u +%Y%m%dT%H%M%SZ).$$"
  ls -1t "$ENV_BACKUP_DIR"/env.* | tail -n +$((ENV_BACKUP_KEEP + 1)) | while read -r old; do rm -f "$old"; done
fi

if [ ! -f "$ENV_BASELINE" ]; then
  echo "env guard: no recorded key set yet; skipping removal check"
  exit 0
fi

missing=$(LC_ALL=C comm -23 "$ENV_BASELINE" <(keys "$ENV_FILE"))
if [ -z "$missing" ]; then
  echo "env guard: ok ($(keys "$ENV_FILE" | wc -l | tr -d ' ') keys)"
  exit 0
fi

count=$(printf '%s\n' "$missing" | wc -l | tr -d ' ')
if [ "${ALLOW_ENV_KEY_REMOVAL:-}" = "1" ]; then
  echo "env guard: $count key(s) removed since last deploy; allowed by ALLOW_ENV_KEY_REMOVAL=1"
  exit 0
fi
echo "env guard: $ENV_FILE is missing $count key(s) present at the last deploy:"
printf '%s\n' "$missing" | sed 's/^/  /'
echo "Restore from $ENV_BACKUP_DIR/, or rerun with ALLOW_ENV_KEY_REMOVAL=1 if the removal is intended."
exit 1
