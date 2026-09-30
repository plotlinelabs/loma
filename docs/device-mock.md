# Device mock: per-session Plotline API proxy

Some SDK features need a server response that production does not send yet. One example is
`widgetSkeletonSettings` in `/sdk/init`. To test these on a device, point the test app's SDK API
endpoint at a **device-mock session** on Loma's own HTTPS origin. Loma then:

1. forwards each request to an **allowlisted** upstream (default `https://api.plotline.so`),
2. applies the session's **scenario** to the `/init` response (RFC 7396 JSON merge patch),
3. delays or fails matching **widget assets** so loading states stay on screen long enough to see,
4. logs every request with the scenario name and version it got, so a screenshot can be tied to
   the scenario the app actually received.

This replaces ad-hoc `trycloudflare` tunnels, a shared scenario file, and a hand-written proxy.

## Agent usage (`tools/device_mock.py`)

```bash
T="--user-email $E --auth-token $TOKEN --scope $CONVERSATION_ID"
python3 tools/device_mock.py $T presets
python3 tools/device_mock.py $T create --ttl-hours 6          # -> session_id, base_url
python3 tools/device_mock.py $T set-scenario --session-id dm_... --preset shimmer
python3 tools/device_mock.py $T set-scenario --session-id dm_... --preset shimmer --patch-file red.json
python3 tools/device_mock.py $T log --session-id dm_... --limit 20
python3 tools/device_mock.py $T delete --session-id dm_...     # always, when the test is done
```

Set the app's Plotline API endpoint to `base_url` (`https://<loma>/device-mock/dmt_<token>`). The SDK
then calls `https://<loma>/device-mock/dmt_<token>/sdk/init`. After each app launch, run `log` and
check the `/sdk/init` entry: `scenario`, `scenario_version`, and `served` (the values the app got at
each patched path) must match the case you are about to screenshot.

## Presets (`device_mock/presets.json`)

| Preset | `/init` | Widget images (`png/jpg/webp/gif` on allowlisted asset hosts) |
|---|---|---|
| `absent` | `widgetSkeletonSettings` removed (old api-go) | delayed 9 s |
| `disabled` | settings present, `enabled: false` | delayed 9 s |
| `shimmer` | shimmer, default colors | delayed 9 s |
| `lottie` | lottie + product Lottie on widgets with no/default `widgetLoaderUrl` | delayed 9 s |
| `failure` | shimmer | 404 after 4 s |
| `timeout` | shimmer | delayed 25 s (past the SDK's 10 s cap) |

Every preset also empties `data.flows`, so unrelated in-app campaigns do not cover the screen. Each
preset also drops `oldResponseBody` from the `/sdk/init` request, so the SDK gets a full body instead
of a diff. The mechanism is generic. Presets are data, so add a new one to the JSON file.

## Scenario schema

```jsonc
{
  "name": "my-case",
  "init_path": "/sdk/init",                        // glob or "re:<regex>" on the request path
  "request_patch": {"oldResponseBody": null},       // merge patch on the JSON request body (init only)
  "init_patch": {"data": {"widgetSkeletonSettings": {"enabled": true}}},  // or "init_patches": [..]
  "item_patches": [{"path": "data.widgets", "where": {"widgetLoaderUrl": ["", null]},
                    "merge": {"widgetLoaderUrl": "https://cdn.plotline.so/x.lottie"}}],
  "asset_scope": "data.widgets",                   // subtree whose asset URLs are rewritten ("" = all)
  "rules": [
    {"match": "re:(?i)\\.(png|jpe?g)$", "delay_ms": 9000},             // kind "asset" (default)
    {"match": "*/hero.webp", "delay_ms": 2000, "status": 404},
    {"kind": "api", "match": "/sdk/events", "status": 503}               // short-circuits, no upstream call
  ]
}
```

Asset URLs in `asset_scope` that match an asset rule and point to an allowlisted asset host
(`LOMA_DEVICE_MOCK_ASSET_HOSTS`, default `cdn.plotline.so`) are rewritten to
`<base_url>/__loma_asset?v=<version>&u=<url>`. That endpoint applies the **current** rule
(delay, then `status` or a 302 to the original URL). Changing the scenario therefore also
affects URLs the app cached from an earlier `/init`.

## Configuration

| Env | Default | Purpose |
|---|---|---|
| `LOMA_DEVICE_MOCK_UPSTREAMS` | `api.plotline.so` | Comma-separated upstream origins. Bare host means https. The first one is the default. |
| `LOMA_DEVICE_MOCK_ASSET_HOSTS` | `cdn.plotline.so` | Hosts that asset URLs may be rewritten for and redirected to |
| `LOMA_DEVICE_MOCK_BASE_URL` | `PUBLIC_BASE_URL` | Public HTTPS origin used to build `base_url` |
| `LOMA_DEVICE_MOCK_RATE_PER_MINUTE` | `600` | Per-session data-plane request limit |

The `/device-mock/` location must reach the backend without SSO (see `deploy/nginx`). If Loma
sits behind Cloudflare Access or another SSO, add a bypass for `/device-mock/*` like the one for
`/device-runner/*`. Devices cannot log in.

## Storage

Sessions live in Mongo (`device_mock_sessions`), next to the device runner collections. The
reasons:
- Sessions last hours, and the backend restarts on deploy and on dashboard "Restart Service". An
  in-memory session would die in the middle of a test.
- A TTL index cleans up expired sessions.
- The data-plane token is stored only as a SHA-256 hash, the same way runner secrets are.

The scenario and a 200-entry request log (`$push` with `$slice`) are stored on the session document,
so concurrent sessions never share state. Rate-limit counters are process-local.

## Security

See the Device mock section of [SECURITY.md](../SECURITY.md).
