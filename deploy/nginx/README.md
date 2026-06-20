# Reverse proxy (nginx)

The `nginx` service in `docker-compose.yml` is the single external entrypoint. It serves the
dashboard and routes traffic over the internal Docker network, so the backend (`3000`) and
dashboard (`3001`) containers are bound to `127.0.0.1` only and never need to be exposed.

```
client ──▶ nginx ──┬─ /api/auth/* , /api/signup ─▶ dashboard (NextAuth, signup)
                   ├─ /api/*                    ─▶ backend  (auth_request injects X-User-Email)
                   ├─ /webhooks/* , /webhook    ─▶ backend  (third-party webhooks, HMAC)
                   └─ everything else           ─▶ dashboard (UI)
```

Identity: Next.js drops middleware-injected headers through its rewrites, so for every `/api/*`
call nginx makes an `auth_request` to the dashboard's `/api/whoami` (which reads the NextAuth
session) and injects a **trusted** `X-User-Email` before proxying to the backend. Any
client-sent `X-User-Email` is overwritten, so it can't be spoofed.

## HTTP on an IP (default)

Active config: `conf.d/loma.conf` (listens on `:80`, `server_name _`). Works locally and on a
bare IP — no domain needed.

Open inbound **80** in your firewall / security group (and don't expose 3000/3001). Set:

- `.env`: `PUBLIC_BASE_URL=http://<your-ip>`
- `dashboard/.env`: `AUTH_URL=http://<your-ip>`

Then `docker compose up -d` and browse to `http://<your-ip>`.

## HTTPS on :443 (a few steps)

HTTPS is opt-in via the committed `docker-compose.tls.yml` overlay, which renders
`templates/loma-tls.conf.template` for your domain (from `${LOMA_DOMAIN}`). The repo default
stays HTTP, so nothing changes until you set the env vars below.

Prereqs: a DNS **A record** for your domain → the server's (ideally static / Elastic) IP, and
inbound **443** open.

1. **Get the certificate** while the default HTTP stack is running (it already serves the ACME
   challenge on :80, no downtime):

   ```bash
   docker compose run --rm certbot certonly \
     --webroot -w /var/www/certbot \
     -d your.domain.com \
     --email you@example.com --agree-tos --no-eff-email
   ```

   The cert lands in `deploy/nginx/certbot/conf/live/your.domain.com/`.

2. **Enable HTTPS** — in `.env`:

   ```
   LOMA_DOMAIN=your.domain.com
   COMPOSE_FILE=docker-compose.yml:docker-compose.tls.yml
   PUBLIC_BASE_URL=https://your.domain.com
   ```

   and in `dashboard/.env`: `AUTH_URL=https://your.domain.com`.

   *(Only if you use Google/Slack OAuth login: also set `GOOGLE_OAUTH_REDIRECT_URI` /
   `SLACK_OAUTH_REDIRECT_URI` to `https://your.domain.com/...` and re-register them with the
   provider. The default local-credentials login needs no OAuth config.)*

3. **Apply:**

   ```bash
   docker compose up -d
   ```

   nginx now renders the TLS template, serves `:443`, and redirects `:80 → :443`. The
   `auth_request` identity flow and chat/terminal streaming work exactly as on HTTP.
   `X-Forwarded-Proto: https` is set, so backend-built webhook URLs become `https://…`.

4. **Auto-renew** — a host cron that renews and reloads nginx:

   ```cron
   0 3 * * * cd /home/ubuntu/loma && docker compose run --rm certbot renew --webroot -w /var/www/certbot && docker compose exec nginx nginx -s reload
   ```

   (Let's Encrypt certs last 90 days; `renew` is a no-op until ~30 days before expiry.)

### Notes

- `LOMA_DOMAIN` and `COMPOSE_FILE` live in your **untracked** `.env`, and certs in the untracked
  `deploy/nginx/certbot/conf` volume, so HTTPS survives a `git pull` / CI redeploy that runs
  `git reset --hard` — the committed repo stays generic/HTTP-by-default.
- If you change `templates/loma-tls.conf.template` itself, recreate nginx so it re-renders:
  `docker compose up -d --force-recreate nginx`.
