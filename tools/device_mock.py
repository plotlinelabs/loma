"""Device-mock personal tool: a per-session Plotline API proxy on Loma's own HTTPS origin.

Point a test app's SDK API endpoint at the session's base_url; Loma forwards to an allowlisted
upstream (default https://api.plotline.so), applies the session's scenario to the /init response
(JSON merge patch) and delays or fails matching assets (images, files). Every request is logged per
session, so you can prove which scenario the app actually received. Replaces ad-hoc tunnels.

Talks to the local Loma backend (loopback only) with your personal auth token. Sessions belong
to you AND this conversation (--scope), expire after --ttl-hours (default 6), and should be
deleted when the test is done.

Commands:
  device_mock.py --user-email E --auth-token T --scope CONVERSATION_ID presets [--verbose]
  device_mock.py ... create [--upstream https://api.plotline.so] [--ttl-hours 6] [--label L]
  device_mock.py ... list
  device_mock.py ... show --session-id ID                  (current scenario in full)
  device_mock.py ... set-scenario --session-id ID (--preset NAME | --patch-file F | --scenario-file F) [--name N]
                     (--preset may be combined with --patch-file / --scenario-file to override it;
                     switch any time mid-session, `--preset passthrough` turns mocking off)
  device_mock.py ... log --session-id ID [--limit 50] [--path GLOB|re:RX] [--method M] [--status 404|4xx]
                     [--applied init|asset_rule|api_rule|blocked|none] [--scenario NAME]
                     [--scenario-version N] [--since ISO]   (pass the previous latest_at to poll)
  device_mock.py ... delete --session-id ID
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


LOG_FILTERS = ('path', 'method', 'status', 'applied', 'scenario', 'scenario_version', 'since')


def _base_url():
    return f"http://127.0.0.1:{int(os.environ.get('WEBHOOK_PORT', '3000'))}"


def _request(headers, body, timeout=60):
    request = urllib.request.Request(_base_url() + '/internal/device-mock/call', data=json.dumps(body).encode(),
                                     method='POST', headers={**headers, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except ValueError:
            return {'error': f'HTTP {exc.code}'}
    except urllib.error.URLError as exc:
        return {'error': f'Loma backend unreachable at {_base_url()}: {exc.reason}'}


def _load_json(path, what):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Could not read {what} {path}: {exc}')
    if not isinstance(data, dict):
        raise SystemExit(f'{what} must be a JSON object')
    return data


def build_body(args):
    """Translate CLI arguments into the /internal/device-mock/call body (pure; unit-tested)."""
    body = {'scope': args.scope if ':' in args.scope else f'conv:{args.scope}'}
    if args.command == 'presets':
        return {**body, 'action': 'presets', **({'verbose': True} if args.verbose else {})}
    if args.command == 'list':
        return {**body, 'action': 'list'}
    if args.command == 'create':
        return {**body, 'action': 'create', 'upstream': args.upstream, 'ttl_hours': args.ttl_hours,
                'label': args.label}
    body = {**body, 'session_id': args.session_id}
    if args.command == 'set-scenario':
        if not (args.preset or args.patch_file or args.scenario_file):
            raise SystemExit('set-scenario needs --preset, --patch-file or --scenario-file')
        body.update(action='set_scenario', preset=args.preset, name=args.name)
        if args.scenario_file:
            body['scenario'] = _load_json(args.scenario_file, 'scenario file')
        if args.patch_file:
            body['init_patch'] = _load_json(args.patch_file, 'patch file')
            if not args.name and not args.preset:
                body['name'] = os.path.splitext(os.path.basename(args.patch_file))[0][:64]
        return body
    if args.command == 'show':
        return {**body, 'action': 'show'}
    if args.command == 'log':
        filters = {key: getattr(args, key) for key in LOG_FILTERS if getattr(args, key) is not None}
        return {**body, 'action': 'log', 'limit': args.limit, **({'filters': filters} if filters else {})}
    if args.command == 'delete':
        return {**body, 'action': 'delete'}
    raise SystemExit('Unknown command')


def parser():
    p = argparse.ArgumentParser(description='Per-session mock Plotline API proxy for device tests')
    p.add_argument('--user-email', required=True)
    p.add_argument('--auth-token', required=True)
    p.add_argument('--scope', required=True, help='This conversation id; sessions are per chat')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('presets').add_argument('--verbose', action='store_true', help='Print full preset scenarios')
    sub.add_parser('list')
    s = sub.add_parser('create')
    s.add_argument('--upstream', help='Allowlisted upstream origin (default: first in LOMA_DEVICE_MOCK_UPSTREAMS)')
    s.add_argument('--ttl-hours', type=float, default=6)
    s.add_argument('--label')

    def with_session(name):
        cmd = sub.add_parser(name)
        cmd.add_argument('--session-id', required=True)
        return cmd

    s = with_session('set-scenario')
    s.add_argument('--preset')
    s.add_argument('--patch-file', help='JSON merge patch (RFC 7396) applied to the /init response body')
    s.add_argument('--scenario-file', help='Full scenario JSON (see docs/device-mock.md)')
    s.add_argument('--name')
    s = with_session('log')
    s.add_argument('--limit', type=int, default=50)
    s.add_argument('--path', help='Glob or re:<regex> on the request path, e.g. /sdk/init')
    s.add_argument('--method')
    s.add_argument('--status', help='Exact status (404) or class (4xx)')
    s.add_argument('--applied', help='init | asset_rule | api_rule | blocked | init_parse_error | none')
    s.add_argument('--scenario', help='Scenario name in effect when the request was served')
    s.add_argument('--scenario-version', type=int)
    s.add_argument('--since', help='ISO timestamp; only newer entries (use latest_at from the last call)')
    with_session('show')
    with_session('delete')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    body = build_body(args)
    result = _request({'X-Loma-User': args.user_email, 'X-Loma-Auth-Token': args.auth_token}, body)
    print(json.dumps(result, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    sys.exit(main())
