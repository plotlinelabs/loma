# Device mock: per-session Plotline API proxy

Device tests often need a backend response you cannot get from production on demand: a field the
server does not send yet, an empty or failing `/sdk/init`, slow or broken images, an outage. To get
one, point the test app's SDK API endpoint at a **device-mock session** on Loma's own HTTPS origin.
Loma then:

1. forwards each request to an **allowlisted** upstream (default `https://api.plotline.so`),
2. applies the session's **scenario** to the `/init` response (RFC 7396 JSON merge patch),
3. delays or fails matching **assets** (images etc. referenced by `/init`) and API paths so loading states stay on screen long enough to see,
4. logs every request with the scenario name and version it got, so a screenshot can be tied to
   the scenario the app actually received.

This replaces ad-hoc `trycloudflare` tunnels, a shared scenario file, and a hand-written proxy.

## Agent usage (`tools/device_mock.py`)

```bash
T="--user-email $E --auth-token $TOKEN --scope $CONVERSATION_ID"
python3 tools/device_mock.py $T presets
python3 tools/device_mock.py $T create --ttl-hours 6          # -> session_id, base_url
python3 tools/device_mock.py $T set-scenario --session-id dm_... --preset slow_images
python3 tools/device_mock.py $T set-scenario --session-id dm_... --preset no_flows --patch-file feature_on.json
python3 tools/device_mock.py $T set-scenario --session-id dm_... --scenario-file case.json --name case-3
python3 tools/device_mock.py $T show --session-id dm_...                  # current scenario in full
python3 tools/device_mock.py $T log --session-id dm_... --path /sdk/init --limit 5
python3 tools/device_mock.py $T log --session-id dm_... --status 4xx --since <latest_at of last call>
python3 tools/device_mock.py $T delete --session-id dm_...     # always, when the test is done
```

Set the app's Plotline API endpoint to `base_url` (`https://<loma>/device-mock/dmt_<token>`). The SDK
then calls `https://<loma>/device-mock/dmt_<token>/sdk/init`. After each app launch, run `log` and
check the `/sdk/init` entry: `scenario`, `scenario_version`, and `served` (the values the app got at
each patched path) must match the case you are about to screenshot.

`set-scenario` can be called any time; the same `base_url` serves the new scenario from the next
request on (relaunch the app if it only calls `/init` at start). `--preset passthrough` turns
mocking off without deleting the session.

`log` filters (all optional, combined with AND): `--path` (glob or `re:<regex>`), `--method`,
`--status` (`404` or `4xx`), `--applied` (`init`, `asset_rule`, `api_rule`, `blocked`,
`init_parse_error`, `none`), `--scenario`, `--scenario-version`, `--since` (ISO time; pass the
`latest_at` from the previous call to see only new requests). The response has `total`, `matched`,
`latest_at` and the last `--limit` matching entries. The log keeps the last 200 requests.

## Presets (`device_mock/presets.json`)

| Preset | Effect |
|---|---|
| `passthrough` | Nothing changed, everything logged. First check that the app reaches the mock |
| `no_flows` | `/init` `data.flows` emptied, so live in-app campaigns do not cover the screen |
| `no_widgets` | `/init` `data.widgets` emptied |
| `slow_images` | png/jpg/webp/gif URLs in `/init` delayed 9 s (loading states) |
| `failing_images` | those images return 404 after 2 s (error / fallback states) |
| `hanging_images` | those images delayed 25 s (timeouts, loader caps) |
| `slow_init` | `/sdk/init` delayed 8 s, then the real response |
| `init_error` | `/sdk/init` returns 500 without calling the upstream |
| `api_down` | every API call returns 503 after 1 s |

Every preset drops `oldResponseBody` from the `/sdk/init` request, so the SDK gets a full body
instead of a diff and patches always apply. `presets --verbose` prints the full scenarios.

Presets are data. Add generic ones to `device_mock/presets.json`; put team or feature presets in
their own JSON file (same `{"base": ..., "presets": ...}` shape) and list it in
`LOMA_DEVICE_MOCK_PRESETS`. A one-off case belongs in a `--scenario-file` or `--patch-file`, which
can be layered on a preset. Never put customer ids, keys or customer URLs in a preset.

## Scenario schema

```jsonc
{
  "name": "my-case",
  "init_path": "/sdk/init",                        // glob or "re:<regex>" on the request path
  "request_patch": {"oldResponseBody": null},       // merge patch on the JSON request body (init only)
  "init_patch": {"data": {"featureX": {"enabled": true}}},   // or "init_patches": [..]; null deletes a key
  "item_patches": [{"path": "data.widgets", "where": {"type": ["banner"]},   // patch matching list items
                    "merge": {"title": "A very long title to test truncation"}}],
  "asset_scope": "",                               // subtree whose asset URLs are rewritten ("" = whole body)
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
| `LOMA_DEVICE_MOCK_PRESETS` | (none) | Extra preset JSON files (`:` separated), loaded after the built-ins |

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
