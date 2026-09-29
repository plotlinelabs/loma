"""Isolated-worker adapter for device.* tools.

Maps the compact model-visible tools (the worker catalog is capped at 64
tools) onto DeviceService operations. Identity and lease scope come from the
backend-created RunAuthority, never from tool arguments. Screenshots are
delivered as run artifacts (bytes never go through the model as base64); the
worker imports them with workspace.import to look at them.
"""
from devices.hub import DeviceError
from devices.service import DeviceService

TOOLS = {'device.list', 'device.lease', 'device.release', 'device.install', 'device.app',
         'device.input', 'device.observe', 'device.run_flow'}
APP_ACTIONS = {'launch', 'stop', 'reset_app', 'uninstall'}
INPUT_FIELDS = {
    'tap': ({'x', 'y'}, set()),
    'swipe': ({'x1', 'y1', 'x2', 'y2'}, {'duration_ms'}),
    'type': ({'text'}, set()),
    'key': ({'key'}, set()),
    'open_url': ({'url'}, set()),
}
OBSERVE_FIELDS = {'screenshot': set(), 'ui_tree': set(), 'logs': {'lines', 'filter', 'clear'}}


def _pick(arguments, required, optional, label):
    extra = set(arguments) - required - optional
    missing = required - set(arguments)
    if extra or missing:
        raise DeviceError(f'{label}: ' + '; '.join(
            ([f'missing {sorted(missing)}'] if missing else []) + ([f'unexpected {sorted(extra)}'] if extra else [])))
    return {k: arguments[k] for k in required | optional if k in arguments}


class DeviceTools:
    def __init__(self, db, authority, conversation_id, artifacts=None, service=None):
        self.authority = authority
        self.scope = f'conv:{conversation_id}' if conversation_id else f'run:{authority.run_id}'
        self.artifacts = artifacts
        self.service = service or DeviceService(db)

    async def __call__(self, authority, tool, arguments):
        if authority != self.authority or tool not in TOOLS or not isinstance(arguments, dict):
            raise DeviceError('Invalid device request')
        owner, args = authority.user_email, dict(arguments)
        try:
            return await self._dispatch(owner, tool, args)
        except DeviceError as exc:
            # Device problems are normal, recoverable outcomes the agent should see and act on.
            return {'error': str(exc)}

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
        if tool == 'device.app':
            picked = _pick(args, {'action', 'app_id'}, set(), tool)
            if picked['action'] not in APP_ACTIONS:
                raise DeviceError('action must be one of ' + ', '.join(sorted(APP_ACTIONS)))
            return await service.call(owner, scope, device_id, picked['action'], {'app_id': picked['app_id']})
        if tool == 'device.input':
            action = args.pop('action', None)
            if action not in INPUT_FIELDS:
                raise DeviceError('action must be one of ' + ', '.join(sorted(INPUT_FIELDS)))
            required, optional = INPUT_FIELDS[action]
            return await service.call(owner, scope, device_id, action, _pick(args, required, optional, f'{tool} {action}'))
        if tool == 'device.observe':
            what = args.pop('what', None)
            if what not in OBSERVE_FIELDS:
                raise DeviceError('what must be one of ' + ', '.join(sorted(OBSERVE_FIELDS)))
            data = await service.call(owner, scope, device_id, what, _pick(args, set(), OBSERVE_FIELDS[what], f'{tool} {what}'))
            if what == 'screenshot':
                return self._deliver_screenshot(data)
            return data
        if tool == 'device.run_flow':
            picked = _pick(args, {'flow'}, set(), tool)
            return await service.call(owner, scope, device_id, 'run_flow', picked)
        raise DeviceError('Unsupported device tool')

    def _deliver_screenshot(self, data):
        png = data.pop('png')
        result = {'width': data.get('width'), 'height': data.get('height')}
        if self.artifacts is None:
            result['note'] = 'Screenshot captured, but this run has no workspace to receive files.'
            return result
        try:
            receipt = self.artifacts.ingest('screenshot.png', png)
        except (ValueError, OSError):
            raise DeviceError('Could not store the screenshot (run file limit reached?)') from None
        result.update({'artifact': receipt,
                       'note': 'Import it with workspace.import(artifact_id) to view it; '
                               'prefer device.observe ui_tree for assertions.'})
        return result
