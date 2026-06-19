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


logger = logging.getLogger(__name__)

# One-time tokens: token -> expiry timestamp
_terminal_tokens: dict[str, float] = {}
TOKEN_TTL = 30  # seconds


async def handle_terminal_token(request: web.Request) -> web.Response:
    """POST /api/terminal/token — issue a one-time token for WebSocket auth."""
    # Clean expired tokens
    now = time.time()
    expired = [t for t, exp in _terminal_tokens.items() if exp < now]
    for t in expired:
        _terminal_tokens.pop(t, None)

    token = secrets.token_urlsafe(32)
    _terminal_tokens[token] = now + TOKEN_TTL
    return web.json_response({"token": token})


async def handle_terminal_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /api/terminal/ws?token=... — WebSocket endpoint that spawns a PTY shell."""
    # Validate one-time token
    token = request.query.get("token", "")
    expiry = _terminal_tokens.pop(token, None)
    if not expiry or expiry < time.time():
        return web.json_response({"error": "Invalid or expired token"}, status=403)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Spawn a PTY with bash
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("CLAUDECODE", None)
    pid, fd = pty.fork()

    if pid == 0:
        # Child process — exec into bash
        os.execvpe("/bin/bash", ["/bin/bash", "--login"], env)
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

    return ws


def setup_terminal_routes(app: web.Application):
    app.router.add_post("/api/terminal/token", handle_terminal_token)
    app.router.add_get("/api/terminal/ws", handle_terminal_ws)
