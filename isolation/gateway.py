"""Trusted run-bound dispatch. No general CLI, URL, path or identity API.

The same gateway serves all runtime adapters. Runtime/model output cannot
register handlers, pass credentials or extend grants. Writes and sends are
never executed here: a worker can only file a durable proposal through the
run-bound proposal gateway (isolation/proposals.py), and only the owner's
signed decision lets the backend execute the exact approved version. An
unsupported action fails closed, not through a shell fallback.
"""
import asyncio
import re

from isolation.protocol import RunAuthority

FILE_SCHEMAS = {
    'artifacts.list': set(),
    'artifacts.describe': {'artifact_id'},
    'artifacts.read': {'artifact_id', 'offset'},
    'artifacts.begin': {'name', 'size'},
    'artifacts.write': {'artifact_id', 'offset', 'data'},
    'artifacts.commit': {'artifact_id', 'sha256'},
}
# Typed read-only adapters over the exact-script personal connector. Sends and
# all other writes stay in the durable approval engine; this gateway has none.
# tool: (required argument names, optional argument names).
READ_SCHEMAS = {
    'gmail.search': ({'query'}, {'limit'}),
    'gmail.read': ({'message_id'}, set()),
    'gmail.inbox': (set(), {'query', 'limit'}),
    'calendar.list': (set(), {'limit'}),
    'calendar.search': ({'query'}, {'limit'}),
    'calendar.get': ({'event_id'}, set()),
    'drive.list': (set(), {'query', 'limit'}),
    'drive.search': ({'query'}, {'limit'}),
    'drive.read': ({'file_id'}, set()),
    'docs.info': ({'document_id'}, set()),
    'docs.read': ({'document_id'}, set()),
    'sheets.info': ({'spreadsheet_id'}, set()),
    'sheets.tabs': ({'spreadsheet_id'}, set()),
    'sheets.read': ({'spreadsheet_id', 'range'}, set()),
    'slack.read': ({'channel'}, {'limit'}),
    'slack.search': ({'query'}, {'limit'}),
    'notifications.list': (set(), {'limit'}),
    'grain.search': ({'query'}, set()),
    'grain.transcript': ({'recording_id'}, set()),
    'grain.recent': (set(), {'days'}),
    'pylon.issue': ({'issue_id'}, set()),
    'pylon.messages': ({'issue_id'}, set()),
    'pylon.teams': (set(), set()),
    'pylon.issues': (set(), {'days', 'state', 'team_id'}),
    'posthog.projects': (set(), set()),
    'posthog.definitions': (set(), {'search', 'limit'}),
    'posthog.events': ({'event_name'}, {'from', 'to', 'limit'}),
    'linear.velocity': ({'month'}, set()),
    'linear.bucket_split': ({'month'}, set()),
}
INT_ARGS = {'limit': (1, 50), 'days': (1, 90)}
_DATE = r'\d{4}-\d{2}-\d{2}'
PATTERN_ARGS = {'month': r'\d{4}-\d{2}', 'from': _DATE, 'to': _DATE}
# Arguments some hand-rolled CLIs consume positionally: a leading dash there
# could be read as a flag, so reject it instead of trusting downstream parsing.
POSITIONAL_ARGS = {
    'grain.search': {'query'}, 'grain.transcript': {'recording_id'},
    'pylon.issue': {'issue_id'}, 'pylon.messages': {'issue_id'},
    'posthog.events': {'event_name'},
}


def validate_read(tool, arguments):
    required, optional = READ_SCHEMAS[tool]
    if not isinstance(arguments, dict) or not required <= set(arguments) <= required | optional:
        raise GatewayDenied('Unknown tool or invalid arguments')
    for name, value in arguments.items():
        if name in INT_ARGS:
            low, high = INT_ARGS[name]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise GatewayDenied('Invalid read arguments')
            continue
        if (not isinstance(value, str) or not value.strip() or len(value) > 1000
                or any(c in value for c in '\x00\r\n')):
            raise GatewayDenied('Invalid read arguments')
        if name in PATTERN_ARGS and not re.fullmatch(PATTERN_ARGS[name], value):
            raise GatewayDenied('Invalid read arguments')
        if name in POSITIONAL_ARGS.get(tool, ()) and value.lstrip().startswith('-'):
            raise GatewayDenied('Invalid read arguments')


