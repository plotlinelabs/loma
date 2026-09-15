"""Codex app-server adapter for the disposable, networkless worker image.

Never import agent pools, backend configuration, dotenv or personal tools here.
The supervisor's container is the security boundary, not these process settings.
This adapter is not called by legacy chat until its backend cutover is ready.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import signal

from isolation.model_bridge import ModelBridge
from isolation.protocol import MAX_FRAME


class RuntimeFailed(RuntimeError):
    pass


def runtime_environment(home):
    # Deliberately do not inherit HOME, account stores, proxy variables, API keys,
    # PYTHONPATH, plugins, hooks or runtime-specific settings from our caller.
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home),
            'CODEX_HOME': str(home / '.codex'), 'LANG': 'C.UTF-8',
            'TMPDIR': str(home / 'tmp')}


def write_config(home, origin, model):
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', origin):
        raise ValueError('Only the worker loopback model bridge is allowed')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', model):
        raise ValueError('A fixed model identifier is required')
    config = home / '.codex'
    config.mkdir(mode=0o700)
    (home / 'tmp').mkdir(mode=0o700)
    # Custom provider intentionally requires no key. The real provider credential
    # is selected by ModelGrant on the backend, not by this runtime or its files.
    (config / 'config.toml').write_text('\n'.join([
        f'model = {json.dumps(model)}', 'model_provider = "broker"',
        'approval_policy = "never"', 'sandbox_mode = "read-only"',
        'web_search = "disabled"',
        '[features]', 'shell_tool = false', 'multi_agent = false',
        '[model_providers.broker]', 'name = "Broker"',
        f'base_url = {json.dumps(origin + "/v1")}', 'wire_api = "responses"',
        'requires_openai_auth = false', 'supports_websockets = false',
        'request_max_retries = 0', 'stream_max_retries = 0',
    ]) + '\n')


def tool_definitions(tools):
    if not isinstance(tools, list) or len(tools) > 64:
        raise ValueError('Invalid worker tool catalog')
    definitions, names = [], {}
    for index, tool in enumerate(tools):
        if (not isinstance(tool, dict) or set(tool) != {'name', 'description', 'input_schema'}
                or not isinstance(tool['name'], str)
                or not re.fullmatch(r'[a-z][a-z0-9_.]{0,127}', tool['name'])
                or tool['name'].startswith(('model.', 'artifacts.'))
                or not isinstance(tool['description'], str) or len(tool['description']) > 4000
                or not isinstance(tool['input_schema'], dict)
                or tool['input_schema'].get('type') != 'object'
                or tool['name'] in names.values()):
            raise ValueError('Invalid worker tool definition')
        # Codex function names cannot contain dots. The map is local, never an
        # authorization grant: every dispatch is still checked by ToolGateway.
        alias = f'gateway_{index}'
        names[alias] = tool['name']
        definitions.append({'name': alias, 'description': tool['description'],
                            'inputSchema': tool['input_schema']})
    return definitions, names


class CodexRuntime:
    def __init__(self, root, rpc, emit, *, executable='/usr/local/bin/codex'):
        self.root, self.rpc, self.emit = Path(root), rpc, emit
        self.executable = executable  # image/operator setting, never a worker input field
        self.proc = None
        self.reader = None
        self.pending = {}
        self.events = asyncio.Queue(maxsize=256)
        self.next_id = 0
        self.tools = {}
        self.thread = None
        self.bridge = ModelBridge('responses', rpc)
        self.closed = False

    async def send(self, payload):
        raw = (json.dumps(payload, allow_nan=False) + '\n').encode()
        if len(raw) > MAX_FRAME:
            raise RuntimeFailed('Runtime frame is too large')
        self.proc.stdin.write(raw)
        await self.proc.stdin.drain()

    async def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            while raw := await self.proc.stdout.readline():
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise RuntimeFailed('Invalid runtime frame')
                if 'id' in value and ('result' in value or 'error' in value):
                    future = self.pending.get(value['id'])
                    if future is None or future.done():
                        raise RuntimeFailed('Unexpected runtime response')
                    if 'error' in value:
                        future.set_exception(RuntimeFailed('Runtime request failed'))
                    else:
                        future.set_result(value['result'])
                else:
                    # Never await a tool in this reader: native requests may
                    # arrive before the turn/start response. A separate consumer
                    # handles them without deadlocking initialization.
                    self.events.put_nowait(value)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # do not expose runtime/provider diagnostics in chat
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeFailed('Runtime connection closed'))
            try:
                self.events.put_nowait({'method': '__closed'})
            except asyncio.QueueFull:
                # Overflow must terminate rather than silently lose completion.
                if self.proc.returncode is None:
                    self.proc.kill()

    async def start(self, model, instructions, tools):
        definitions, self.tools = tool_definitions(tools)
        if not isinstance(instructions, str) or len(instructions.encode()) > 256 * 1024:
            raise ValueError('Invalid instructions')
        # A fresh directory per adapter; never restore account/config directories
        # or native sessions from an attachment or another worker.
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        origin = await self.bridge.serve()
        write_config(self.root, origin, model)
        self.proc = await asyncio.create_subprocess_exec(self.executable, 'app-server',
            cwd=self.root, env=runtime_environment(self.root), start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME)
        self.reader = asyncio.create_task(self.read())
        await self.request('initialize', {'clientInfo': {'name': 'loma-worker', 'version': '1'},
                                         'capabilities': {'experimentalApi': True}})
        await self.send({'method': 'initialized', 'params': {}})
        result = await self.request('thread/start', {'model': model, 'modelProvider': 'broker',
            'cwd': str(self.root), 'approvalPolicy': 'never', 'sandbox': 'read-only',
            'ephemeral': True, 'baseInstructions': instructions, 'dynamicTools': definitions})
        self.thread = result['thread']['id']

    async def turn(self, prompt, *, timeout=600):
        if self.closed or not self.thread or not isinstance(prompt, str) or len(prompt.encode()) > 512 * 1024:
            raise RuntimeFailed('Invalid runtime turn')
        async with asyncio.timeout(timeout):
            await self.request('turn/start', {'threadId': self.thread,
                                'input': [{'type': 'text', 'text': prompt}]})
            while True:
                event = await self.events.get()
                method, params = event.get('method'), event.get('params') or {}
                if method == '__closed':
                    raise RuntimeFailed('Runtime disconnected; not replayed')
                if 'id' in event:
                    result = await self.handle_call(method, params)
                    await self.send({'id': event['id'], 'result': result})
                elif method == 'item/agentMessage/delta':
                    text = params.get('delta')
                    if not isinstance(text, str):
                        raise RuntimeFailed('Invalid text event')
                    await self.emit(text)
                elif method == 'turn/completed':
                    if params.get('turn', {}).get('status') != 'completed':
                        raise RuntimeFailed('Runtime turn did not complete')
                    return
                elif method == 'error' and not params.get('willRetry', False):
                    raise RuntimeFailed('Runtime failed; not replayed')

    async def handle_call(self, method, params):
        if method != 'item/tool/call':
            # Never auto-approve native shell/file/network permission requests.
            if method in ('execCommandApproval', 'applyPatchApproval'):
                return {'decision': 'denied'}
            return {'decision': 'decline'}
        name = self.tools.get(params.get('tool'))
        if (name is None or params.get('threadId') != self.thread
                or params.get('namespace') not in (None, '') or not isinstance(params.get('arguments'), dict)):
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Tool denied'}]}
        result = await self.rpc(name, params['arguments'])
        return {'success': not (isinstance(result, dict) and 'error' in result),
                'contentItems': [{'type': 'inputText', 'text': json.dumps(result, allow_nan=False)}]}

    async def close(self):
        self.closed = True
        # Kill the process group even if the parent already exited; otherwise
        # runtime subprocesses may keep the pipe/HTTP bridge open indefinitely.
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.proc.wait()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.bridge.close()
