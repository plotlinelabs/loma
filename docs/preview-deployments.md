# PR Preview Deployments

> **For upstream maintainers only.** Previews are an *optional* workflow for a
> project's canonical deployment. Self-hosting Loma needs none of this — a normal
> `docker compose up` / `scripts/deploy.sh` is unaffected (the only shared change
> is env-driven host ports that default to today's values).

Each pull request can get its own throwaway, full-stack environment at
`https://pr-<N>.preview.<domain>` for testing before merge.

## How it works

- A maintainer adds the **`preview`** label to a PR.
- [`.github/workflows/preview.yml`](../.github/workflows/preview.yml) SSHes to the
  **preview box** and runs [`scripts/preview_up.sh`](../scripts/preview_up.sh),
  which brings up an isolated Docker Compose stack for that PR.
- The preview URL is posted to the **internal Slack channel** (never to the PR or
  CI logs). The PR gets a comment that just says a preview exists.
- Closing the PR (or removing the label) runs
  [`scripts/preview_down.sh`](../scripts/preview_down.sh) to tear everything down.

## Isolation (parallel previews)

Each PR's stack is fully isolated, so many can run at once:

| Resource        | Per-PR value                                   |
| --------------- | ---------------------------------------------- |
| Compose project | `COMPOSE_PROJECT_NAME=loma-pr-<N>` (prefixes containers, network, volumes) |
| Host ports      | `20000 + N*10` → backend / dashboard / nginx (loopback only) |
| Database        | `OBSERVABILITY_DB_NAME=loma_pr_<N>` (own DB in the shared cluster) |
| Auth            | fresh per-PR `AUTH_SECRET`; app login = local (behind SSO) |

A single front-proxy nginx on the box routes `pr-<N>.preview.<domain>` →
that stack's loopback nginx port.

## Access control

`*.preview.<domain>` sits behind **Cloudflare Access** (Google SSO, restricted to
your organization's email domain). A leaked URL is useless to anyone outside the
org, so the URL never needs to be secret.

## Gating (who can trigger)

1. **Same-repo only** — fork PRs are skipped, so live credentials never run
   fork-authored code.
2. **Maintainer label** — the workflow only acts on the `preview` label.
3. **Allowlist** — the actor that triggers a deploy (applying the label *or*
   pushing new commits to a labeled PR) must be in the `PREVIEW_MAINTAINERS` repo
   variable (space-separated GitHub usernames). A non-allowlisted push won't
   auto-redeploy; a maintainer re-applies the label to refresh.
4. *(Optional)* protect the `preview` GitHub Environment with **required
   reviewers** for an extra human gate.

## Safety with live integrations

Previews use real integration secrets (from `~/preview-secrets.env` on the box),
so they run with two guards to avoid disturbing production:

- `LOMA_ENABLE_SCHEDULER=false` — preview scheduled flows don't double-fire.
- `LOMA_ENABLE_SLACK=false` — the Slack Socket Mode consumer is off, so the
  preview doesn't double-reply to the production Slack app's events. (For live
  Slack testing, put a **separate** Slack app's `SLACK_APP_TOKEN` in the preview
  secrets and set `LOMA_ENABLE_SLACK=true`.)

## One-time setup (preview box + GitHub)

**Preview EC2**
- Install Docker + Compose; create the deploy user + SSH key.
- Place `~/preview-secrets.env` (live integration creds + `OBSERVABILITY_MONGODB_URI`
  + `LOMA_SETUP_TOKEN`); keep it untracked, like prod's `.env`.
- Run a persistent **front-proxy** nginx container (default name `preview-proxy`)
  that mounts `~/preview-proxy/conf.d` and a wildcard **Cloudflare Origin
  Certificate** at `/etc/nginx/certs/origin.{pem,key}`, and publishes `:443`.

**Cloudflare**
- Wildcard DNS `*.preview.<domain>` (proxied) → preview EC2.
- Cloudflare Access app over `*.preview.<domain>`: allow only your org's email
  domain (Google IdP). Origin TLS = Full (strict).

**GitHub**
- Secrets: `PREVIEW_SSH_HOST`, `PREVIEW_SSH_USER`, `PREVIEW_SSH_KEY`,
  `PREVIEW_BASE_DOMAIN`, `SLACK_WEBHOOK_URL`.
- Variable: `PREVIEW_MAINTAINERS` (space-separated usernames).
- Create the `preview` label (and optionally the `preview` Environment).

## Environment knobs (scripts)

`preview_up.sh` / `preview_down.sh` read these (sensible defaults shown):

| Var                   | Default                    |
| --------------------- | -------------------------- |
| `PREVIEW_BASE_DOMAIN` | *(required)*               |
| `PREVIEW_SECRETS_FILE`| `~/preview-secrets.env`    |
| `PREVIEW_ROOT`        | `~/previews`               |
| `PREVIEW_VHOST_DIR`   | `~/preview-proxy/conf.d`   |
| `PREVIEW_FRONT_PROXY` | `preview-proxy` (container)|
