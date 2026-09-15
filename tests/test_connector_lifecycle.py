"""Real, credential-free subprocess checks, not proof of an OS sandbox.

Use synthetic Python children only. No personal accounts, DB or providers are
accessed. Keep these tests in ordinary CI so cancellation/timeout regressions
cannot hide behind optional integration-test flags.
"""
import asyncio
import os
from pathlib import Path
import signal
import sys
from unittest.mock import patch

import pytest

from autonomy import connector

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group lifecycle')


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


def running(pid):
    try:
        # Killed grandchildren may remain zombies until the host reaps them.
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z'
    except FileNotFoundError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'invalid', 'error', 'overflow', 'timeout', 'cancel'])
async def test_connector_reaps_its_process_group(tmp_path, outcome):
    pid_file = tmp_path / 'pids'
    script = tmp_path / 'synthetic.py'
    script.write_text('''import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(20)'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
mode = sys.argv[2]
if mode in ('timeout', 'cancel'):
    time.sleep(20)
elif mode == 'invalid':
    print('not json', flush=True)
elif mode == 'error':
    sys.exit(1)
elif mode == 'overflow':
    print('x' * 150000, flush=True)
else:
    print('{"ok": true}', flush=True)
''')
    task = None
    # A sentinel outside the connector group must survive cleanup.
    sentinel = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c',
        'import time; time.sleep(20)', env={}, start_new_session=True)
    try:
        with patch.object(connector, 'build_argv', return_value=[sys.executable, '-I', str(script), str(pid_file), outcome]), \
             patch.object(connector, 'environment', return_value={}), \
             patch.object(connector, 'CONNECTOR_TIMEOUT', .5 if outcome == 'timeout' else 5), \
             patch('tools._auth_token.create_user_auth_token', return_value='synthetic'):
            task = asyncio.create_task(connector.personal('gmail', [], 'test@example.test'))
            await wait_for(pid_file.exists)
            import json
            parent, child = json.loads(pid_file.read_text())
            if outcome == 'cancel':
                task.cancel()
            if outcome == 'success':
                assert await task == {'ok': True}
            else:
                error = {'timeout': TimeoutError, 'cancel': asyncio.CancelledError}.get(outcome, ValueError)
                with pytest.raises(error):
                    await task
            await wait_for(lambda: not running(parent) and not running(child))
            assert sentinel.returncode is None
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pid_file.exists():
            import json
            parent, child = json.loads(pid_file.read_text())
            # Limit emergency test cleanup to our synthetic group's known ID.
            try:
                os.killpg(parent, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if sentinel.returncode is None:
            sentinel.kill()
        await sentinel.wait()


@pytest.mark.asyncio
async def test_real_child_receives_only_filtered_environment(tmp_path):
    script = tmp_path / 'inspect.py'
    script.write_text('import os, json; print(json.dumps(dict(os.environ)))')
    with patch.dict(os.environ, {'AWS_SECRET_ACCESS_KEY': 'synthetic-not-real',
                                 'GITHUB_API_KEY': 'synthetic-not-real',
                                 'ANTHROPIC_API_KEY': 'synthetic-not-real',
                                 'HTTP_PROXY': 'http://invalid.test',
                                 'PYTHONPATH': '/not/a/module/path'}, clear=True), \
         patch.object(connector, 'build_argv', return_value=[sys.executable, '-I', str(script)]), \
         patch('tools._auth_token.create_user_auth_token', return_value='synthetic'):
        result = await connector.personal('gmail', [], 'test@example.test')
    assert result['PYTHON_DOTENV_DISABLED'] == '1'
    assert not set(result) & {'AWS_SECRET_ACCESS_KEY', 'GITHUB_API_KEY', 'ANTHROPIC_API_KEY', 'HTTP_PROXY', 'PYTHONPATH'}
