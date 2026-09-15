"""Trusted run-bound dispatch. No general CLI, URL, path or identity API.

The same gateway serves all runtime adapters. Runtime/model output cannot
register handlers, pass credentials or extend grants. Sending must use the
existing durable approval engine; this read/file gateway deliberately has no
send adapter. An unsupported action fails closed, not through a shell fallback.
"""
import asyncio

from isolation.protocol import RunAuthority

FILE_SCHEMAS = {
    'artifacts.list': set(),
    'artifacts.read': {'artifact_id', 'offset'},
    'artifacts.begin': {'name', 'size'},
    'artifacts.write': {'artifact_id', 'offset', 'data'},
    'artifacts.commit': {'artifact_id', 'sha256'},
}
READ_SCHEMAS = {'gmail.search': {'query'}, 'gmail.read': {'message_id'}, 'calendar.list': set()}


class GatewayDenied(ValueError):
    pass


class ToolGateway:
    def __init__(self, authority: RunAuthority, *, authorize, audit, artifacts, connector=None, models=None):
        if artifacts.authority != authority or not callable(authorize) or not callable(audit):
            raise ValueError('A matching server-owned artifact scope and policy are required')
        self.authority, self.authorize, self.audit = authority, authorize, audit
        self.artifacts = artifacts
        self.connector = connector
        if models is not None and models.authority != authority:
            raise ValueError('A matching server-owned model relay is required')
        self.models = models
        self.lock = asyncio.Lock()
        self.calls = 0

    async def __call__(self, authority, tool, arguments):
        # Serialize file mutation and policy checks, even when called outside the
        # currently sequential WebSocket transport. Cancelled calls never replay.
        async with self.lock:
            if authority != self.authority or not await self.authorize(self.authority):
                raise GatewayDenied('Run access is no longer valid')
            if self.calls >= 500:
                raise GatewayDenied('Tool-call limit reached')
            self.calls += 1
            if not isinstance(tool, str) or tool not in authority.allowed_tools:
                raise GatewayDenied('Tool is not allowed')
            if tool.startswith('model.'):
                if self.models is None:
                    raise GatewayDenied('Model relay is unavailable')
                return await self.models(authority, tool, arguments)
            schema = FILE_SCHEMAS.get(tool, READ_SCHEMAS.get(tool))
            if schema is None or not isinstance(arguments, dict) or set(arguments) != schema:
                raise GatewayDenied('Unknown tool or invalid arguments')
            if tool in READ_SCHEMAS:
                if self.connector is None:
                    raise GatewayDenied('Personal connector is unavailable')
                for value in arguments.values():
                    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or '\x00' in value:
                        raise GatewayDenied('Invalid read arguments')
            # Audit must succeed before a privileged action; do not put message
            # contents, provider data, bytes or tokens in infrastructure logs.
            await self.audit(authority, {'tool': tool, 'stage': 'requested'})
            if not await self.authorize(authority):
                raise GatewayDenied('Run access is no longer valid')
            try:
                if tool == 'artifacts.list':
                    result = {'files': self.artifacts.manifest()}
                elif tool == 'artifacts.read':
                    result = self.artifacts.read(arguments['artifact_id'], arguments['offset'])
                elif tool == 'artifacts.begin':
                    result = self.artifacts.begin(arguments['name'], arguments['size'])
                elif tool == 'artifacts.write':
                    result = self.artifacts.write(arguments['artifact_id'], arguments['offset'], arguments['data'])
                elif tool == 'artifacts.commit':
                    result = self.artifacts.commit(arguments['artifact_id'], arguments['sha256'])
                else:
                    result = await self.connector(tool, dict(arguments), authority.user_email)
            except (ValueError, OSError):
                await self.audit(authority, {'tool': tool, 'stage': 'failed'})
                raise GatewayDenied('Tool request failed') from None
            # Never return data fetched while the account was being revoked.
            if not await self.authorize(authority):
                raise GatewayDenied('Run access is no longer valid')
            await self.audit(authority, {'tool': tool, 'stage': 'completed'})
            return result


async def personal_read(tool, arguments, owner):
    """Fixed backend connector commands. Credentials never enter a worker."""
    from autonomy.connector import personal
    if tool == 'gmail.search':
        return await personal('gmail', ['search', '--query', arguments['query'], '--limit', '5'], owner)
    if tool == 'gmail.read':
        return await personal('gmail', ['read-email', '--message-id', arguments['message_id']], owner)
    if tool == 'calendar.list':
        return await personal('calendar', ['list-events', '--limit', '10'], owner)
    raise GatewayDenied('Read adapter is unavailable')
