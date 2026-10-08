"""Pure logic for device-mock sessions: scenarios, JSON merge patch, rules, header filtering.

No I/O here, so every piece is unit-tested directly. See docs/device-mock.md.
"""
import copy
import fnmatch
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

PRESETS_FILE = Path(__file__).resolve().parent / 'presets.json'

ASSET_PATH = '__loma_asset'  # reserved data-plane path: /device-mock/<token>/__loma_asset?u=<url>
MAX_SCENARIO_BYTES = 256 * 1024
MAX_RULES = 50
MAX_DELAY_MS = 120_000
MAX_PATTERN = 200
MAX_INIT_PATCHES = 10

# RFC 7230 hop-by-hop headers plus headers that must be recomputed per hop.
HOP_BY_HOP = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
    'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length', 'content-encoding',
})
# Loma / edge identity never goes upstream: cookies (Loma session), nginx-injected identity,
# client address metadata, Cloudflare Access headers.
_DROP_REQUEST = frozenset({'cookie', 'accept-encoding', 'x-user-email', 'x-real-ip', 'forwarded', 'via'})
_DROP_REQUEST_PREFIXES = ('x-forwarded-', 'x-loma-', 'cf-')
# Upstream cookies would land on Loma's own origin.
_DROP_RESPONSE = frozenset({'set-cookie', 'set-cookie2', 'strict-transport-security', 'alt-svc'})


def upstream_allowlist():
    """Allowed upstream origins, from LOMA_DEVICE_MOCK_UPSTREAMS (comma separated).

    Bare hosts mean https. Default: https://api.plotline.so.
    """
    raw = os.environ.get('LOMA_DEVICE_MOCK_UPSTREAMS', '').strip() or 'api.plotline.so'
    origins = []
    for item in raw.split(','):
        origin = normalize_origin(item)
        if origin and origin not in origins:
            origins.append(origin)
    return origins


def asset_host_allowlist():
    """Hosts that rewritten asset URLs may redirect to (LOMA_DEVICE_MOCK_ASSET_HOSTS)."""
    raw = os.environ.get('LOMA_DEVICE_MOCK_ASSET_HOSTS', '').strip() or 'cdn.plotline.so'
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def normalize_origin(value):
    value = str(value or '').strip().rstrip('/')
    if not value:
        return None
    if '://' not in value:
        value = 'https://' + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query \
            or parsed.username or parsed.password:
        return None
    return f'{parsed.scheme}://{parsed.netloc.lower()}'


# ── JSON merge patch (RFC 7396) ───────────────────────────────────────────


def merge_patch(target, patch):
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def deep_merge(base, override):
    """Compose preset definitions: like merge patch, but None is kept as a value."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = deep_merge(result.get(key), value) if isinstance(value, dict) else copy.deepcopy(value)
    return result


# ── Matching ──────────────────────────────────────────────────────────────


def _compile(pattern):
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
        raise ValueError(f'match must be a non-empty string of at most {MAX_PATTERN} chars')
    if pattern.startswith('re:'):
        try:
            return re.compile(pattern[3:])
        except re.error as exc:
            raise ValueError(f'invalid regex {pattern!r}: {exc}') from None
    return re.compile(fnmatch.translate(pattern))


def matches(pattern, value):
    """Glob (fnmatch, `*` also crosses `/`) or `re:<regex>` (search)."""
    compiled = _compile(pattern)
    return bool(compiled.search(value) if pattern.startswith('re:') else compiled.match(value))


def first_rule(rules, kind, value):
    for rule in rules or []:
        if rule.get('kind', 'asset') == kind and matches(rule['match'], value):
            return rule
    return None


# ── Scenario validation ───────────────────────────────────────────────────


def preset_files():
    """Built-in presets plus any extra JSON files in LOMA_DEVICE_MOCK_PRESETS (os.pathsep separated)."""
    extra = [Path(p.strip()).expanduser() for p in os.environ.get('LOMA_DEVICE_MOCK_PRESETS', '').split(os.pathsep)
             if p.strip()]
    return [PRESETS_FILE, *extra]


def load_presets():
    """{name: scenario}. Later files override earlier ones; each file's `base` applies to its own presets."""
    presets = {}
    for path in preset_files():
        with open(path) as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get('presets'), dict):
            raise ValueError(f'{path}: expected {{"base": {{...}}, "presets": {{name: scenario}}}}')
        base = data.get('base') or {}
        for name, spec in data['presets'].items():
            try:
                presets[name] = validate_scenario(deep_merge(base, {'name': name, **spec}))
            except ValueError as exc:
                raise ValueError(f'{path}: preset {name!r}: {exc}') from None
    return presets


