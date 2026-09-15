"""Native OpenCode adapter for a disposable networkless worker image.

All provider/tool traffic terminates at worker-local bridges. Configuration and
state are created afresh, never copied from a backend or personal home directory.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import signal

from isolation.codex_worker import RuntimeFailed
from isolation.mcp_bridge import MCPBridge
from isolation.model_bridge import ModelBridge
from isolation.protocol import MAX_FRAME


def runtime_environment(home, config):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8',
            'TMPDIR': str(home / 'tmp'), 'XDG_CONFIG_HOME': str(home / 'config'),
            'XDG_DATA_HOME': str(home / 'data'), 'XDG_CACHE_HOME': str(home / 'cache'),
            'XDG_STATE_HOME': str(home / 'state'), 'OPENCODE_CONFIG_CONTENT': json.dumps(config),
            'OPENCODE_DISABLE_AUTOUPDATE': 'true', 'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true',
            'OPENCODE_DISABLE_PROJECT_CONFIG': 'true', 'OPENCODE_DISABLE_CLAUDE_CODE': 'true',
            'OPENCODE_DISABLE_MODELS_FETCH': 'true', 'OPENCODE_DISABLE_SHARE': 'true'}


def configuration(origin, mcp_url, model, instructions):
    return {'model': 'broker/' + model, 'small_model': 'broker/' + model,
        'enabled_providers': ['broker'], 'autoupdate': False, 'share': 'disabled',
        'snapshot': False, 'plugin': [], 'instructions': [],
        'compaction': {'auto': False, 'prune': False},
        'permission': {'*': 'deny', 'gateway_*': 'allow'},
        'provider': {'broker': {'npm': '@ai-sdk/openai-compatible', 'name': 'Broker',
            'options': {'baseURL': origin + '/v1', 'apiKey': 'worker-local-placeholder'},
            'models': {model: {'name': model, 'limit': {'context': 128000, 'output': 8192}}}}},
        'agent': {'worker': {'mode': 'primary', 'prompt': instructions,
                            'permission': {'*': 'deny', 'gateway_*': 'allow'}}},
        'default_agent': 'worker',
        'mcp': {'gateway': {'type': 'remote', 'url': mcp_url, 'oauth': False}}}


class OpenCodeRuntime:
    def __init__(self, root, rpc, emit, *, executable='/usr/local/bin/opencode'):
        self.root, self.rpc, self.emit = Path(root), rpc, emit
        self.executable = executable
        self.bridge = ModelBridge('chat', rpc)
        self.mcp = None
        self.proc = None
        self.closed = False
        self.busy = False
        self.used = False

    async def start(self, model, instructions, tools):
        if (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,200}', model)
                or not isinstance(instructions, str) or len(instructions.encode()) > 256 * 1024):
            raise ValueError('Invalid runtime configuration')
        self.mcp = MCPBridge(tools, self.rpc)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        (self.root / 'tmp').mkdir(mode=0o700)
        origin = await self.bridge.serve()
        mcp_url = await self.mcp.serve()
        self.model = model
        self.env = runtime_environment(self.root, configuration(origin, mcp_url, model, instructions))

    async def turn(self, prompt, *, timeout=3600):
        if (self.closed or self.busy or self.used or not isinstance(prompt, str)
                or len(prompt.encode()) > 512 * 1024):
            raise RuntimeFailed('Invalid runtime turn')
        self.busy = True
        self.used = True
        completed = False
        try:
            async with asyncio.timeout(timeout):
                self.proc = await asyncio.create_subprocess_exec(self.executable,
                    'run', '--pure', '--format', 'json', '--model', 'broker/' + self.model,
                    '--agent', 'worker', '--title', 'Isolated work',
                    cwd=self.root, env=self.env, start_new_session=True,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME)
                self.proc.stdin.write(prompt.encode())
                await self.proc.stdin.drain()
                self.proc.stdin.close()
                async for raw in self.proc.stdout:
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise RuntimeFailed('Invalid native event') from None
                    if not isinstance(event, dict):
                        raise RuntimeFailed('Invalid native event')
                    if event.get('type') == 'error':
                        raise RuntimeFailed('Native runtime failed; not replayed')
                    if event.get('type') == 'text':
                        text = event.get('part', {}).get('text')
                        if not isinstance(text, str):
                            raise RuntimeFailed('Invalid native text')
                        await self.emit(text)
                    if event.get('type') == 'step_finish' and event.get('part', {}).get('reason') == 'stop':
                        completed = True
                if await self.proc.wait() != 0 or not completed:
                    raise RuntimeFailed('Native runtime disconnected; not replayed')
        finally:
            await self._stop()
            self.busy = False

    async def _stop(self):
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.proc.wait()

    async def close(self):
        self.closed = True
        await self._stop()
        if self.mcp is not None:
            await self.mcp.close()
        await self.bridge.close()
