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
  device.py ... install --device-id ID (--repo OWNER/NAME --artifact-name NAME [--pr N | --run-id N]
                [--wait SECONDS] [--dispatch-workflow FILE.yml] | --file PATH) [--app-id PKG]
                [--grant-appop OP ...] [--grant-privacy SERVICE ...] [--force]
  device.py ... app --device-id ID --action launch|stop|reset_app|uninstall --app-id PKG
                [--extra KEY=VALUE ...] [--bool-extra KEY=true|false ...] [--activity .Main] [--console]
  device.py ... open-url --device-id ID --url URL
  device.py ... tap --device-id ID (--ref e3 | --x X --y Y)      (refs come from ui-tree)
  device.py ... tap-text --device-id ID --match TEXT [--by any|text|id|label] [--exact] [--timeout S]
  device.py ... wait-for --device-id ID --match TEXT [--by ...] [--exact] [--timeout S] [--gone]
  device.py ... scroll-until-visible --device-id ID --match TEXT [--direction down|up] [--max-swipes N]
  device.py ... set-text --device-id ID --text TEXT [--ref e3 | --match FIELD [--by ...]] [--no-clear]
  device.py ... clear-text --device-id ID [--ref e3 | --match FIELD [--by ...]]
  device.py ... animations --device-id ID --off|--on                (Android emulators)
  device.py ... swipe --device-id ID --x1 . --y1 . --x2 . --y2 . [--duration-ms MS]
  device.py ... type --device-id ID --text TEXT
  device.py ... key --device-id ID --key back|home|enter|delete|escape|wakeup|...
  device.py ... ui-tree --device-id ID [--compact] [--clickable-only] [--filter TEXT]
  device.py ... screenshot --device-id ID [--out PATH] [--preview]    (then Read the PNG / preview JPEG)
  device.py ... burst --device-id ID --count N [--interval-ms MS] [--app-id PKG [--extra K=V ...]] [--preview]
  device.py ... record --device-id ID --duration S [--app-id PKG [--extra K=V ...]]
  device.py ... logs --device-id ID [--lines N] [--filter TEXT] [--tag TAG ...] [--since CURSOR] [--clear]
                [--source auto|system|console]     (every result has a cursor; pass it as --since next time)
  device.py ... run-flow --device-id ID --flow-file flow.yaml [--verbose]
  device.py ... scenario --device-id ID --spec case.yaml   (timed steps + video + screen changes + logs, one call)

Auth: --user-email / --auth-token, or LOMA_USER_EMAIL / LOMA_AUTH_TOKEN in the environment.
Never write the token into a script or file: it expires after an hour anyway.

scenario spec (YAML or JSON). The same spec shape covers any test; names below are placeholders:
  app_id: com.example.app        # optional: stopped, then launched at t0
  duration_s: 30                 # capture window, 1-60 s
  record: true                   # optional mp4 of the window
  log_tags: [MyTag, OtherTag]    # optional: keep log lines containing any of these
  steps:                         # run one after the other (after_ms after the previous step, default 0)
    - {action: tap_text, match: "Sign in"}
    - {action: set_text, match: "Email", text: "a@b.co"}
    - {action: wait_for, match: "Welcome", timeout_s: 15}
    - {action: screenshot, name: home}
  expect:                        # optional: the runner returns verdict pass/fail + reasons
    app_running: true
    logs: [{match: "login_ok", by_ms: 8000}, {match: "ERROR", max: 0}]
  Timing tests: give steps a fixed offset instead ({at_ms: 1500, action: tap, x: 540, y: 1200}) and add
  sample_ms: 250 (screen-change timeline; sample_region / sample_min_change narrow it) with
  expect: {settled_by_ms: 3000}.
  Step actions: tap, tap_text, wait_for, set_text, clear_text, scroll_until_visible, swipe, type, key,
  open_url, screenshot, launch_app, stop_app. stop_on_fail: true ends the steps at the first failure;
  end_after_steps: true returns as soon as the steps are done (duration_s is then only an upper bound).

