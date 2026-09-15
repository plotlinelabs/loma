"""Fixed personal CLI bridge. Environment hygiene, not an OS security sandbox.

No inherited cloud/model/proxy credentials; no shell, user-provided paths or
executables. Children run in their own session with conservative resource
limits, and an operator may prefix an OS sandbox via LOMA_CONNECTOR_SANDBOX
(for example a bubblewrap/nsjail argv prefix) on hosts that support it.
The existing Google/Slack helpers still require DB/OAuth authority, so legacy
runtimes sharing this OS user remain outside this protection boundary, and
sandbox-prefix isolation is only as strong as the host verifies it to be.
"""
import asyncio
import json
import os
import resource
import shlex
import signal
import sys
import tempfile
from pathlib import Path

KEYS = ('OBSERVABILITY_MONGODB_URI', 'OBSERVABILITY_DB_NAME', 'OAUTH_ENCRYPTION_KEY',
        'GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET')
MAX_OUTPUT = 128000
CONNECTOR_TIMEOUT = 45
# Exact first-party scripts only. Argument order matches each CLI's parser.
TOOLS = {'gmail': 'tools/gmail.py', 'slack': 'tools/slack_user.py', 'calendar': 'tools/google_calendar.py'}


def _limits():
    """Belt-and-braces child limits; failure of any one limit is not fatal."""
    for key, value in (('RLIMIT_CPU', 60), ('RLIMIT_AS', 1024 * 1024 * 1024),
                       ('RLIMIT_FSIZE', 16 * 1024 * 1024), ('RLIMIT_CORE', 0),
                       ('RLIMIT_NOFILE', 256)):
        try:
            resource.setrlimit(getattr(resource, key), (value, value))
        except (ValueError, OSError):
            pass


def environment():
    env = {k: os.environ[k] for k in KEYS if k in os.environ}
    # dotenv must not reload the application's full credentials in the child.
    env.update(PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', LANG='C.UTF-8')
    return env


def sandbox_prefix():
    """Optional operator-configured argv prefix; never model or job supplied."""
    return shlex.split(os.getenv('LOMA_CONNECTOR_SANDBOX', ''))


def build_argv(tool, command, owner, token):
    script = str(Path(__file__).resolve().parents[1] / TOOLS[tool])
    interpreter = [sys.executable, '-I', script]
    if tool == 'slack':
        # slack_user.py accepts global args anywhere before the subcommand.
        argv = interpreter + ['--auth-token', token, '--user-email', owner, *command]
    else:
        # gmail.py/google_calendar.py: subcommand first, --user-email per-sub.
        argv = interpreter + ['--auth-token', token, *command, '--user-email', owner]
    return sandbox_prefix() + argv


async def personal(tool, command, owner):
    if tool not in TOOLS:
        raise ValueError('Unknown personal tool')
    from tools._auth_token import create_user_auth_token
    argv = build_argv(tool, command, owner, create_user_auth_token(owner))
    # Private disposable cwd prevents output/cache mixing across principals.
    with tempfile.TemporaryDirectory(prefix='loma-connector-') as cwd:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=environment(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_OUTPUT,
            start_new_session=True, preexec_fn=_limits)
        try:
            async with asyncio.timeout(CONNECTOR_TIMEOUT):
                chunks, size = [], 0
                while chunk := await proc.stdout.read(8192):
                    size += len(chunk)
                    if size > MAX_OUTPUT:
                        raise ValueError('Connector response exceeded the safe size limit')
                    chunks.append(chunk)
                await proc.wait()
            if proc.returncode:
                raise ValueError('Personal connector action failed. Check your connected accounts.')
            value = json.loads(b''.join(chunks))
            if not isinstance(value, dict) or value.get('error'):
                raise ValueError('Personal connector action returned an invalid result')
            return value
        finally:
            # start_new_session makes this PID the connector's process-group ID.
            # Clean up descendants even when the direct child already exited:
            # wrappers may otherwise outlive a successful/failed connector, and
            # inherited stdout can keep read() open until the timeout. Never use
            # process-name matching here; other runs share this host.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await asyncio.shield(proc.wait())


async def gmail(command, owner):
    return await personal('gmail', command, owner)
