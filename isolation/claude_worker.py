"""Native Claude Code, credential-free and gateway-only inside a worker.

The fixed image and supervisor provide containment. Never run on the backend.
No SDK/pool imports, native session restore, account files or inherited config.
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


def runtime_environment(home, origin):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home),
            'CLAUDE_CONFIG_DIR': str(home / '.claude'), 'TMPDIR': str(home / 'tmp'),
            'LANG': 'C.UTF-8', 'ANTHROPIC_BASE_URL': origin,
            # Local SDK placeholder only; bridge discards it and the real grant
            # chooses credentials on the backend. It has no upstream authority.
            'ANTHROPIC_API_KEY': 'worker-local-placeholder',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
            'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS': '1', 'ENABLE_TOOL_SEARCH': 'false',
            'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '8192', 'MAX_THINKING_TOKENS': '0',
            'CLAUDE_CODE_SKIP_PROMPT_HISTORY': '1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1',
            'ANTHROPIC_MAX_RETRIES': '0'}


class ClaudeRuntime:
    def __init__(self, root, rpc, emit, *, executable='/usr/local/bin/claude'):
        self.root, self.rpc, self.emit = Path(root), rpc, emit
        self.executable = executable
        self.bridge = ModelBridge('messages', rpc, native_messages=True)
        self.mcp = None
        self.proc = None
        self.closed = False
        self.busy = False
        self.used = False

    async def start(self, model, instructions, tools):
        if (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', model)
                or not isinstance(instructions, str) or len(instructions.encode()) > 256 * 1024):
            raise ValueError('Invalid runtime configuration')
        self.mcp = MCPBridge(tools, self.rpc)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        (self.root / 'tmp').mkdir(mode=0o700)
        origin = await self.bridge.serve()
        mcp_url = await self.mcp.serve()
        self.model, self.instructions = model, instructions
        self.env = runtime_environment(self.root, origin)
        self.config = {'mcpServers': {'gateway': {'type': 'http', 'url': mcp_url}}}

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
                    '--bare', '--print', '--verbose', '--output-format', 'stream-json',
                    '--include-partial-messages', '--no-session-persistence', '--tools', '',
                    '--disable-slash-commands', '--strict-mcp-config', '--mcp-config', json.dumps(self.config),
                    '--setting-sources', '', '--permission-mode', 'dontAsk',
                    '--allowedTools', 'mcp__gateway__*', '--model', self.model,
                    '--system-prompt', self.instructions,
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
                    if event.get('type') == 'stream_event':
                        data = event.get('event', {})
                        delta = data.get('delta', {})
                        if data.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                            text = delta.get('text')
                            if not isinstance(text, str):
                                raise RuntimeFailed('Invalid native text')
                            await self.emit(text)
                    elif event.get('type') == 'result':
                        if completed or event.get('is_error') or event.get('subtype') != 'success':
                            raise RuntimeFailed('Native runtime failed; not replayed')
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
