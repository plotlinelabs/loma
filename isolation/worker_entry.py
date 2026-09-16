"""Credential-free worker entrypoint. Only framed stdin/stdout crosses the host.

Image entrypoint only; never invoke this module on the privileged backend as a
fallback. The supervisor owns gVisor confinement and mandatory process teardown.
"""
import asyncio
import json
import sys
from pathlib import Path
import tempfile

from isolation.codex_worker import CodexRuntime
from isolation.workspace_tools import WorkspaceTools, TOOLS as WORKSPACE_TOOLS
from isolation.protocol import MAX_INPUT, ProtocolError, decode, response_frame, worker_frame


class Broker:
    def __init__(self, reader, write):
        self.reader, self.write = reader, write
        self.lock = asyncio.Lock()
        self.counter = 0
        self.failed = False

    async def frame(self, value):
        raw = json.dumps(value, allow_nan=False)
        worker_frame(raw)
        self.write((raw + '\n').encode())

    async def rpc(self, tool, arguments):
        # stdin has one consumer. Serial requests avoid stealing each other's
        # responses; a failed/cancelled exchange poisons the stream, never retries.
        async with self.lock:
            if self.failed or self.counter >= 500:
                raise ProtocolError('Broker unavailable')
            self.counter += 1
            request_id = str(self.counter)
            try:
                await self.frame({'type': 'tool_request', 'id': request_id,
                                  'tool': tool, 'arguments': arguments})
                async with asyncio.timeout(120):
                    raw = await self.reader.readline()
                result = decode(raw)
                response_frame(result)
                if result['id'] != request_id:
                    raise ProtocolError('Mismatched broker response')
                if isinstance(result['result'], dict) and 'error' in result['result']:
                    raise ProtocolError('Broker denied the request')
                return result['result']
            except BaseException:
                self.failed = True
                raise

    async def emit(self, text):
        await self.frame({'type': 'text', 'text': text})


def validate_input(value):
    if (not isinstance(value, dict)
            or set(value) != {'runtime', 'model', 'instructions', 'prompt', 'tools'}
            or value['runtime'] not in ('codex', 'claude', 'opencode')
            or not isinstance(value['prompt'], str)
            or len(value['prompt'].encode()) > 512 * 1024):
        raise ProtocolError('Unsupported worker input')
    return value


async def run(reader, write, *, root=Path('/workspace'), executable=None):
    raw = await asyncio.wait_for(reader.readline(), 15)
    first = decode(raw, limit=MAX_INPUT)
    if set(first) != {'type', 'input'} or first['type'] != 'start':
        raise ProtocolError('Missing worker start')
    value = validate_input(first['input'])
    broker = Broker(reader, write)
    with tempfile.TemporaryDirectory(dir=root, prefix='chat-') as work:
        if value['runtime'] == 'claude':
            from isolation.claude_worker import ClaudeRuntime
            runtime_class = ClaudeRuntime
        elif value['runtime'] == 'opencode':
            from isolation.opencode_worker import OpenCodeRuntime
            runtime_class = OpenCodeRuntime
        else:
            runtime_class = CodexRuntime
        # Fixed image locations. The optional executable override is for trusted
        # local tests, never a wire field or a fallback to backend execution.
        executable = executable or '/usr/local/bin/' + value['runtime']
        allowed = {tool['name'] for tool in value['tools']}
        files = WorkspaceTools(Path(work) / 'files', broker.rpc, allowed)
        runtime = runtime_class(Path(work) / 'runtime', files, broker.emit, executable=executable)
        try:
            await runtime.start(value['model'], value['instructions'], value['tools'])
            if allowed & WORKSPACE_TOOLS:
                manifest = await broker.rpc('artifacts.list', {})
                await files.workspace.stage(manifest['files'])
            await runtime.turn(value['prompt'])
            # Native completion can precede backend EOF/usage settlement. Do
            # not cancel an in-flight broker reply or emit done with a pending
            # request. Cancellation still takes the immediate finally path.
            await runtime.bridge.drain()
        finally:
            try:
                await runtime.close()
            finally:
                files.close()
    await broker.frame({'type': 'done'})


async def main():
    reader = asyncio.StreamReader(limit=MAX_INPUT + 1)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    def write(raw):
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
    try:
        await run(reader, write)
    finally:
        transport.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        # No diagnostics/prompt/provider content crosses stdout or logs as an
        # apparent completion. The supervisor maps nonzero exit to run failure.
        sys.exit(1)