Files (screenshots, burst frames, recordings, flow screenshots) are written to
$LOMA_CONVERSATION_DIR/device/ when that is set, else to a per-conversation dir
under /tmp/loma-device/<scope>/, always under a new unique name, so a later capture
never reuses (or shadows) an earlier file.
"""
import argparse
import base64
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PREVIEW_MAX = 1024
_counter = itertools.count(1)


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


def _pairs(items, flag, as_bool=False):
    """--extra KEY=VALUE (repeatable) -> dict. Keys/values are validated by the backend and runner."""
    result = {}
    for item in items or []:
        key, sep, value = item.partition('=')
        if not sep or not key:
            raise SystemExit(f'{flag} expects KEY=VALUE, got {item!r}')
        if as_bool:
            if value.lower() not in ('true', 'false'):
                raise SystemExit(f'{flag} {key} must be true or false')
            value = value.lower() == 'true'
        result[key] = value
    return result


def _launch_args(args):
    out = {}
    extras, flags = _pairs(args.extra, '--extra'), _pairs(args.bool_extra, '--bool-extra', as_bool=True)
    if extras:
        out['extras'] = extras
    if flags:
        out['bool_extras'] = flags
    if args.activity:
        out['activity'] = args.activity
    return out


def _selector(args):
    out = {'match': args.match} if args.match else {}
    if args.by != 'any':
        out['by'] = args.by
    if args.exact:
        out['exact'] = True
    return out


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
        if args.file:  # main() adds upload_id after uploading the file
            if args.repo or args.artifact_name or args.pr or args.run_id or args.wait or args.dispatch_workflow:
                raise SystemExit('install takes either --file or --repo/--artifact-name, not both')
        else:
            if not (args.repo and args.artifact_name):
                raise SystemExit('install needs --file, or --repo and --artifact-name')
            build = {'repo': args.repo, 'artifact_name': args.artifact_name}
            if args.pr:
                build['pr'] = args.pr
            if args.run_id:
                build['run_id'] = args.run_id
            call_args['build'] = build
            if args.wait:
                call_args['wait_s'] = args.wait
            if args.dispatch_workflow:
                call_args['dispatch_workflow'] = args.dispatch_workflow
        if args.grant_appop:
            call_args['grant_appops'] = args.grant_appop
        if args.grant_privacy:
            call_args['grant_privacy'] = args.grant_privacy
        if args.force:
            call_args['force'] = True
        return {**call, 'op': 'install', 'args': call_args}
    if args.command == 'app':
        call_args = {'app_id': args.app_id}
        if args.action == 'launch':
            call_args.update(_launch_args(args))
            if args.console:
                call_args['console'] = True
        elif args.extra or args.bool_extra or args.activity or args.console:
            raise SystemExit('--extra/--bool-extra/--activity/--console only apply to --action launch')
        return {**call, 'op': args.action, 'args': call_args}
    if args.command == 'open-url':
        return {**call, 'op': 'open_url', 'args': {'url': args.url}}
    if args.command == 'tap':
        if args.ref:
            if args.x is not None or args.y is not None:
                raise SystemExit('tap takes either --ref or --x/--y')
            return {**call, 'op': 'tap', 'args': {'ref': args.ref}}
        if args.x is None or args.y is None:
            raise SystemExit('tap needs --ref, or both --x and --y')
        return {**call, 'op': 'tap', 'args': {'x': args.x, 'y': args.y}}
    if args.command == 'animations':
        return {**call, 'op': 'animations', 'args': {'enabled': args.on}}
    if args.command in ('tap-text', 'wait-for'):
        call_args = _selector(args)
        if args.timeout is not None:
            call_args['timeout_s'] = args.timeout
        if args.command == 'wait-for' and args.gone:
            call_args['gone'] = True
        return {**call, 'op': args.command.replace('-', '_'), 'args': call_args}
    if args.command == 'scroll-until-visible':
        call_args = {**_selector(args), 'direction': args.direction, 'max_swipes': args.max_swipes}
        return {**call, 'op': 'scroll_until_visible', 'args': call_args}
    if args.command in ('set-text', 'clear-text') and args.ref and args.match:
        raise SystemExit('Use either --ref or --match, not both')
    if args.command == 'set-text':
        call_args = {'text': args.text, **_selector(args), **({'ref': args.ref} if args.ref else {})}
        if args.no_clear:
            call_args['clear'] = False
        return {**call, 'op': 'set_text', 'args': call_args}
    if args.command == 'clear-text':
        return {**call, 'op': 'clear_text', 'args': {**_selector(args), **({'ref': args.ref} if args.ref else {})}}
    if args.command == 'swipe':
        return {**call, 'op': 'swipe', 'args': {'x1': args.x1, 'y1': args.y1, 'x2': args.x2, 'y2': args.y2,
                                               'duration_ms': args.duration_ms}}
    if args.command == 'type':
        return {**call, 'op': 'type', 'args': {'text': args.text}}
    if args.command == 'key':
        return {**call, 'op': 'key', 'args': {'key': args.key}}
    if args.command == 'ui-tree':
        call_args = {}
        if args.compact:
            call_args['compact'] = True
        if args.clickable_only:
            call_args['clickable_only'] = True
        if args.filter:
            call_args['filter'] = args.filter
        return {**call, 'op': 'ui_tree', 'args': call_args}
    if args.command == 'screenshot':
        return {**call, 'op': 'screenshot', 'args': {}}
    if args.command in ('burst', 'record'):
        call_args = {'count': args.count, 'interval_ms': args.interval_ms} if args.command == 'burst' \
            else {'duration_s': args.duration}
        if args.app_id:
            call_args.update({'app_id': args.app_id, **_launch_args(args)})
        elif args.extra or args.bool_extra or args.activity:
            raise SystemExit('--extra/--bool-extra/--activity need --app-id (the app launched when capture starts)')
        return {**call, 'op': args.command, 'args': call_args}
    if args.command == 'logs':
        log_args = {'lines': args.lines, 'clear': args.clear}
        if args.filter:
            log_args['filter'] = args.filter
        if args.source:
            log_args['source'] = args.source
        if args.tag:
            log_args['tags'] = args.tag
        if args.since:
            log_args['since'] = args.since
        return {**call, 'op': 'logs', 'args': log_args}
    if args.command == 'scenario':
        return {**call, 'op': 'scenario', 'args': load_spec(args.spec)}
    if args.command == 'run-flow':
        with open(args.flow_file) as handle:
            flow_args = {'flow': handle.read()}
        if args.verbose:
            flow_args['verbose'] = True
        return {**call, 'op': 'run_flow', 'args': flow_args}
    raise SystemExit('Unknown command')


def load_spec(path):
    """A scenario spec file (YAML or JSON object); the backend and runner validate its contents."""
    with open(path) as handle:
        text = handle.read()
    try:
        spec = json.loads(text)
    except ValueError:
        import yaml
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SystemExit(f'--spec is not valid YAML/JSON: {str(exc)[:300]}')
    if not isinstance(spec, dict):
        raise SystemExit('--spec must be a mapping (duration_s, steps, ...)')
    return spec


def parser():
    p = argparse.ArgumentParser(description='Drive devices on your Loma Device Runners')
    p.add_argument('--user-email', default=os.environ.get('LOMA_USER_EMAIL'))
    p.add_argument('--auth-token', default=os.environ.get('LOMA_AUTH_TOKEN'))
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

    def launch_options(cmd):
        cmd.add_argument('--extra', action='append', metavar='KEY=VALUE',
                         help='Android: am start --es KEY VALUE; iOS: launch argument -KEY VALUE (UserDefaults)')
        cmd.add_argument('--bool-extra', action='append', metavar='KEY=true|false',
                         help='Android: am start --ez; iOS: -KEY YES|NO')
        cmd.add_argument('--activity', help='Android activity to start (default: the launcher activity)')

    def selector(cmd, required=True):
        cmd.add_argument('--match', required=required, help='Text, resource-id / accessibility id, or label')
        cmd.add_argument('--by', choices=['any', 'text', 'id', 'label'], default='any')
        cmd.add_argument('--exact', action='store_true', help='Whole-value match (case-insensitive)')

    with_device('release')
    s = with_device('install')
    s.add_argument('--repo')
    s.add_argument('--artifact-name')
    s.add_argument('--pr', type=int)
    s.add_argument('--run-id', type=int)
    s.add_argument('--file')
    s.add_argument('--app-id')
    s.add_argument('--wait', type=int, default=0, metavar='SECONDS',
                   help='Backend waits (no model turns) up to SECONDS for the CI run to produce the artifact')
    s.add_argument('--dispatch-workflow', metavar='FILE.yml',
                   help='With --pr: dispatch this workflow on the PR branch if no run exists for its head (must be in the admin workflow allowlist)')
    s.add_argument('--grant-appop', action='append', metavar='OP', help='Android app-op to allow, e.g. SCHEDULE_EXACT_ALARM')
    s.add_argument('--grant-privacy', action='append', metavar='SERVICE', help='iOS simctl privacy service, e.g. photos')
    s.add_argument('--force', action='store_true', help='Reinstall even if this exact build is already installed')
    s = with_device('app')
    s.add_argument('--action', required=True, choices=['launch', 'stop', 'reset_app', 'uninstall'])
    s.add_argument('--app-id', required=True)
    launch_options(s)
    s.add_argument('--console', action='store_true', help='iOS: capture the app stdout (print) for `logs`')
    with_device('open-url').add_argument('--url', required=True)
    s = with_device('tap')
    s.add_argument('--ref', help='Element ref from the latest ui-tree (e.g. e3)')
    s.add_argument('--x', type=int)
    s.add_argument('--y', type=int)
    s = with_device('animations')
    toggle = s.add_mutually_exclusive_group(required=True)
    toggle.add_argument('--off', dest='on', action='store_false', help='Faster, steadier ui-tree / taps')
    toggle.add_argument('--on', dest='on', action='store_true', help='Restore default animation scales')
    for name in ('tap-text', 'wait-for'):
        s = with_device(name)
        selector(s)
        s.add_argument('--timeout', type=int, metavar='SECONDS')
        if name == 'wait-for':
            s.add_argument('--gone', action='store_true', help='Wait until no element matches')
    s = with_device('scroll-until-visible')
    selector(s)
    s.add_argument('--direction', choices=['down', 'up'], default='down')
    s.add_argument('--max-swipes', type=int, default=8)
    s = with_device('set-text')
    s.add_argument('--text', required=True)
    selector(s, required=False)
    s.add_argument('--ref', help='Field ref from the latest ui-tree (e.g. e3)')
    s.add_argument('--no-clear', action='store_true', help='Append instead of replacing the field content')
    s = with_device('clear-text')
    selector(s, required=False)
    s.add_argument('--ref', help='Field ref from the latest ui-tree (e.g. e3)')
    s = with_device('swipe')
    for name in ('--x1', '--y1', '--x2', '--y2'):
        s.add_argument(name, type=int, required=True)
    s.add_argument('--duration-ms', type=int, default=300)
    with_device('type').add_argument('--text', required=True)
    with_device('key').add_argument('--key', required=True)
    s = with_device('ui-tree')
    s.add_argument('--compact', action='store_true', help='One line per element: ref type text #id @x,y (* clickable)')
    s.add_argument('--clickable-only', action='store_true')
    s.add_argument('--filter', help='Keep elements whose text/label/id contains this')
    s = with_device('screenshot')
    s.add_argument('--out')
    s.add_argument('--preview', action='store_true', help=f'Also write a JPEG (max {PREVIEW_MAX}px) for viewing')
    s = with_device('burst')
    s.add_argument('--count', type=int, required=True)
    s.add_argument('--interval-ms', type=int, default=500)
    s.add_argument('--app-id', help='Launch this app right before the first frame')
    s.add_argument('--preview', action='store_true')
    launch_options(s)
    s = with_device('record')
    s.add_argument('--duration', type=int, required=True, metavar='SECONDS')
    s.add_argument('--app-id', help='Launch this app once recording has started')
    launch_options(s)
    s = with_device('logs')
    s.add_argument('--lines', type=int, default=300)
    s.add_argument('--filter')
    s.add_argument('--clear', action='store_true')
    s.add_argument('--source', choices=['auto', 'system', 'console'])
    s.add_argument('--tag', action='append', metavar='TAG', help='Keep lines containing any --tag (counts per tag)')
    s.add_argument('--since', metavar='CURSOR', help='Only lines after the cursor a previous logs call returned')
    s = with_device('scenario')
    s.add_argument('--spec', required=True, help='YAML/JSON scenario spec (see the module docstring)')
    s = with_device('run-flow')
    s.add_argument('--flow-file', required=True)
    s.add_argument('--verbose', action='store_true', help='Full Maestro output and JUnit report')
    return p


def output_dir(scope=None):
    """$LOMA_CONVERSATION_DIR/device, else /tmp/loma-device/<scope> (never a bare '/device')."""
    base = (os.environ.get('LOMA_CONVERSATION_DIR') or '').strip()
    if base and os.path.isabs(base) and Path(base) != Path('/'):
        folder = Path(base) / 'device'
    else:
        safe = ''.join(c for c in str(scope or '') if c.isalnum() or c in '-_.').strip('.')[:128]
        folder = Path('/tmp/loma-device') / safe if safe else Path('/tmp/loma-device')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unique_path(stem, ext, requested=None, scope=None):
    """A path that does not exist yet: timestamp + pid + counter. --out is kept if it is free."""
    if requested:
        path = Path(requested)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        folder, stem, ext = path.parent, path.stem, path.suffix or ext
    else:
        folder = output_dir(scope)
    while True:
        stamp = time.strftime('%Y%m%d-%H%M%S') + f'-{int(time.time() * 1000) % 1000:03d}'
        path = folder / f'{stem}-{stamp}-{os.getpid()}-{next(_counter)}{ext}'
        if not path.exists():
            return path


def write_preview(png_path, max_side=PREVIEW_MAX):
    """Downscaled JPEG next to the PNG for viewing (cheaper to read); the PNG stays for uploads."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(png_path) as image:
            image = image.convert('RGB')
            image.thumbnail((max_side, max_side))
            target = png_path.with_suffix('.preview.jpg')
            image.save(target, 'JPEG', quality=80)
            return str(target)
    except OSError:
        return None


