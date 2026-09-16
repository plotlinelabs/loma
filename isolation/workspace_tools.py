"""Worker-only editing/execution, never a trusted-backend tool adapter.

These tools are useful only inside the disposable gVisor container. The shell
is intentionally untrusted and is NOT a sandbox; the supervisor supplies the
network/filesystem/process boundary. Never register this class in ToolGateway.
"""
import asyncio
import os
from pathlib import Path
import signal
import stat

from isolation.workspace import Workspace

TOOLS = frozenset({'workspace.list', 'workspace.read', 'workspace.write', 'workspace.exec', 'workspace.publish', 'workspace.import'})
MAX_TEXT = 256 * 1024


class WorkspaceTools:
    def __init__(self, root, rpc, allowed):
        self.root = Path(root)
        self.workspace = Workspace(self.root, rpc)
        self.rpc, self.allowed = rpc, frozenset(allowed)
        self.lock = asyncio.Lock()

    async def __call__(self, tool, arguments):
        if tool not in TOOLS:
            return await self.rpc(tool, arguments)
        if tool not in self.allowed or not isinstance(arguments, dict):
            raise ValueError('Workspace tool is not allowed')
        async with self.lock:
            schemas = {'workspace.list': set(), 'workspace.read': {'path'},
                       'workspace.write': {'path', 'content'}, 'workspace.exec': {'command'},
                       'workspace.publish': {'path'}, 'workspace.import': {'artifact_id'}}
            if set(arguments) != schemas[tool]:
                raise ValueError('Invalid workspace arguments')
            if tool == 'workspace.list':
                files = []
                # Do not follow symlink directories or enumerate an unbounded
                # output tree created by an earlier command.
                for visited, (directory, dirs, names) in enumerate(os.walk(self.root, followlinks=False)):
                    if visited >= 200:
                        return {'files': files, 'truncated': True}
                    dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())[:200]
                    for filename in sorted(names):
                        path = Path(directory) / filename
                        if stat.S_ISREG(path.lstat().st_mode):
                            files.append(str(path.relative_to(self.root)))
                            if len(files) >= 200:
                                return {'files': files, 'truncated': True}
                return {'files': files, 'truncated': False}
            if tool == 'workspace.publish':
                return await self.workspace.publish(arguments['path'])
            if tool == 'workspace.import':
                meta = await self.rpc('artifacts.describe', arguments)
                return {'paths': await self.workspace.stage([meta])}
            if tool == 'workspace.exec':
                return await self._exec(arguments['command'])
            parent, filename = self.workspace._parent(arguments['path'])
            try:
                if tool == 'workspace.write':
                    content = arguments['content']
                    if not isinstance(content, str) or len(content.encode()) > MAX_TEXT:
                        raise ValueError('Workspace text exceeds the limit')
                    flags = os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW
                    fd = os.open(filename, flags, 0o600, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as handle:
                        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                            raise ValueError('A regular file is required')
                        handle.truncate(0)
                        handle.write(content.encode())
                    return {'path': arguments['path'], 'size': len(content.encode())}
                fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise ValueError('A regular file is required')
                    raw = handle.read(MAX_TEXT + 1)
                return {'path': arguments['path'], 'content': raw[:MAX_TEXT].decode('utf-8', errors='replace'),
                        'truncated': len(raw) > MAX_TEXT}
            finally:
                os.close(parent)

    async def _exec(self, command):
        if not isinstance(command, str) or not command.strip() or len(command.encode()) > 16384 or '\x00' in command:
            raise ValueError('Invalid workspace command')
        # Only this disposable filesystem is accessible under the supervisor.
        # Do not inherit provider credentials, proxies, runtime config or hooks.
        process = await asyncio.create_subprocess_exec('/bin/sh', '-c', command,
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(self.root),
                                'TMPDIR': str(self.root), 'LANG': 'C.UTF-8'},
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        output = bytearray()
        timed_out = truncated = False
        try:
            async with asyncio.timeout(120):
                while chunk := await process.stdout.read(8192):
                    output.extend(chunk)
                    if len(output) > 65536:
                        truncated = True
                        break
                if not truncated:
                    await process.wait()
        except TimeoutError:
            timed_out = True
        finally:
            # Always kill the whole group, including descendants whose parent
            # exited. Cancellation never leaves a detached workspace process.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return {'exit_code': process.returncode, 'output': output[:65536].decode('utf-8', errors='replace'),
                'truncated': truncated, 'timed_out': timed_out}

    def close(self):
        self.workspace.close()
