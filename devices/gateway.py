"""Isolated-worker adapter for device.* tools.

Maps the compact model-visible tools (the worker catalog is capped at 64
tools) onto DeviceService operations. Identity and lease scope come from the
backend-created RunAuthority, never from tool arguments.

Screenshots are committed as run artifacts and registered as chat downloads,
so the USER sees them in the conversation. Isolated workers cannot render
images, so the model asserts on device.observe ui_tree instead.

Results are capped well below the worker frame limit: an oversized tool
result would otherwise abort the whole run (isolation/protocol.MAX_FRAME).
"""
import json
import logging

from devices.hub import DeviceError
from devices.service import DeviceService

logger = logging.getLogger(__name__)
MAX_RESULT = 200 * 1024
MAX_SCREENSHOTS = 8  # per run; shares the run's 20-file artifact budget with workspace.publish

TOOLS = {'device.list', 'device.lease', 'device.release', 'device.install', 'device.app',
         'device.input', 'device.observe', 'device.run_flow'}
APP_ACTIONS = {'launch', 'stop', 'reset_app', 'uninstall'}
INPUT_ACTIONS = {'tap', 'swipe', 'type', 'key', 'open_url'}
OBSERVE_ACTIONS = {'screenshot', 'ui_tree', 'logs'}


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
        self.service = service or DeviceService(db)

    async def __call__(self, authority, tool, arguments):
        if authority != self.authority or tool not in TOOLS or not isinstance(arguments, dict):
            raise DeviceError('Invalid device request')
        owner, args = authority.user_email, dict(arguments)
        try:
            return cap_result(await self._dispatch(owner, tool, args))
        except DeviceError as exc:
            # Device problems are normal, recoverable outcomes the agent should see and act on.
            return {'error': str(exc)}
        except Exception:
            # Never let an infrastructure error (Mongo, network) abort the whole run.
            logger.exception('Device tool %s failed', tool)
            return {'error': 'Device operation failed unexpectedly; retry, or check Loma → Devices'}

    async def _dispatch(self, owner, tool, args):
        service, scope = self.service, self.scope
        if tool == 'device.list':
            _pick(args, set(), set(), tool)
            return {'devices': await service.list_devices(owner)}
        if tool == 'device.lease':
            picked = _pick(args, set(), {'platform', 'device_id'}, tool)
            return await service.lease(owner, scope, picked.get('device_id'), picked.get('platform'))
        device_id = args.pop('device_id', None)
        if not isinstance(device_id, str):
            raise DeviceError('device_id is required; get one from device.list or device.lease')
        if tool == 'device.release':
            _pick(args, set(), set(), tool)
            return await service.release(owner, scope, device_id)
        if tool == 'device.install':
            picked = _pick(args, {'repo', 'artifact_name'}, {'pr', 'run_id', 'app_id'}, tool)
            build = {k: picked[k] for k in ('repo', 'artifact_name', 'pr', 'run_id') if k in picked}
            call_args = {'build': build, **({'app_id': picked['app_id']} if 'app_id' in picked else {})}
            return await service.call(owner, scope, device_id, 'install', call_args)
        # Per-op argument shapes are validated once, in DeviceService.
        if tool == 'device.app':
            return await service.call(owner, scope, device_id, _action(args, 'action', APP_ACTIONS), args)
        if tool == 'device.input':
            return await service.call(owner, scope, device_id, _action(args, 'action', INPUT_ACTIONS), args)
        if tool == 'device.observe':
            what = _action(args, 'what', OBSERVE_ACTIONS)
            data = await service.call(owner, scope, device_id, what, args)
            return await self._deliver_screenshot(data) if what == 'screenshot' else data
        return await service.call(owner, scope, device_id, 'run_flow', args)

    async def _deliver_screenshot(self, data):
        png = data.pop('png')
        if self.screenshots >= MAX_SCREENSHOTS:
            raise DeviceError(f'Screenshot limit for this run reached ({MAX_SCREENSHOTS}); use ui_tree, '
                              'and keep screenshots for final evidence')
        try:
            receipt = self.artifacts.ingest(f'device-screenshot-{self.screenshots + 1}.png', png)
            info = await self.on_artifact(dict(receipt))
        except (ValueError, OSError):
            raise DeviceError('Could not store the screenshot (run file limit reached?)') from None
        self.screenshots += 1
        return {'width': data.get('width'), 'height': data.get('height'), 'delivered': True,
                'file': {'name': info.get('name'), 'url': info.get('url')},
                'note': 'The screenshot is shown to the user in this chat as evidence. You cannot view '
                        'images here; use device.observe ui_tree to check what is on screen.'}


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
        return {'error': 'Device result too large; narrow it (e.g. logs with filter and fewer lines)'}
    return result