def _rule(raw):
    if not isinstance(raw, dict):
        raise ValueError('each rule must be an object')
    unknown = set(raw) - {'match', 'kind', 'delay_ms', 'status'}
    if unknown:
        raise ValueError(f'unknown rule keys: {sorted(unknown)}')
    kind = raw.get('kind', 'asset')
    if kind not in ('asset', 'api'):
        raise ValueError('rule kind must be "asset" or "api"')
    _compile(raw.get('match'))
    delay = raw.get('delay_ms', 0)
    if not isinstance(delay, int) or isinstance(delay, bool) or not 0 <= delay <= MAX_DELAY_MS:
        raise ValueError(f'delay_ms must be an integer 0..{MAX_DELAY_MS}')
    status = raw.get('status')
    if status is not None and (not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 599):
        raise ValueError('status must be an HTTP status 200..599')
    return {'match': raw['match'], 'kind': kind, 'delay_ms': delay, 'status': status}


def _item_patch(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('path'), str) or not isinstance(raw.get('merge'), dict):
        raise ValueError('item_patches entries need "path" (dotted) and "merge" (object)')
    where = raw.get('where') or {}
    if not isinstance(where, dict) or not all(isinstance(v, list) for v in where.values()):
        raise ValueError('item_patches "where" maps a field to a list of allowed values')
    return {'path': raw['path'], 'where': where, 'merge': raw['merge']}


def validate_scenario(raw):
    """Normalise a scenario dict or raise ValueError. See docs/device-mock.md for the schema."""
    if not isinstance(raw, dict):
        raise ValueError('scenario must be a JSON object')
    if len(json.dumps(raw)) > MAX_SCENARIO_BYTES:
        raise ValueError('scenario is larger than 256 KB')
    unknown = set(raw) - {'name', 'description', 'init_path', 'request_patch', 'init_patch', 'init_patches',
                          'item_patches', 'asset_scope', 'rules'}
    if unknown:
        raise ValueError(f'unknown scenario keys: {sorted(unknown)}')
    name = str(raw.get('name') or 'custom')[:64]
    init_path = raw.get('init_path', '/sdk/init')
    _compile(init_path)
    patches = list(raw.get('init_patches') or [])
    if raw.get('init_patch') is not None:
        patches.append(raw['init_patch'])
    if len(patches) > MAX_INIT_PATCHES or not all(isinstance(p, dict) for p in patches):
        raise ValueError(f'init_patch(es) must be up to {MAX_INIT_PATCHES} JSON objects')
    request_patch = raw.get('request_patch')
    if request_patch is not None and not isinstance(request_patch, dict):
        raise ValueError('request_patch must be a JSON object')
    rules = raw.get('rules') or []
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ValueError(f'rules must be a list of at most {MAX_RULES}')
    items = raw.get('item_patches') or []
    if not isinstance(items, list) or len(items) > MAX_RULES:
        raise ValueError('item_patches must be a list')
    scope = raw.get('asset_scope', '')
    if not isinstance(scope, str):
        raise ValueError('asset_scope must be a dotted path string ("" = whole body)')
    return {'name': name, 'description': str(raw.get('description') or '')[:300], 'init_path': init_path,
            'request_patch': request_patch, 'init_patches': patches,
            'item_patches': [_item_patch(i) for i in items], 'asset_scope': scope,
            'rules': [_rule(r) for r in rules]}


def compose_scenario(presets, preset=None, scenario=None, init_patch=None, name=None):
    """Preset (optional) + full-scenario overrides + one extra init merge patch."""
    if preset is not None and preset not in presets:
        raise ValueError(f'unknown preset {preset!r}; available: {", ".join(sorted(presets))}')
    result = copy.deepcopy(presets[preset]) if preset else {'name': 'custom'}
    if scenario is not None:
        if not isinstance(scenario, dict):
            raise ValueError('scenario must be a JSON object')
        result = deep_merge(result, scenario)
    if init_patch is not None:
        if not isinstance(init_patch, dict):
            raise ValueError('patch must be a JSON object (RFC 7396 merge patch)')
        result['init_patches'] = list(result.get('init_patches') or []) + [init_patch]
        result.pop('init_patch', None)
    if name:
        result['name'] = name
    return validate_scenario(result)


# ── Request log queries ──────────────────────────────────────────────────

LOG_FILTERS = ('path', 'method', 'status', 'applied', 'scenario', 'scenario_version', 'since')


def _status_matches(want, status):
    want = str(want).strip().lower()
    if len(want) == 3 and want.endswith('xx') and want[0].isdigit():
        return isinstance(status, int) and status // 100 == int(want[0])
    try:
        return status == int(want)
    except ValueError:
        raise ValueError('status must be an HTTP status (e.g. 404) or a class (e.g. 4xx)') from None


