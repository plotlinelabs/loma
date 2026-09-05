"""WebSocket-based terminal — spawns a PTY and bridges it to the browser via xterm.js."""

import asyncio
import fcntl
import logging
import os
import pty
import secrets
import signal
import shutil
from pathlib import Path
import struct
import termios
import time

from aiohttp import web, WSMsgType

from api.auth_helpers import get_user_email, require_maintainer_or_above


logger = logging.getLogger(__name__)

# One-time tokens: token -> (expiry timestamp, authenticated owner)
_terminal_tokens: dict[str, tuple[float, str]] = {}
TOKEN_TTL = 30  # seconds

_login_tokens: dict[str, dict] = {}


def register_login_terminal(email: str, provider: str, config_dir: Path) -> str:
    """Trusted login-only PTY, never a backend shell or model invocation."""
    if not email or Path(email).name != email or email in {".", ".."}:
        raise web.HTTPBadRequest(text="Invalid account identity")
    commands = {"claude": ["claude", "auth", "login"],
                "codex": ["codex", "login", "--device-auth"]}
    if provider not in commands:
        raise web.HTTPBadRequest(text="Unknown login provider")
    if config_dir.is_symlink():
        raise web.HTTPBadRequest(text="Invalid account directory")
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    executable = shutil.which(provider)
    if not executable:
        raise web.HTTPServiceUnavailable(text="Login client unavailable")
    command = [executable, *commands[provider][1:]]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(config_dir),
           "TERM": "xterm-256color",
           "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME": str(config_dir)}
    now = time.time()
    for old in [key for key, value in _login_tokens.items() if value["expiry"] < now]:
        _login_tokens.pop(old, None)
    token = secrets.token_urlsafe(32)
    _login_tokens[token] = {"expiry": now + TOKEN_TTL, "email": email,
                            "argv": command, "env": env, "cwd": str(config_dir)}
    return token


async def handle_terminal_token(request: web.Request) -> web.Response:
    """POST /api/terminal/token — issue a one-time token for WebSocket auth."""
    require_maintainer_or_above(request)
    if not get_user_email(request):
        raise web.HTTPUnauthorized()
    # Clean expired tokens
    now = time.time()
    expired = [t for t, (exp, _) in _terminal_tokens.items() if exp < now]
    for t in expired:
        _terminal_tokens.pop(t, None)

    token = secrets.token_urlsafe(32)
    _terminal_tokens[token] = (now + TOKEN_TTL, get_user_email(request))
    return web.json_response({"token": token})


async def handle_terminal_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /api/terminal/ws?token=... — WebSocket endpoint that spawns a PTY shell."""
    token = request.query.get("token", "")
    user_email = get_user_email(request)
    login = _login_tokens.pop(token, None)
    if login is not None:
        if login["expiry"] < time.time() or login["email"] != user_email:
            return web.json_response({"error": "Invalid or expired token"}, status=403)
    else:
        require_maintainer_or_above(request)
        grant = _terminal_tokens.pop(token, None)
        if not grant or grant[0] < time.time() or not user_email or grant[1] != user_email:
            return web.json_response({"error": "Invalid or expired token"}, status=403)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Terminals go through the same worker boundary as agent runs: a fresh
    # private workspace and a scrubbed allowlist environment. The backend
    # env (DB URIs, encryption keys, provider/API tokens) is NEVER inherited.
    from broker import worker as worker_mod

    if login is not None:
        # Fixed vendor authentication command. No shell interpolation, backend
        # environment inheritance, model prompt or post-login interactive shell.
        workspace = None
        env, command, cwd = login["env"], login["argv"], login["cwd"]
        def child_setup():
            os.umask(0o077)
    else:
        workspace = worker_mod.create_workspace(prefix="terminal")
        cwd = str(workspace)
        env = worker_mod.build_worker_env(workspace)
        command = ["/bin/bash", "--noprofile", "--norc", "-i"]
        from broker import sandbox
        if sandbox.enabled():
            command = sandbox.prepare(command, workspace, env)
            env = {'PATH': '/usr/local/bin:/usr/bin:/bin'}
            def child_setup():
                os.umask(0o077)
        else:
            if worker_mod.bwrap_available():
                command = worker_mod.build_bwrap_argv(command, workspace)
            child_setup = worker_mod.worker_preexec_fn(setsid=False, workspace=workspace)
    pid, fd = pty.fork()

    if pid == 0:
        # Child process — apply worker limits, confine to the workspace,
        # then exec into bash. Never fall back to an unconfined shell.
        try:
            child_setup()
            os.chdir(cwd)
            os.execvpe(command[0], command, env)
        except Exception:
            pass
        os._exit(1)

    # Parent — bridge the PTY fd and the WebSocket
    loop = asyncio.get_event_loop()

    # Make the PTY fd non-blocking
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    closed = False

    async def expire_terminal():
        await asyncio.sleep(600 if login else 3600)
        await ws.close()

    expiry_task = asyncio.create_task(expire_terminal())

    def on_pty_readable():
        nonlocal closed
        if closed:
            return
        try:
            data = os.read(fd, 65536)
            if data:
                asyncio.ensure_future(ws.send_bytes(data))
            else:
                asyncio.ensure_future(ws.close())
        except OSError:
            asyncio.ensure_future(ws.close())

    loop.add_reader(fd, on_pty_readable)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                if msg.data.startswith("\x01RESIZE:"):
                    try:
                        parts = msg.data[8:].split(",")
                        cols, rows = int(parts[0]), int(parts[1])
                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                    except (ValueError, IndexError, OSError):
                        pass
                else:
                    os.write(fd, msg.data.encode())
            elif msg.type == WSMsgType.BINARY:
                os.write(fd, msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    except Exception:
        logger.exception("Terminal WebSocket error")
    finally:
        closed = True
        expiry_task.cancel()
        loop.remove_reader(fd)
        os.close(fd)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            from broker import worker as worker_mod
            if workspace is not None:
                worker_mod.cleanup_workspace(workspace)
        except Exception:
            logger.warning("Failed to clean terminal workspace %s", workspace)

    return ws


def setup_terminal_routes(app: web.Application):
    app.router.add_post("/api/terminal/token", handle_terminal_token)
    app.router.add_get("/api/terminal/ws", handle_terminal_ws)
