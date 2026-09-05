"""WebSocket-based terminal — spawns a PTY and bridges it to the browser via xterm.js."""

import asyncio
import fcntl
import logging
import os
import pty
import secrets
import signal
import struct
import termios
import time

from aiohttp import web, WSMsgType

from api.auth_helpers import get_user_email, require_maintainer_or_above


logger = logging.getLogger(__name__)

# One-time tokens: token -> (expiry timestamp, authenticated owner)
_terminal_tokens: dict[str, tuple[float, str]] = {}
TOKEN_TTL = 30  # seconds


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
    require_maintainer_or_above(request)
    # Validate one-time token
    token = request.query.get("token", "")
    grant = _terminal_tokens.pop(token, None)
    user_email = get_user_email(request)
    if not grant or grant[0] < time.time() or not user_email or grant[1] != user_email:
        return web.json_response({"error": "Invalid or expired token"}, status=403)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Terminals go through the same worker boundary as agent runs: a fresh
    # private workspace and a scrubbed allowlist environment. The backend
    # env (DB URIs, encryption keys, provider/API tokens) is NEVER inherited.
    from broker import worker as worker_mod

    workspace = worker_mod.create_workspace(prefix="terminal")
    env = worker_mod.build_worker_env(workspace)
    command = ["/bin/bash", "--noprofile", "--norc", "-i"]
    if worker_mod.bwrap_available():
        command = worker_mod.build_bwrap_argv(command, workspace)
    child_setup = worker_mod.worker_preexec_fn(setsid=False)
    pid, fd = pty.fork()

    if pid == 0:
        # Child process — apply worker limits, confine to the workspace,
        # then exec into bash. Never fall back to an unconfined shell.
        try:
            child_setup()
            os.chdir(str(workspace))
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
            worker_mod.cleanup_workspace(workspace)
        except Exception:
            logger.warning("Failed to clean terminal workspace %s", workspace)

    return ws


def setup_terminal_routes(app: web.Application):
    app.router.add_post("/api/terminal/token", handle_terminal_token)
    app.router.add_get("/api/terminal/ws", handle_terminal_ws)
