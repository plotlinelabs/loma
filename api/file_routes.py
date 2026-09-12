"""Compatibility entry point for the single ownership-checked file handler."""

from aiohttp import web


async def serve_file(request: web.Request) -> web.StreamResponse:
    from api.routes import handle_serve_file
    return await handle_serve_file(request)


def setup_file_routes(app: web.Application):
    # Both app.py and setup_api_routes call this during startup.
    path = "/api/files/{file_id}"
    if any(resource.canonical == path for resource in app.router.resources()):
        return
    app.router.add_get(path, serve_file)