class GatewayDenied(ValueError):
    pass


class ToolGateway:
    def __init__(self, authority: RunAuthority, *, authorize, audit, artifacts, connector=None, models=None, knowledge=None, on_artifact=None, proposals=None):
        if artifacts.authority != authority or not callable(authorize) or not callable(audit):
            raise ValueError('A matching server-owned artifact scope and policy are required')
        self.authority, self.authorize, self.audit = authority, authorize, audit
        self.artifacts = artifacts
        self.connector = connector
        if models is not None and models.authority != authority:
            raise ValueError('A matching server-owned model relay is required')
        self.models = models
        if knowledge is not None and knowledge.authority != authority:
            raise ValueError('A matching server-owned knowledge scope is required')
        self.knowledge = knowledge
        if proposals is not None and proposals.authority != authority:
            raise ValueError('A matching server-owned proposal scope is required')
        self.proposals = proposals
        if on_artifact is not None and not callable(on_artifact):
            raise ValueError('A trusted artifact registration callback is required')
        self.on_artifact = on_artifact
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
            from isolation.knowledge import SCHEMAS as KNOWLEDGE_SCHEMAS, validate as validate_knowledge
            if tool in KNOWLEDGE_SCHEMAS:
                if self.knowledge is None:
                    raise GatewayDenied('Knowledge gateway is unavailable')
                validate_knowledge(tool, arguments)
                await self.audit(authority, {'tool': tool, 'stage': 'requested'})
                if not await self.authorize(authority):
                    raise GatewayDenied('Run access is no longer valid')
                result = await self.knowledge(authority, tool, arguments)
                if not await self.authorize(authority):
                    raise GatewayDenied('Run access is no longer valid')
                await self.audit(authority, {'tool': tool, 'stage': 'completed'})
                return result
            from isolation.proposals import TOOLS as PROPOSAL_TOOLS
            if tool in PROPOSAL_TOOLS:
                # Durable proposal only: no send adapter exists on this path.
                if self.proposals is None:
                    raise GatewayDenied('Proposal gateway is unavailable')
                await self.audit(authority, {'tool': tool, 'stage': 'requested'})
                if not await self.authorize(authority):
                    raise GatewayDenied('Run access is no longer valid')
                result = await self.proposals(authority, tool, arguments)
                if not await self.authorize(authority):
                    raise GatewayDenied('Run access is no longer valid')
                await self.audit(authority, {'tool': tool, 'stage': 'completed'})
                return result
            if tool in FILE_SCHEMAS:
                if not isinstance(arguments, dict) or set(arguments) != FILE_SCHEMAS[tool]:
                    raise GatewayDenied('Unknown tool or invalid arguments')
            elif tool in READ_SCHEMAS:
                if self.connector is None:
                    raise GatewayDenied('Personal connector is unavailable')
                validate_read(tool, arguments)
            else:
                raise GatewayDenied('Unknown tool or invalid arguments')
            # Audit must succeed before a privileged action; do not put message
            # contents, provider data, bytes or tokens in infrastructure logs.
            await self.audit(authority, {'tool': tool, 'stage': 'requested'})
            if not await self.authorize(authority):
                raise GatewayDenied('Run access is no longer valid')
            try:
                if tool == 'artifacts.list':
                    result = {'files': self.artifacts.manifest()}
                elif tool == 'artifacts.describe':
                    result = self.artifacts.metadata(arguments['artifact_id'])
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
            if tool == 'artifacts.commit' and self.on_artifact is not None:
                # Receipt comes from committed/checksummed broker bytes, not a
                # worker-supplied URL, path, owner or claimed output event.
                await self.on_artifact(dict(result))
                if not await self.authorize(authority):
                    raise GatewayDenied('Run access is no longer valid')
            await self.audit(authority, {'tool': tool, 'stage': 'completed'})
            return result