def filter_log(entries, query):
    """Entries matching every filter in `query` (oldest first). Raises ValueError on bad filters.

    path: glob or re:<regex> on the logged path (asset requests are logged as `/__loma_asset`,
    with the original URL in `asset`). method: GET/POST... status: 404 or a class like 4xx.
    applied: init | asset_rule | api_rule | blocked | init_parse_error | none.
    since: ISO timestamp; only entries strictly after it (pass the previous `latest_at` to poll).
    """
    query = {k: v for k, v in (query or {}).items() if k in LOG_FILTERS and v not in (None, '')}
    if 'path' in query:
        _compile(query['path'])
    if 'scenario_version' in query:
        try:
            query['scenario_version'] = int(query['scenario_version'])
        except (TypeError, ValueError):
            raise ValueError('scenario_version must be an integer') from None
    if 'status' in query:
        _status_matches(query['status'], 200)  # validate once
    if 'since' in query:
        try:
            since = datetime.fromisoformat(str(query['since']).replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('since must be an ISO 8601 timestamp (use latest_at from a previous log call)') from None
        query['since'] = (since if since.tzinfo else since.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    out = []
    for entry in entries or []:
        if 'path' in query and not matches(query['path'], str(entry.get('path') or '')):
            continue
        if 'method' in query and str(entry.get('method') or '').upper() != str(query['method']).upper():
            continue
        if 'status' in query and not _status_matches(query['status'], entry.get('status')):
            continue
        if 'applied' in query:
            want = None if str(query['applied']).lower() == 'none' else str(query['applied'])
            if entry.get('applied') != want:
                continue
        if 'scenario' in query and entry.get('scenario') != query['scenario']:
            continue
        if 'scenario_version' in query and entry.get('scenario_version') != query['scenario_version']:
            continue
        if 'since' in query and not str(entry.get('at') or '') > str(query['since']):
            continue
        out.append(entry)
    return out


# ── Applying a scenario to an /init response ──────────────────────────────


def _get_path(node, dotted):
    for part in [p for p in dotted.split('.') if p]:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set_path(root, dotted, value):
    parts = [p for p in dotted.split('.') if p]
    node = root
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return root
    if isinstance(node, dict) and parts[-1] in node:
        node[parts[-1]] = value
    return root


def rewrite_assets(node, rules, asset_hosts, asset_base):
    """Point matching asset URLs at the session's asset endpoint so delay/status rules apply."""
    if isinstance(node, dict):
        return {k: rewrite_assets(v, rules, asset_hosts, asset_base) for k, v in node.items()}
    if isinstance(node, list):
        return [rewrite_assets(v, rules, asset_hosts, asset_base) for v in node]
    if isinstance(node, str) and node.startswith('https://') and len(node) <= 2048:
        host = (urllib.parse.urlsplit(node).hostname or '').lower()
        if host in asset_hosts and first_rule(rules, 'asset', node):
            return asset_base + urllib.parse.quote(node, safe='')
    return node


def apply_init(body, scenario, asset_hosts, asset_base):
    """Return the patched /init JSON body for a scenario (does not mutate `body`)."""
    result = copy.deepcopy(body)
    for patch in scenario.get('init_patches') or []:
        result = merge_patch(result, patch)
    for item in scenario.get('item_patches') or []:
        items = _get_path(result, item['path'])
        if isinstance(items, list):
            for i, entry in enumerate(items):
                if isinstance(entry, dict) and all(entry.get(k) in allowed for k, allowed in item['where'].items()):
                    items[i] = merge_patch(entry, item['merge'])
    if any(r.get('kind', 'asset') == 'asset' for r in scenario.get('rules') or []):
        scope = scenario.get('asset_scope', '')
        target = _get_path(result, scope) if scope else result
        if target is not None:
            rewritten = rewrite_assets(target, scenario['rules'], asset_hosts, asset_base)
            result = _set_path(result, scope, rewritten) if scope else rewritten
    return result


# ── Header filtering ──────────────────────────────────────────────────────


def _connection_tokens(headers):
    if not hasattr(headers, 'getall'):
        return set()
    return {t.strip().lower() for v in headers.getall('Connection', []) for t in v.split(',')}


def request_headers(headers):
    """Headers to forward upstream: drop hop-by-hop, Loma cookies/identity, and edge metadata."""
    extra = _connection_tokens(headers)
    out = {}
    for key, value in headers.items():
        low = key.lower()
        if low in HOP_BY_HOP or low in _DROP_REQUEST or low in extra or low.startswith(_DROP_REQUEST_PREFIXES):
            continue
        out[key] = value
    return out


def response_headers(headers):
    extra = _connection_tokens(headers)
    return [(k, v) for k, v in headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() not in _DROP_RESPONSE and k.lower() not in extra]
