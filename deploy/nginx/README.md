# Reverse proxy (nginx)

The `nginx` service in `docker-compose.yml` is the single external entrypoint. It serves the
dashboard and routes traffic over the internal Docker network, so the backend (`3000`) and
dashboard (`3001`) containers are bound to `127.0.0.1` only and never need to be exposed.

```
client ──▶ :80 nginx ──┬─ /api/auth/*          ─▶ dashboard (NextAuth)
                       ├─ /api/*               ─▶ backend  (API, OAuth cb, chat SSE, terminal WS)
                       ├─ /webhooks/* , /webhook ─▶ backend  (third-party webhooks)
                       └─ everything else      ─▶ dashboard (UI)
```

## Phase 1 — HTTP on an IP (default)

Active config: `conf.d/loma.conf` (listens on `:80`, `server_name _`).

Open inbound **80** in your firewall / cloud security group and **close 3000/3001**. Set, in
the relevant env files:

- `.env`: `PUBLIC_BASE_URL=http://<your-ip>`
- `dashboard/.env`: `AUTH_URL=http://<your-ip>`

Then `docker compose up -d` and browse to `http://<your-ip>`.

## Phase 2 — Domain + HTTPS on :443

Prereqs: a DNS A record pointing your domain at the server, and inbound **443** open.

1. **Serve the ACME challenge over HTTP.** The active `loma.conf` already serves
   `/.well-known/acme-challenge/` from `/var/www/certbot`, so just make sure the stack is up.

2. **Obtain the certificate** (webroot challenge — no downtime):

   ```bash
   docker compose run --rm certbot certonly \
     --webroot -w /var/www/certbot \
     -d your.domain.com \
     --email you@example.com --agree-tos --no-eff-email
   ```

   The cert lands in `deploy/nginx/certbot/conf/live/your.domain.com/` (mounted into nginx at
   `/etc/letsencrypt`).

3. **Switch nginx to HTTPS:**
   - In `docker-compose.yml`, add `"443:443"` to the `nginx` service `ports`.
   - Copy `conf.d/loma-ssl.conf.example` → `conf.d/loma.conf`, replacing `YOUR_DOMAIN` with
     your domain (this replaces the HTTP-only server with the redirect + TLS server).
   - `docker compose up -d` (recreates nginx with the new port + config).

4. **Update the app's external URL** (and re-register OAuth redirect URIs with the providers):
   - `.env`: `PUBLIC_BASE_URL=https://your.domain.com`,
     `GOOGLE_OAUTH_REDIRECT_URI=https://your.domain.com/api/oauth/google/callback`,
     `SLACK_OAUTH_REDIRECT_URI=https://your.domain.com/api/oauth/slack/callback`
   - `dashboard/.env`: `AUTH_URL=https://your.domain.com`
   - Recreate so the values are picked up: `docker compose up -d --force-recreate loma-backend loma-dashboard`.

   nginx forwards `X-Forwarded-Proto: https`, so backend-built webhook URLs become `https://…`
   automatically.

5. **Auto-renew.** Run a periodic renewal that reloads nginx on success — e.g. a host cron:

   ```cron
   0 3 * * * cd /home/ubuntu/loma && docker compose run --rm certbot renew --webroot -w /var/www/certbot && docker compose exec nginx nginx -s reload
   ```

   (Let's Encrypt certs last 90 days; renewal is a no-op until ~30 days before expiry.)
