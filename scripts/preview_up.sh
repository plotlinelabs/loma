#!/usr/bin/env bash
# Bring up (or update) an isolated preview stack for a PR on the preview box.
#
# Run from the root of a checkout of the PR's code:
#   PREVIEW_BASE_DOMAIN=preview.example.com bash scripts/preview_up.sh <PR_NUMBER>
#
# Isolation per PR: COMPOSE_PROJECT_NAME=loma-pr-<N> (prefixes containers, network,
# and named volumes), unique loopback host ports, a dedicated Mongo database
# (loma_pr_<N>), and a stable per-PR AUTH_SECRET. Live integration secrets come
# from a shared, untracked secrets file on the box (PREVIEW_SECRETS_FILE).
#
# Safety: previews run with the scheduler and Slack Socket Mode OFF so they never
# double-fire scheduled flows or double-consume the production Slack app's events.
set -euo pipefail

PR="${1:?usage: preview_up.sh <PR_NUMBER>}"
[[ "$PR" =~ ^[0-9]+$ ]] || { echo "PR number must be numeric: $PR" >&2; exit 2; }

: "${PREVIEW_BASE_DOMAIN:?PREVIEW_BASE_DOMAIN is required (e.g. preview.example.com)}"
SECRETS_FILE="${PREVIEW_SECRETS_FILE:-$HOME/preview-secrets.env}"
VHOST_DIR="${PREVIEW_VHOST_DIR:-$HOME/preview-proxy/conf.d}"
FRONT_PROXY_CONTAINER="${PREVIEW_FRONT_PROXY:-preview-proxy}"
STATE_DIR="${PREVIEW_ROOT:-$HOME/previews}"

[[ -f "$SECRETS_FILE" ]] || { echo "secrets file not found: $SECRETS_FILE" >&2; exit 3; }

# Deterministic, non-colliding loopback host ports derived from the PR number.
BASE=$(( 20000 + PR * 10 ))
export COMPOSE_PROJECT_NAME="loma-pr-${PR}"
export BACKEND_HOST_PORT="$BASE"
export DASHBOARD_HOST_PORT="$(( BASE + 1 ))"
export NGINX_HOST_PORT="$(( BASE + 2 ))"
export NGINX_HOST_BIND="127.0.0.1"

HOST="pr-${PR}.${PREVIEW_BASE_DOMAIN}"
URL="https://${HOST}"

read_secret() { grep -E "^${1}=" "$SECRETS_FILE" | head -1 | cut -d= -f2-; }

# Stable per-PR AUTH_SECRET so sessions survive redeploys.
mkdir -p "$STATE_DIR"
SECRET_CACHE="${STATE_DIR}/.auth-secret-pr-${PR}"
[[ -f "$SECRET_CACHE" ]] || openssl rand -base64 32 > "$SECRET_CACHE"
AUTH_SECRET="$(cat "$SECRET_CACHE")"

MONGO_URI="$(read_secret OBSERVABILITY_MONGODB_URI)"
SETUP_TOKEN="$(read_secret LOMA_SETUP_TOKEN)"

# --- Backend .env: live integration secrets + preview overrides ---
{
  cat "$SECRETS_FILE"
  cat <<EOF

# --- preview overrides (pr-${PR}; appended, so they win over the lines above) ---
ENV=PROD
PUBLIC_BASE_URL=${URL}
OBSERVABILITY_DB_NAME=loma_pr_${PR}
LOMA_ENABLE_SCHEDULER=false
LOMA_ENABLE_SLACK=false
NEXT_PUBLIC_AUTH_PROVIDER=local
EOF
} > .env

# --- Dashboard .env ---
cat > dashboard/.env <<EOF
AUTH_SECRET=${AUTH_SECRET}
AUTH_PROVIDER=local
NEXT_PUBLIC_AUTH_PROVIDER=local
AUTH_URL=${URL}
BACKEND_URL=http://loma-backend:3000
OBSERVABILITY_MONGODB_URI=${MONGO_URI}
OBSERVABILITY_DB_NAME=loma_pr_${PR}
LOMA_SETUP_TOKEN=${SETUP_TOKEN}
EOF

echo "[preview] up ${COMPOSE_PROJECT_NAME} (backend=${BACKEND_HOST_PORT} dashboard=${DASHBOARD_HOST_PORT} nginx=${NGINX_HOST_PORT})"
docker compose up -d --build

# --- Front-proxy vhost: route the PR subdomain to this stack's nginx ---
mkdir -p "$VHOST_DIR"
cat > "${VHOST_DIR}/pr-${PR}.conf" <<EOF
server {
    listen 443 ssl;
    http2 on;
    server_name ${HOST};

    ssl_certificate     /etc/nginx/certs/origin.pem;
    ssl_certificate_key /etc/nginx/certs/origin.key;

    client_max_body_size 110m;

    location / {
        proxy_pass http://127.0.0.1:${NGINX_HOST_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host  \$host;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$http_connection;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }
}
EOF
docker exec "$FRONT_PROXY_CONTAINER" nginx -s reload 2>/dev/null \
  || echo "[preview] warning: could not reload front proxy '${FRONT_PROXY_CONTAINER}'" >&2

# --- Health check through this stack's own nginx ---
ok=0
for _ in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${NGINX_HOST_PORT}/" || echo 000)
  if [ "$code" != "000" ]; then ok=1; break; fi
  sleep 3
done
[ "$ok" = 1 ] || { echo "[preview] health check failed for ${COMPOSE_PROJECT_NAME}" >&2; docker compose ps; exit 1; }

echo "[preview] ready: ${URL}"
