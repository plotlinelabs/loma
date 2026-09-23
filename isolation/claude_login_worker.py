"""Login-only image entrypoint. Never starts a shell or a model session."""
import json
import os
from pathlib import Path
import pty
import re
import selectors
import subprocess
import sys
import termios
import time


def emit(frame):
    print(json.dumps(frame), flush=True)


def main(home=Path('/workspace')):
    if json.loads(sys.stdin.readline()) != {'type': 'start', 'input': {'runtime': 'claude-login'}}:
        raise ValueError('Invalid login request')
    config = home / '.claude'
    config.mkdir(mode=0o700, exist_ok=True)
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO  # pasted authorization codes must never be echoed
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    env = {'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(config),
           'PATH': '/usr/local/bin:/usr/bin:/bin', 'TERM': 'dumb',
           'BROWSER': '/bin/false', 'DISABLE_AUTOUPDATER': '1'}
    proc = subprocess.Popen(['claude', 'auth', 'login'], env=env, cwd=home,
                            stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    deadline, size, attempt = time.monotonic() + 600, 0, 0
    emit({'type': 'tool_request', 'id': 'code-0', 'tool': 'login_code', 'arguments': {}})
    selector = selectors.DefaultSelector()
    selector.register(master, selectors.EVENT_READ)
    selector.register(sys.stdin, selectors.EVENT_READ)
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline:
                raise TimeoutError('Login expired')
            for key, _ in selector.select(.1):
                if key.fileobj == master:
                    try:
                        raw = os.read(master, 8192)
                    except OSError:
                        continue  # PTY EOF; poll reaps the child
                    size += len(raw)
                    if size > 65536:
                        raise ValueError('Login output exceeded budget')
                    emit({'type': 'text', 'text': raw.decode(errors='replace')})
                else:
                    raw = sys.stdin.readline(8193)
                    response = json.loads(raw)
                    if (response.get('type') != 'tool_response' or response.get('id') != f'code-{attempt}'
                            or set(response.get('result', {})) != {'code'}):
                        raise ValueError('Invalid login response')
                    code = response['result']['code']
                    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_.#~-]{1,2048}', code):
                        raise ValueError('Invalid authorization code')
                    os.write(master, (code + '\n').encode())
                    attempt += 1
                    selector.unregister(sys.stdin)
        if proc.returncode != 0:
            raise ValueError('Claude login failed')
        # Fixed files only. No CLI history, settings, MCPs or arbitrary files leave.
        files = {}
        for name in ('.claude.json', '.credentials.json'):
            path = config / name
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError('Invalid credential file')
            files[name] = json.loads(path.read_text())
        if attempt == 0:
            emit({'type': 'text', 'text': 'LOMA_LOGIN_FINISHED'})
            response = json.loads(sys.stdin.readline(8193))
            if response != {'type': 'tool_response', 'id': 'code-0', 'result': {'cancelled': True}}:
                raise ValueError('Invalid completion acknowledgement')
        emit({'type': 'tool_request', 'id': 'save', 'tool': 'save_credentials', 'arguments': files})
        response = json.loads(sys.stdin.readline(8193))
        if response != {'type': 'tool_response', 'id': 'save', 'result': {'ok': True}}:
            raise ValueError('Login was not accepted')
        emit({'type': 'done'})
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        selector.close()
        os.close(master)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No exception may print credentials or authorization codes.
        sys.exit(1)
