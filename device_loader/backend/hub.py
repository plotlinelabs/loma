"""In-process registry of connected runners and request/response RPC over their WebSocket.

A runner holds one WebSocket to ONE backend process. Loma runs a single backend
process today; with several replicas a call must be routed to the replica that
holds the socket (not implemented; calls elsewhere fail with "offline").
"""
import asyncio
import json
import logging
import secrets
import time

logger = logging.getLogger(__name__)

STALE_SECONDS = 60  # no heartbeat for this long => treat as offline
OP_TIMEOUTS = {'install': 900, 'run_flow': 660, 'logs': 90, 'ui_tree': 90, 'wait_for': 120, 'tap_text': 120,
               'set_text': 90, 'clear_text': 90, 'scroll_until_visible': 300, 'burst': 120, 'record': 90,
               'boot': 420, 'shutdown': 90}
DEFAULT_TIMEOUT = 60
MAX_PENDING = 32


# Stable failure codes. The first group comes from runners (>= 1.3.0), the rest from the backend.
RUNNER_CODES = {'invalid_args', 'unsupported', 'policy_denied', 'not_found', 'ambiguous', 'ui_not_idle', 'timeout',
                'tool_missing', 'device_error'}
CODES = RUNNER_CODES | {'runner_offline', 'device_busy', 'device_held', 'ref_stale', 'runner_too_old'}
RETRIABLE = {'ui_not_idle', 'timeout', 'runner_offline', 'device_busy', 'device_held', 'not_found'}
HINTS = {
    'ui_not_idle': 'The screen kept animating; retry, or turn animations off (device.input animations enabled=false).',
    'timeout': 'Check whether the action happened (ui_tree) before retrying; dispatched=unknown means it may have.',
    'not_found': 'Read ui_tree to see what is on screen, wait longer (timeout_s), or scroll_until_visible.',
    'ambiguous': 'Pick one of details.candidates with nth, narrow the match (by, exact), or tap a ref from ui_tree.',
    'ref_stale': 'Read ui_tree again (or use the refs returned by an action with settle=true).',
    'runner_offline': 'The runner machine is asleep or the runner stopped; retry later or lease another device.',
    'device_busy': 'Another session holds the device; wait, or lease another device.',
    'device_held': ('A person is using this device from the dashboard. Wait 30-60 s and retry the same call; '
                    'keep the device (your app state is on it) instead of leasing another one.'),
    'runner_too_old': 'The runner owner must update the Loma Device Runner.',
    'policy_denied': 'The runner owner does not allow this; do not retry.',
}
MAX_DETAILS = 4000  # bytes of JSON; runner-sent details (e.g. candidates) beyond this are dropped


class DeviceError(Exception):
    """User-facing failure (offline runner, runner-side error, timeout).

    code: a stable reason from CODES. dispatched: 'no' when the input certainly never reached the device
    (safe to retry), 'unknown' when it may have; None when it does not apply. retriable: retrying the same
    call can succeed. details: small structured context, e.g. candidates for an ambiguous match.
    """

    def __init__(self, message, code='device_error', dispatched=None, details=None, retriable=None, hint=None):
        super().__init__(message)
        self.code = code if code in CODES else 'device_error'
        self.dispatched = dispatched if dispatched in ('no', 'unknown') else None
        self.details = details if isinstance(details, dict) else {}
        self.retriable = (self.code in RETRIABLE) if retriable is None else bool(retriable)
        self.hint = hint or HINTS.get(self.code)

    def to_dict(self):
        """The structured part, for tool results (never an 'error' key: see gateway.failure)."""
        info = {'code': self.code, 'retriable': self.retriable}
        if self.dispatched:
            info['dispatched'] = self.dispatched
        if self.hint:
            info['hint'] = self.hint
        if self.details:
            info['details'] = self.details
        return info

    @classmethod
    def from_runner(cls, frame):
        """A failed result frame. Runners before 1.3.0 send only 'error'."""
        details = frame.get('details') if isinstance(frame.get('details'), dict) else None
        if details is not None and len(json.dumps(details, default=str)) > MAX_DETAILS:
            details = None
        code = frame.get('code') if frame.get('code') in RUNNER_CODES else 'device_error'
        dispatched = frame.get('dispatched') if frame.get('dispatched') in ('no', 'unknown') else 'unknown'
        return cls(str(frame.get('error') or 'Runner call failed')[:2000], code, dispatched, details)


