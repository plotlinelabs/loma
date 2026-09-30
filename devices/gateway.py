"""Isolated-worker adapter for device.* tools.

Maps the compact model-visible tools (the worker catalog is capped at 64
tools) onto DeviceService operations. Identity and lease scope come from the
backend-created RunAuthority, never from tool arguments.

Screenshots are committed as run artifacts and registered as chat downloads,
so the USER sees them in the conversation. Isolated workers cannot render
images, so the model asserts on device.observe ui_tree instead.

Results are capped well below the worker frame limit: an oversized tool
result would otherwise abort the whole run (isolation/protocol.MAX_FRAME).

Two more worker limits shape the results (isolation/worker_entry.Broker.rpc):
- A result containing an 'error' key is a fatal broker denial that poisons the
  rest of the run, so recoverable device failures use failure() instead.
- Each tool call must answer within 120 s. Longer operations (install, Maestro
  flows) keep running here and report pending; calling the same tool again on the
  same device waits for that result.
"""
import asyncio
import json
import logging

from devices.hub import DeviceError
from devices.service import DeviceService

logger = logging.getLogger(__name__)
MAX_RESULT = 200 * 1024
MAX_SCREENSHOTS = 8  # per run; shares the run's 20-file artifact budget with workspace.publish
MAX_RECORDINGS = 2  # per run; each up to 16 MB (a run holds 64 MB of artifacts in total)
WAIT_SECONDS = 90  # below the worker's 120 s per-call RPC timeout

TOOLS = {'device.list', 'device.lease', 'device.release', 'device.install', 'device.app',
         'device.input', 'device.observe', 'device.run_flow', 'device.configure'}
APP_ACTIONS = {'launch', 'stop', 'reset_app', 'uninstall'}
INPUT_ACTIONS = {'animations', 'tap', 'swipe', 'type', 'key', 'open_url', 'set_text', 'clear_text', 'tap_text', 'wait_for',
                 'scroll_until_visible'}
OBSERVE_ACTIONS = {'screenshot', 'ui_tree', 'logs', 'record', 'burst', 'network', 'plotline', 'visual'}


def failure(message, **extra):
    """A recoverable device outcome the agent should read and act on (never an 'error' key)."""
    return {'ok': False, 'device_error': message, **extra}


def _pick(arguments, required, optional, label):
    extra = set(arguments) - required - optional
    missing = required - set(arguments)
    if extra or missing:
        raise DeviceError(f'{label}: ' + '; '.join(
            ([f'missing {sorted(missing)}'] if missing else []) + ([f'unexpected {sorted(extra)}'] if extra else [])))
    return {k: arguments[k] for k in required | optional if k in arguments}


def _action(arguments, key, allowed):
    action = arguments.pop(key, None)
    if action not in allowed:
        raise DeviceError(f'{key} must be one of ' + ', '.join(sorted(allowed)))
    return action