# tool: (connector script key, subcommand, argument template). Template rows
# are (option flag or None for a positional, argument name, default); a None or
# empty-string value omits the row entirely. Flag spellings and positional
# order are pinned to each first-party CLI's parser; see autonomy/connector.py.
READ_COMMANDS = {
    'gmail.search': ('gmail', 'search', (('--query', 'query', None), ('--limit', 'limit', 5))),
    'gmail.read': ('gmail', 'read-email', (('--message-id', 'message_id', None),)),
    'gmail.inbox': ('gmail', 'list-inbox', (('--query', 'query', ''), ('--limit', 'limit', 10))),
    'calendar.list': ('calendar', 'list-events', (('--limit', 'limit', 10),)),
    'calendar.search': ('calendar', 'search', (('--query', 'query', None), ('--limit', 'limit', 10))),
    'calendar.get': ('calendar', 'get-event', (('--event-id', 'event_id', None),)),
    'drive.list': ('drive', 'list-files', (('--query', 'query', ''), ('--limit', 'limit', 10))),
    'drive.search': ('drive', 'search', (('--query', 'query', None), ('--limit', 'limit', 10))),
    'drive.read': ('drive', 'read-file', (('--file-id', 'file_id', None),)),
    'docs.info': ('docs', 'get-info', (('--document-id', 'document_id', None),)),
    'docs.read': ('docs', 'read-doc', (('--document-id', 'document_id', None),)),
    'sheets.info': ('sheets', 'get-info', (('--spreadsheet-id', 'spreadsheet_id', None),)),
    'sheets.tabs': ('sheets', 'list-sheets', (('--spreadsheet-id', 'spreadsheet_id', None),)),
    'sheets.read': ('sheets', 'read-range', (('--spreadsheet-id', 'spreadsheet_id', None), ('--range', 'range', None))),
    'slack.read': ('slack', 'read-channel', (('--channel', 'channel', None), ('--limit', 'limit', 50))),
    'slack.search': ('slack', 'search', (('--query', 'query', None), ('--limit', 'limit', 20))),
    'notifications.list': ('notify', 'list', (('--limit', 'limit', 20),)),
    'grain.search': ('grain', 'search', ((None, 'query', None),)),
    'grain.transcript': ('grain', 'transcript', ((None, 'recording_id', None),)),
    'grain.recent': ('grain', 'recent', ((None, 'days', 7),)),
    'pylon.issue': ('pylon', 'issue', ((None, 'issue_id', None),)),
    'pylon.messages': ('pylon', 'messages', ((None, 'issue_id', None),)),
    'pylon.teams': ('pylon', 'teams', ()),
    'pylon.issues': ('pylon', 'issues', (('--days', 'days', 7), ('--state', 'state', ''), ('--team', 'team_id', ''))),
    'posthog.projects': ('posthog', 'projects', ()),
    'posthog.definitions': ('posthog', 'definitions', (('--search', 'search', ''), ('--limit', 'limit', 50))),
    'posthog.events': ('posthog', 'events', ((None, 'event_name', None), ('--from', 'from', ''), ('--to', 'to', ''), ('--limit', 'limit', 50))),
    'linear.velocity': ('linear', 'velocity', (('--month', 'month', None),)),
    'linear.bucket_split': ('linear', 'bucket-split', (('--month', 'month', None),)),
}


async def personal_read(tool, arguments, owner):
    """Fixed backend connector commands. Credentials never enter a worker."""
    from autonomy.connector import personal
    if tool not in READ_COMMANDS or not isinstance(arguments, dict):
        raise GatewayDenied('Read adapter is unavailable')
    validate_read(tool, arguments)
    script, subcommand, template = READ_COMMANDS[tool]
    command = [subcommand]
    for option, name, default in template:
        value = arguments.get(name, default)
        if value is None or value == '':
            continue
        command += [option, str(value)] if option else [str(value)]
    return await personal(script, command, owner)