class Connection:
    def __init__(self, runner_id, ws, devices, version='', templates=None):
        self.runner_id = runner_id
        self.ws = ws
        self.devices = devices
        self.version = version  # runner VERSION from its hello; service.py gates newer ops on it
        self.templates = templates or []  # [{name, platform, clean}] the runner can boot (from its hello)
        self.pending = {}
        self.last_seen = time.monotonic()
        self.send_lock = asyncio.Lock()

    @property
    def alive(self):
        return not self.ws.closed and time.monotonic() - self.last_seen < STALE_SECONDS

    async def send(self, frame):
        async with self.send_lock:
            await self.ws.send_str(json.dumps(frame))


class RunnerHub:
    def __init__(self):
        self.connections = {}

    def get(self, runner_id):
        conn = self.connections.get(runner_id)
        return conn if conn is not None and conn.alive else None

    async def attach(self, runner_id, ws, devices, version='', templates=None):
        old = self.connections.get(runner_id)
        conn = Connection(runner_id, ws, devices, version, templates)
        self.connections[runner_id] = conn
        if old is not None and old.ws is not ws:
            self._fail_pending(old, 'Runner reconnected')
            await old.ws.close(code=4000, message=b'replaced by a newer connection')
        return conn

    def detach(self, conn):
        if self.connections.get(conn.runner_id) is conn:
            del self.connections[conn.runner_id]
        self._fail_pending(conn, 'Runner disconnected')

    def _fail_pending(self, conn, reason):
        for future in conn.pending.values():
            if not future.done():
                future.set_exception(DeviceError(reason, 'runner_offline', 'unknown'))
        conn.pending.clear()

    def on_frame(self, conn, frame):
        """Handle one runner frame. Returns an updated device list when the runner sent one."""
        conn.last_seen = time.monotonic()
        kind = frame.get('type') if isinstance(frame, dict) else None
        if kind == 'result':
            future = conn.pending.pop(frame.get('id'), None) if isinstance(frame.get('id'), str) else None
            if future is not None and not future.done():
                if frame.get('ok') is True and isinstance(frame.get('data'), dict):
                    future.set_result(frame['data'])
                else:
                    future.set_exception(DeviceError.from_runner(frame))
        elif kind == 'devices' and isinstance(frame.get('devices'), list):
            conn.devices = frame['devices'][:50]
            return conn.devices
        return None

    async def call(self, runner_id, op, serial, args):
        conn = self.get(runner_id)
        if conn is None:
            raise DeviceError('Runner is offline. Start the Loma Device Runner on that machine and retry.',
                              'runner_offline', 'no')
        if len(conn.pending) >= MAX_PENDING:
            raise DeviceError('Runner is busy; retry shortly', 'device_busy', 'no')
        call_id = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = future
        try:
            timeout = OP_TIMEOUTS.get(op, DEFAULT_TIMEOUT)
            # The runner gets the deadline too, so it drops calls this side has given up on.
            await conn.send({'type': 'call', 'id': call_id, 'op': op, 'device': serial, 'args': args, 'timeout': timeout})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise DeviceError(f'Device operation {op} timed out', 'timeout', 'unknown') from None
        except ConnectionResetError:
            raise DeviceError('Runner disconnected', 'runner_offline', 'unknown') from None
        finally:
            conn.pending.pop(call_id, None)

    async def revoke(self, runner_id):
        conn = self.connections.pop(runner_id, None)
        if conn is None:
            return
        self._fail_pending(conn, 'Runner was revoked')
        try:
            await conn.send({'type': 'revoked'})
        except Exception:
            pass
        await conn.ws.close(code=4001, message=b'revoked')


hub = RunnerHub()
