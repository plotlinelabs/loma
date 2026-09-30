# Security Policy

Please report vulnerabilities privately to the repository maintainers. Do not open public issues for exploitable security problems.

Never commit:

- API keys or OAuth secrets
- Slack tokens
- MongoDB connection strings with credentials
- Private company prompts or playbooks
- Customer data or screenshots
- Production deployment secrets

## Device mock proxy (`/device-mock/*`)

`/device-mock/<token>/...` is a public path, reachable only with a per-session token, that forwards
device SDK traffic to an upstream (see [docs/device-mock.md](docs/device-mock.md)). Controls:

- **Not an open proxy.** Only origins in `LOMA_DEVICE_MOCK_UPSTREAMS` (default `api.plotline.so`) are
  allowed. The upstream is fixed when the session is created and checked again on every request.
  The target URL is built from that origin, so path tricks cannot change the host. Redirects go
  only to allowlisted asset hosts, and upstream redirects are not followed.
- **Tokens.** 256-bit random tokens, stored only as SHA-256 hashes, bound to user and conversation,
  with a TTL (default 6 h, max 24 h, at most 10 active sessions per user) and explicit delete.
  Unknown, expired and deleted tokens return 404.
- **Control plane.** `/internal/device-mock/call` is loopback-only and requires the HMAC
  personal-tool token (same as `/internal/devices/*`). Sessions are visible only to their owner in
  the conversation that created them.
- **Header hygiene.** Hop-by-hop headers are stripped, and so are cookies (Loma session),
  `X-User-Email`, `X-Loma-*`, `X-Forwarded-*` and `CF-*`. Upstream `Set-Cookie` is dropped.
  Responses are `no-store`, `nosniff`, and CSP `sandbox`, so upstream bytes cannot run as a page on
  Loma's origin.
- **Limits.** 1 MB request bodies, 32 MB upstream bodies, 60 s upstream timeout, 600 requests/min
  and 64 in-flight requests per session, delays capped at 120 s, scenarios capped at 256 KB.