def save_media(args, result):
    """Write media returned as base64 to unique files and replace it with paths."""
    preview = getattr(args, 'preview', False)
    scope = getattr(args, 'scope', None)
    if 'png_base64' in result:
        path = unique_path('screenshot', '.png', getattr(args, 'out', None), scope=scope)
        path.write_bytes(base64.b64decode(result.pop('png_base64')))
        result['saved_to'] = str(path)
        if preview:
            result['preview'] = write_preview(path)
    for index, frame in enumerate(result.get('frames') or []):
        if 'png_base64' in frame:
            path = unique_path(f'burst-{index:02d}-{frame.get("at_ms", 0)}ms', '.png', scope=scope)
            path.write_bytes(base64.b64decode(frame.pop('png_base64')))
            frame['saved_to'] = str(path)
            if preview:
                frame['preview'] = write_preview(path)
    for shot in result.get('screenshots') or []:
        if 'png_base64' in shot:
            name = ''.join(c for c in str(shot.get('name') or 'flow') if c.isalnum() or c in '-_.')[:60]
            path = unique_path('flow-' + (name[:-4] if name.endswith('.png') else name), '.png', scope=scope)
            path.write_bytes(base64.b64decode(shot.pop('png_base64')))
            shot['saved_to'] = str(path)
    if 'mp4_base64' in result:
        path = unique_path('recording', '.mp4', scope=scope)
        path.write_bytes(base64.b64decode(result.pop('mp4_base64')))
        result['saved_to'] = str(path)
    return result


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not args.user_email or not args.auth_token:
        p.error('--user-email and --auth-token are required (or set LOMA_USER_EMAIL / LOMA_AUTH_TOKEN)')
    body = build_body(args)
    headers = {'X-Loma-User': args.user_email, 'X-Loma-Auth-Token': args.auth_token}
    if args.command == 'install' and args.file:
        name = os.path.basename(args.file)
        with open(args.file, 'rb') as handle:
            upload = _request('/internal/devices/upload?filename=' + urllib.request.quote(name), headers, data=handle.read())
        if 'error' in upload:
            print(json.dumps(upload))
            return 1
        body['args']['upload_id'] = upload['upload_id']
    timeout = 1800 + (getattr(args, 'wait', 0) or 0)
    result = save_media(args, _request('/internal/devices/call', headers, body, timeout=timeout))
    print(json.dumps(result, indent=2))
    return 1 if 'error' in result or result.get('found') is False else 0


if __name__ == '__main__':
    sys.exit(main())
