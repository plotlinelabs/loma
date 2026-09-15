"""Fixed personal CLI bridge. Environment hygiene, not an OS security sandbox.

No inherited cloud/model/proxy credentials; no shell, user-provided paths or
executables. The existing Google helper still requires DB/OAuth authority, so
legacy runtimes sharing this OS user remain outside this protection boundary.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

KEYS = ('OBSERVABILITY_MONGODB_URI', 'OBSERVABILITY_DB_NAME', 'OAUTH_ENCRYPTION_KEY',
        'GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET')
MAX_OUTPUT = 128000


def environment():
    env = {k: os.environ[k] for k in KEYS if k in os.environ}
    # dotenv must not reload the application's full credentials in the child.
    env.update(PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', LANG='C.UTF-8')
    return env


async def gmail(command, owner):
    from tools._auth_token import create_user_auth_token
    argv = [sys.executable, '-I', str(Path(__file__).resolve().parents[1] / 'tools/gmail.py'),
            '--auth-token', create_user_auth_token(owner), *command, '--user-email', owner]
    # Private disposable cwd prevents output/cache mixing across principals.
    with tempfile.TemporaryDirectory(prefix='loma-connector-') as cwd:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=environment(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_OUTPUT)
        try:
            async with asyncio.timeout(45):
                chunks, size = [], 0
                while chunk := await proc.stdout.read(8192):
                    size += len(chunk)
                    if size > MAX_OUTPUT:
                        raise ValueError('Connector response exceeded the safe size limit')
                    chunks.append(chunk)
                await proc.wait()
            if proc.returncode:
                raise ValueError('Personal Gmail action failed. Check your Google connection.')
            value = json.loads(b''.join(chunks))
            if not isinstance(value, dict) or value.get('error'):
                raise ValueError('Personal Gmail action returned an invalid result')
            return value
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
