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
               'scenario': 200}  # scenario: 60 s window + 30 s step grace + video finalise
DEFAULT_TIMEOUT = 60
MAX_PENDING = 32


class DeviceError(Exception):
    """User-facing failure (offline runner, runner-side error, timeout)."""


class Connection:
    def __init__(self, runner_id, ws, devices, version=''):
        self.runner_id = runner_id
        self.ws = ws
        self.devices = devices
        self.version = version  # runner VERSION from its hello; service.py gates newer ops on it
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

    async def attach(self, runner_id, ws, devices, version=''):
        old = self.connections.get(runner_id)
        conn = Connection(runner_id, ws, devices, version)
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
                future.set_exception(DeviceError(reason))
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
                    future.set_exception(DeviceError(str(frame.get('error') or 'Runner call failed')[:2000]))
        elif kind == 'devices' and isinstance(frame.get('devices'), list):
            conn.devices = frame['devices'][:50]
            return conn.devices
        return None

    async def call(self, runner_id, op, serial, args):
        conn = self.get(runner_id)
        if conn is None:
            raise DeviceError('Runner is offline. Start the Loma Device Runner on that machine and retry.')
        if len(conn.pending) >= MAX_PENDING:
            raise DeviceError('Runner is busy; retry shortly')
        call_id = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = future
        try:
            timeout = OP_TIMEOUTS.get(op, DEFAULT_TIMEOUT)
            # The runner gets the deadline too, so it drops calls this side has given up on.
            await conn.send({'type': 'call', 'id': call_id, 'op': op, 'device': serial, 'args': args, 'timeout': timeout})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise DeviceError(f'Device operation {op} timed out') from None
        except ConnectionResetError:
            raise DeviceError('Runner disconnected') from None
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
