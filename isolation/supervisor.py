"""Trusted, dedicated-host supervisor for disposable networkless workers.

The Docker socket belongs ONLY to this service. Worker containers receive no
host mounts, credentials, network or supervisor token. All model/provider/tool
I/O must use the framed broker protocol. Do not deploy beside the production DB.
"""
import asyncio
import hmac
import json
import os
import re
import ssl
import uuid
from dataclasses import dataclass, field

from aiohttp import web, WSMsgType
from isolation.protocol import MAX_FRAME, MAX_INPUT, ProtocolError, decode, response_frame, worker_frame

LABEL = 'io.loma.isolated-worker=1'


@dataclass(frozen=True)
class Settings:
    image: str
    token: str = field(repr=False)
    runtime: str = 'runsc'
    max_seconds: int = 3600
    max_workers: int = 4

    def __post_init__(self):
        # Operator-only immutable image, no request-supplied executable/env/flags.
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[a-f0-9]{64}', self.image):
            raise ValueError('Worker image must be pinned to a sha256 digest')
        if self.runtime != 'runsc':
            raise ValueError('A verified runsc runtime is required; no runc fallback')
        if len(self.token) < 32:
            raise ValueError('Supervisor token must contain at least 32 characters')
        if not 1 <= self.max_workers <= 32 or not 30 <= self.max_seconds <= 14400:
            raise ValueError('Invalid worker limits')

    @classmethod
    def from_env(cls):
        from pathlib import Path
        token_file = os.environ.get('LOMA_WORKER_CONTROL_TOKEN_FILE')
        token = Path(token_file).read_text().strip() if token_file else os.environ['LOMA_WORKER_CONTROL_TOKEN']
        return cls(os.environ['LOMA_WORKER_IMAGE'], token,
                   max_seconds=int(os.getenv('LOMA_WORKER_MAX_SECONDS', '3600')),
                   max_workers=int(os.getenv('LOMA_WORKER_MAX_CONCURRENCY', '4')))


def docker_command(settings, name):
    if not re.fullmatch(r'loma-worker-[a-f0-9]{32}', name):
        raise ValueError('Invalid container name')
    return ['docker', 'run', '--rm', '--interactive', '--name', name,
            '--label', LABEL, '--runtime', settings.runtime,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--user', '65532:65532',
            '--pids-limit', '128', '--memory', '1g', '--memory-swap', '1g',
            '--cpus', '1', '--ulimit', 'nofile=256:256', '--ulimit', 'core=0:0',
            '--log-driver', 'none',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=700',
            '--tmpfs', '/workspace:rw,nosuid,nodev,size=512m,uid=65532,gid=65532,mode=700',
            '--workdir', '/workspace', '--env', 'HOME=/workspace',
            '--env', 'PYTHON_DOTENV_DISABLED=1', settings.image]


def process_environment():
    # In particular, never inherit DOCKER_HOST/CONTEXT or arbitrary proxy values.
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/nonexistent'}


