"""Device personal tool: drive Android emulators / iOS simulators on your Loma Device Runners.

Talks to the local Loma backend (loopback only) which enforces ownership,
leases, validation and audit. Devices come from runners enrolled in Loma →
Devices. --scope <conversation-id> is required so a lease belongs to one chat.
In this legacy runtime the scope is advisory between your own chats (other
users are always isolated); isolated workers bind the scope server-side.

Commands:
  device.py --user-email E --auth-token T --scope CONVERSATION_ID list
  device.py ... lease [--platform android|ios] [--device-id ID]
  device.py ... release --device-id ID
  device.py ... install --device-id ID (--repo OWNER/NAME --artifact-name NAME [--pr N | --run-id N] | --file PATH) [--app-id PKG]
  device.py ... app --device-id ID --action launch|stop|reset_app|uninstall --app-id PKG
  device.py ... open-url --device-id ID --url URL
  device.py ... tap --device-id ID --x X --y Y
  device.py ... swipe --device-id ID --x1 . --y1 . --x2 . --y2 . [--duration-ms MS]
  device.py ... type --device-id ID --text TEXT
  device.py ... key --device-id ID --key back|home|enter|...
  device.py ... ui-tree --device-id ID
  device.py ... screenshot --device-id ID [--out /tmp/shot.png]     (then Read the PNG to view it)
  device.py ... logs --device-id ID [--lines N] [--filter TEXT] [--clear]
  device.py ... run-flow --device-id ID --flow-file flow.yaml
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _base_url():
    return f"http://127.0.0.1:{int(os.environ.get('WEBHOOK_PORT', '3000'))}"


def _request(path, headers, body=None, data=None, timeout=1800):
    payload = data if data is not None else json.dumps(body).encode()
    request = urllib.request.Request(_base_url() + path, data=payload, method='POST', headers={
        **headers, 'Content-Type': 'application/octet-stream' if data is not None else 'application/json'})
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
    except (TimeoutError, OSError) as exc:
        return {'error': f'No response from Loma within {timeout}s ({type(exc).__name__}); '
                         'the operation may still finish, check with ui-tree'}


def build_body(args):
    """Translate CLI arguments into the /internal/devices/call body (pure; unit-tested)."""
    # Same lease scope as isolated runs (conv:<id>), so both runtimes agree.
    body = {'scope': args.scope if ':' in args.scope else f'conv:{args.scope}'}
    if args.command == 'list':
        return {**body, 'action': 'list'}
    if args.command == 'lease':
        return {**body, 'action': 'lease', 'device_id': args.device_id, 'platform': args.platform}
    if args.command == 'release':
        return {**body, 'action': 'release', 'device_id': args.device_id}
    call = {**body, 'action': 'call', 'device_id': args.device_id}
    if args.command == 'install':
        call_args = {}
        if args.app_id:
            call_args['app_id'] = args.app_id
        if args.file:
            if args.repo or args.artifact_name or args.pr or args.run_id:
                raise SystemExit('install takes either --file or --repo/--artifact-name, not both')
            call_args['upload_id'] = getattr(args, 'upload_id', None) or 'pending-upload'
        else:
            if not (args.repo and args.artifact_name):
                raise SystemExit('install needs --file, or --repo and --artifact-name')
            build = {'repo': args.repo, 'artifact_name': args.artifact_name}
            if args.pr:
                build['pr'] = args.pr
            if args.run_id:
                build['run_id'] = args.run_id
            call_args['build'] = build
        return {**call, 'op': 'install', 'args': call_args}
    if args.command == 'app':
        return {**call, 'op': args.action, 'args': {'app_id': args.app_id}}
    if args.command == 'open-url':
        return {**call, 'op': 'open_url', 'args': {'url': args.url}}
    if args.command == 'tap':
        return {**call, 'op': 'tap', 'args': {'x': args.x, 'y': args.y}}
    if args.command == 'swipe':
        return {**call, 'op': 'swipe', 'args': {'x1': args.x1, 'y1': args.y1, 'x2': args.x2, 'y2': args.y2,
                                               'duration_ms': args.duration_ms}}
    if args.command == 'type':
        return {**call, 'op': 'type', 'args': {'text': args.text}}
    if args.command == 'key':
        return {**call, 'op': 'key', 'args': {'key': args.key}}
    if args.command == 'ui-tree':
        return {**call, 'op': 'ui_tree', 'args': {}}
    if args.command == 'screenshot':
        return {**call, 'op': 'screenshot', 'args': {}}
    if args.command == 'logs':
        log_args = {'lines': args.lines, 'clear': args.clear}
        if args.filter:
            log_args['filter'] = args.filter
        return {**call, 'op': 'logs', 'args': log_args}
    if args.command == 'run-flow':
        with open(args.flow_file) as handle:
            return {**call, 'op': 'run_flow', 'args': {'flow': handle.read()}}
    raise SystemExit('Unknown command')


def parser():
    p = argparse.ArgumentParser(description='Drive devices on your Loma Device Runners')
    p.add_argument('--user-email', required=True)
    p.add_argument('--auth-token', required=True)
    p.add_argument('--scope', required=True,
                   help='Lease scope: pass this conversation id, so leases are per chat')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    s = sub.add_parser('lease')
    s.add_argument('--platform', choices=['android', 'ios'])
    s.add_argument('--device-id')

    def with_device(name):
        cmd = sub.add_parser(name)
        cmd.add_argument('--device-id', required=True)
        return cmd

    with_device('release')
    s = with_device('install')
    s.add_argument('--repo')
    s.add_argument('--artifact-name')
    s.add_argument('--pr', type=int)
    s.add_argument('--run-id', type=int)
    s.add_argument('--file')
    s.add_argument('--app-id')
    s = with_device('app')
    s.add_argument('--action', required=True, choices=['launch', 'stop', 'reset_app', 'uninstall'])
    s.add_argument('--app-id', required=True)
    with_device('open-url').add_argument('--url', required=True)
    s = with_device('tap')
    s.add_argument('--x', type=int, required=True)
    s.add_argument('--y', type=int, required=True)
    s = with_device('swipe')
    for name in ('--x1', '--y1', '--x2', '--y2'):
        s.add_argument(name, type=int, required=True)
    s.add_argument('--duration-ms', type=int, default=300)
    with_device('type').add_argument('--text', required=True)
    with_device('key').add_argument('--key', required=True)
    with_device('ui-tree')
    with_device('screenshot').add_argument('--out')
    s = with_device('logs')
    s.add_argument('--lines', type=int, default=300)
    s.add_argument('--filter')
    s.add_argument('--clear', action='store_true')
    with_device('run-flow').add_argument('--flow-file', required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    from _auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({'error': 'Invalid or expired auth token for this user'}))
        return 1
    headers = {'X-Loma-User': args.user_email, 'X-Loma-Auth-Token': args.auth_token}
    if args.command == 'install' and args.file:
        name = os.path.basename(args.file)
        with open(args.file, 'rb') as handle:
            upload = _request('/internal/devices/upload?filename=' + urllib.request.quote(name), headers, data=handle.read())
        if 'error' in upload:
            print(json.dumps(upload))
            return 1
        args.upload_id = upload['upload_id']
    result = _request('/internal/devices/call', headers, build_body(args))
    if args.command == 'screenshot' and 'png_base64' in result:
        out = args.out or f'/tmp/loma-device-{int(time.time())}.png'
        with open(out, 'wb') as handle:
            handle.write(base64.b64decode(result.pop('png_base64')))
        result['saved_to'] = out
    print(json.dumps(result, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    sys.exit(main())