class DeviceTools:
    def __init__(self, db, authority, conversation_id, artifacts=None, service=None, on_artifact=None):
        self.authority = authority
        self.scope = f'conv:{conversation_id}' if conversation_id else f'run:{authority.run_id}'
        self.artifacts = artifacts
        self.on_artifact = on_artifact
        self.screenshots = 0
        self.recordings = 0
        self.service = service or DeviceService(db)
        self.pending = {}  # device_id -> (tool, task) still running past WAIT_SECONDS

    async def __call__(self, authority, tool, arguments):
        if authority != self.authority or tool not in TOOLS or not isinstance(arguments, dict):
            raise DeviceError('Invalid device request')
        owner, args = authority.user_email, dict(arguments)
        key = args.get('device_id') if isinstance(args.get('device_id'), str) else None
        if tool == 'device.lease' and key is None:  # a waiting lease / boot is resumed by calling it again
            key = f"lease:{args.get('platform')}:{args.get('template')}"
        try:
            if key in self.pending:
                busy_tool, task = self.pending[key]
                if busy_tool != tool:
                    return failure(f'This device is still busy with {busy_tool}; call {busy_tool} again with the '
                                   'same device_id to wait for its result', pending=True)
            else:
                task = asyncio.ensure_future(self._dispatch(owner, tool, args))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())  # never "never retrieved"
            done, _ = await asyncio.wait({task}, timeout=WAIT_SECONDS)
            if not done:
                self.pending[key] = (tool, task)
                return failure(f'{tool} is still running on the device. Call {tool} again with the same '
                               'device_id to wait for the result.', pending=True)
            self.pending.pop(key, None)
            return cap_result(task.result())
        except DeviceError as exc:
            return failure(str(exc))
        except Exception:
            # Never let an infrastructure error (Mongo, network) abort the whole run.
            logger.exception('Device tool %s failed', tool)
            return failure('Device operation failed unexpectedly; retry, or check Integrations → Devices')

    async def _dispatch(self, owner, tool, args):
        service, scope = self.service, self.scope
        if tool == 'device.list':
            _pick(args, set(), set(), tool)
            return {'devices': await service.list_devices(owner), 'templates': await service.templates_for(owner)}
        if tool == 'device.lease':
            picked = _pick(args, set(), {'platform', 'device_id', 'wait_online_s', 'template', 'clean'}, tool)
            return await service.lease(owner, scope, picked.get('device_id'), picked.get('platform'),
                                       picked.get('wait_online_s', 0), picked.get('template'), picked.get('clean', False))
        device_id = args.pop('device_id', None)
        if not isinstance(device_id, str):
            raise DeviceError('device_id is required; get one from device.list or device.lease')
        if tool == 'device.release':
            _pick(args, set(), set(), tool)
            return await service.release(owner, scope, device_id)
        if tool == 'device.install':
            options = {'app_id', 'grant_appops', 'grant_privacy', 'force', 'wait_s', 'dispatch_workflow'}
            picked = _pick(args, {'repo', 'artifact_name'}, {'pr', 'run_id'} | options, tool)
            build = {k: picked[k] for k in ('repo', 'artifact_name', 'pr', 'run_id') if k in picked}
            call_args = {'build': build, **{k: picked[k] for k in options if k in picked}}
            return await service.call(owner, scope, device_id, 'install', call_args)
        # Per-op argument shapes are validated once, in DeviceService.
        if tool == 'device.app':
            return await service.call(owner, scope, device_id, _action(args, 'action', APP_ACTIONS), args)
        if tool == 'device.input':
            return await service.call(owner, scope, device_id, _action(args, 'action', INPUT_ACTIONS), args)
        if tool == 'device.configure':
            capture = args.pop('capture_network', None)
            result = {}
            if capture is not None:
                if type(capture) is not bool:
                    raise DeviceError('capture_network must be true or false')
                result['network_capture'] = await service.call(owner, scope, device_id, 'netcap',
                                                               {'action': 'start' if capture else 'stop'})
            if args or capture is None:
                result.update(await service.call(owner, scope, device_id, 'configure', args))
            return result
        if tool == 'device.observe':
            what = _action(args, 'what', OBSERVE_ACTIONS)
            if what == 'network':
                return await service.call(owner, scope, device_id, 'netcap', {'action': 'read', **_pick(
                    args, set(), {'filter', 'limit'}, 'device.observe network')})
            if what == 'plotline':
                return await service.plotline_check(owner, scope, device_id, _pick(
                    args, {'product_id', 'user_id'}, {'flow_id', 'since_s'}, 'device.observe plotline'))
            if what == 'visual':
                picked = _pick(args, {'expect'}, set(), 'device.observe visual')
                verdict = await service.visual_check(owner, scope, device_id, picked['expect'])
                png = verdict.pop('png')
                try:
                    verdict['screenshot'] = await self._store_png(png, 'visual')
                except DeviceError:
                    pass  # the verdict still stands without the evidence file
                return verdict
            if what == 'record' and self.recordings >= MAX_RECORDINGS:
                raise DeviceError(f'Recording limit for this run reached ({MAX_RECORDINGS}); keep recordings for '
                                  'the final evidence')
            data = await service.call(owner, scope, device_id, what, args)
            if what == 'screenshot':
                return await self._deliver_screenshot(data)
            if what == 'record':
                return await self._deliver_recording(data)
            if what == 'burst':
                return await self._deliver_burst(data)
            return data
        data = await service.call(owner, scope, device_id, 'run_flow', args)
        return await self._deliver_flow_screenshots(data)

    async def _store_png(self, png, label):
        if self.screenshots >= MAX_SCREENSHOTS:
            raise DeviceError(f'Screenshot limit for this run reached ({MAX_SCREENSHOTS}); use ui_tree, '
                              'and keep screenshots for final evidence')
        info = await self._store(f'device-{label}-{self.screenshots + 1}.png', png, 'screenshot')
        self.screenshots += 1
        return info

    async def _store(self, filename, data, what):
        try:
            receipt = self.artifacts.ingest(filename, data)
            info = await self.on_artifact(dict(receipt))
        except (ValueError, OSError):
            raise DeviceError(f'Could not store the {what} (run file limit reached?)') from None
        return {'name': info.get('name'), 'url': info.get('url')}

    async def _deliver_recording(self, data):
        mp4 = data.pop('mp4')
        info = await self._store(f'device-recording-{self.recordings + 1}.mp4', mp4, 'recording')
        self.recordings += 1
        return {'delivered': True, 'bytes': len(mp4), 'duration_s': data.get('duration_s'), 'file': info,
                'note': 'The recording is shown to the user in this chat as evidence; you cannot view it.'}

    async def _deliver_burst(self, data):
        """Frames go to the user as screenshots (same budget); the model gets their timing only."""
        delivered, skipped = [], 0
        for frame in data.get('frames') or []:
            try:
                delivered.append({'at_ms': frame.get('at_ms'), **await self._store_png(frame['png'], 'burst')})
            except DeviceError:
                skipped += 1
        result = {'delivered': len(delivered), 'frames': delivered, 'interval_ms': data.get('interval_ms'),
                  'note': 'Frames are shown to the user in this chat; you cannot view them.'}
        if skipped:
            result['frames_not_delivered'] = skipped
        return result

    async def _deliver_screenshot(self, data):
        png = data.pop('png')
        return {'width': data.get('width'), 'height': data.get('height'), 'delivered': True,
                'file': await self._store_png(png, 'screenshot'),
                'note': 'The screenshot is shown to the user in this chat as evidence. You cannot view '
                        'images here; use device.observe ui_tree to check what is on screen.'}

    async def _deliver_flow_screenshots(self, data):
        """Maestro takeScreenshot files go to the user like device.observe screenshots (same budget)."""
        shots, delivered, skipped = data.pop('screenshots', None) or [], [], []
        for shot in shots:
            try:
                delivered.append({'step': shot.get('name'), **await self._store_png(shot['png'], 'flow')})
            except DeviceError:
                skipped.append(shot.get('name'))
        if delivered:
            data['screenshots'] = delivered
        if skipped:
            data['screenshots_not_delivered'] = skipped
        return data


def cap_result(result, limit=MAX_RESULT):
    """Trim list/text payloads (oldest log lines first) so the result fits the frame budget."""
    if not isinstance(result, dict) or len(json.dumps(result, default=str).encode()) <= limit:
        return result
    result = dict(result)
    for key in ('lines', 'elements'):
        items = result.get(key)
        if isinstance(items, list):
            while items and len(json.dumps(result, default=str).encode()) > limit:
                items = items[len(items) // 4 or 1:] if key == 'lines' else items[:len(items) * 3 // 4]
                result[key] = items
    for key in ('output', 'report'):
        text = result.get(key)
        if isinstance(text, str) and len(json.dumps(result, default=str).encode()) > limit:
            result[key] = text[-(limit // 4):]
    result['truncated'] = True
    if len(json.dumps(result, default=str).encode()) > limit:
        return failure('Device result too large; narrow it (e.g. logs with filter and fewer lines)')
    return result