async def command(*argv):
    proc = await asyncio.create_subprocess_exec(*argv, env=process_environment(),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(15):
            out, _ = await proc.communicate()
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    if proc.returncode:
        raise RuntimeError('Worker host command failed')
    return out


class Supervisor:
    def __init__(self, settings):
        self.settings = settings
        self.active = set()
        self.unhealthy = False

    async def preflight(self, app):
        # Fail startup, do not downgrade to a normal host process or runc.
        info = json.loads(await command('docker', 'info', '--format', '{{json .Runtimes}}'))
        if self.settings.runtime not in info:
            raise RuntimeError('Required isolated runtime is unavailable')
        await command('docker', 'image', 'inspect', self.settings.image)
        # Dedicated daemon only. Clean abandoned containers after supervisor loss.
        ids = (await command('docker', 'ps', '-aq', '--filter', f'label={LABEL}')).decode().split()
        if ids:
            await command('docker', 'rm', '-f', *ids)

    async def health(self, request):
        self.authenticate(request)
        if self.unhealthy:
            raise web.HTTPServiceUnavailable(text='Worker cleanup requires operator intervention')
        return web.json_response({'protocol': 1, 'runtime': self.settings.runtime})

    def authenticate(self, request):
        expected = 'Bearer ' + self.settings.token
        if not hmac.compare_digest(request.headers.get('Authorization', '').encode(), expected.encode()):
            raise web.HTTPUnauthorized()

    def container_command(self, name):
        return docker_command(self.settings, name)

    async def remove_worker(self, name, proc):
        try:
            await command('docker', 'rm', '-f', name)
        except (RuntimeError, TimeoutError):
            try:
                ids = await command('docker', 'ps', '-aq', '--filter', f'name=^/{name}$')
                if ids.strip():
                    self.unhealthy = True
            except (RuntimeError, TimeoutError):
                self.unhealthy = True

    async def run(self, request):
        self.authenticate(request)
        if self.unhealthy or len(self.active) >= self.settings.max_workers:
            raise web.HTTPServiceUnavailable(text='Isolated worker unavailable')
        name = 'loma-worker-' + uuid.uuid4().hex
        self.active.add(name)  # no await between capacity check and reservation
        ws = web.WebSocketResponse(max_msg_size=MAX_INPUT, heartbeat=20)
        proc = None
        launched = False
        tasks = []
        try:
            await ws.prepare(request)
            async with asyncio.timeout(self.settings.max_seconds):
                first = await asyncio.wait_for(ws.receive(), 15)
                if first.type != WSMsgType.TEXT:
                    raise ProtocolError('Missing run input')
                value = decode(first.data, limit=MAX_INPUT)
                if set(value) != {'type', 'input'} or value['type'] != 'start' or not isinstance(value['input'], dict):
                    raise ProtocolError('Invalid run input')
                launched = True
                proc = await asyncio.create_subprocess_exec(*self.container_command(name),
                    env=process_environment(), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    limit=MAX_FRAME + 1)
                proc.stdin.write((json.dumps(value) + '\n').encode())
                await proc.stdin.drain()
                pending = set()
                seen = set()

                async def read_worker():
                    size = 0
                    while raw := await proc.stdout.readline():
                        size += len(raw)
                        if size > 64 * MAX_FRAME:
                            raise ProtocolError('Worker output budget exceeded')
                        frame = worker_frame(raw)
                        if frame['type'] == 'tool_request':
                            if frame['id'] in seen or len(seen) >= 500 or len(pending) >= 8:
                                raise ProtocolError('Invalid or excessive tool requests')
                            seen.add(frame['id'])
                            pending.add(frame['id'])
                        if frame['type'] == 'done':
                            # Success requires clean process exit, not a forged marker.
                            proc.stdin.close()
                            async with asyncio.timeout(10):
                                await proc.wait()
                            if proc.returncode or pending:
                                raise ProtocolError('Worker did not complete cleanly')
                            await ws.send_json(frame)
                            return
                        await ws.send_json(frame)
                    raise ProtocolError('Worker ended without a completion frame')

                async def read_backend():
                    async for msg in ws:
                        if msg.type != WSMsgType.TEXT:
                            raise ProtocolError('Invalid backend message')
                        frame = decode(msg.data)
                        encoded = response_frame(frame)
                        if frame['id'] not in pending:
                            raise ProtocolError('Unknown tool response')
                        pending.remove(frame['id'])
                        proc.stdin.write((encoded + '\n').encode())
                        await proc.stdin.drain()
                    # Returning triggers unconditional container removal below.

                tasks = [asyncio.create_task(read_worker()), asyncio.create_task(read_backend())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (ProtocolError, TimeoutError, ValueError, OSError, RuntimeError):
            if ws.prepared and not ws.closed:
                await ws.close(code=1011, message=b'Isolated worker failed; not retried')
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # docker-client death does not kill the container. Explicitly remove
            # it even after disconnect, protocol failure, deadline or cancellation.
            async def cleanup():
                try:
                    if launched:
                        await self.remove_worker(name, proc)
                except Exception:
                    self.unhealthy = True
                finally:
                    if proc is not None:
                        if proc.returncode is None:
                            proc.kill()
                        await proc.wait()
                    self.active.discard(name)
            clean_task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(clean_task)
            except asyncio.CancelledError:
                await clean_task
                raise
            if ws.prepared:
                await ws.close()
        return ws


def make_app(settings):
    supervisor = Supervisor(settings)
    app = web.Application(client_max_size=MAX_INPUT)
    app.router.add_get('/health', supervisor.health)
    app.router.add_get('/v1/run', supervisor.run)
    app.on_startup.append(supervisor.preflight)
    return app


if __name__ == '__main__':
    # TLS is mandatory on the dedicated host; firewall to backend addresses only.
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ['LOMA_WORKER_TLS_CERT'], os.environ['LOMA_WORKER_TLS_KEY'])
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.load_verify_locations(os.environ['LOMA_WORKER_CLIENT_CA'])
    web.run_app(make_app(Settings.from_env()), host='0.0.0.0', port=8443, ssl_context=tls)
