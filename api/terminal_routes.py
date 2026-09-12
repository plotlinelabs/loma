"""Host terminals are disabled until isolated, credential-free workers exist."""

from aiohttp import web


async def handle_terminal_token(request: web.Request) -> web.Response:
    """Never issue host-shell tokens, including to administrators."""
    return web.json_response({"error": "Host terminals are disabled"}, status=403)


async def handle_terminal_ws(request: web.Request) -> web.Response:
    """Reject before upgrading, including tokens issued by an older worker."""
    return web.json_response({"error": "Host terminals are disabled"}, status=403)


def setup_terminal_routes(app: web.Application):
    app.router.add_post("/api/terminal/token", handle_terminal_token)
    app.router.add_get("/api/terminal/ws", handle_terminal_ws)
