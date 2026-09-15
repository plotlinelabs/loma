"""Worker-local, stateless MCP facade for the fixed gateway catalog.

This loopback listener is not an authorization boundary. The backend gateway
revalidates every call. No remote URLs, account selection, command execution,
resource subscriptions or worker-chosen tools can be registered here.
"""
import asyncio
import json

from aiohttp import web

from isolation.codex_worker import tool_definitions
from isolation.protocol import MAX_FRAME


class MCPBridge:
    def __init__(self, tools, rpc):
        definitions, self.names = tool_definitions(tools)
        self.tools = definitions
        self.rpc = rpc
        self.runner = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.seen = set()

    async def serve(self):
        app = web.Application(client_max_size=MAX_FRAME - 1024)
        app.router.add_post('/mcp', self.handle)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
        await self.runner.setup()
        await web.TCPSite(self.runner, '127.0.0.1', 0).start()
        return 'http://127.0.0.1:' + str(self.runner.addresses[0][1]) + '/mcp'

    async def handle(self, request):
        if request.query_string or request.headers.get('Origin'):
            raise web.HTTPBadRequest(text='Unsupported MCP request')
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text='Invalid MCP JSON') from None
        if (not isinstance(value, dict) or value.get('jsonrpc') != '2.0'
                or set(value) - {'jsonrpc', 'id', 'method', 'params'}):
            raise web.HTTPBadRequest(text='Invalid MCP frame')
        method, params = value.get('method'), value.get('params', {})
        if not isinstance(params, dict):
            raise web.HTTPBadRequest(text='Invalid MCP parameters')
        if 'id' not in value:
            if method != 'notifications/initialized':
                raise web.HTTPBadRequest(text='Unsupported MCP notification')
            return web.Response(status=202)
        request_id = value['id']
        if (type(request_id) not in (str, int) or len(str(request_id)) > 64):
            raise web.HTTPBadRequest(text='Invalid MCP request ID')
        if method == 'initialize':
            result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'gateway', 'version': '1'}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list' and not params:
            result = {'tools': self.tools}
        elif method == 'tools/call':
            if (set(params) - {'name', 'arguments', '_meta'} or not isinstance(params.get('name'), str)
                    or params['name'] not in self.names
                    or not isinstance(params.get('arguments'), dict)):
                raise web.HTTPBadRequest(text='Unsupported gateway tool')
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                async with self.lock:
                    # Even a client retry after a lost response cannot repeat a
                    # privileged dispatch. New IDs still require backend policy.
                    key = (type(request_id), request_id)
                    if key in self.seen or len(self.seen) >= 500:
                        raise web.HTTPConflict(text='Repeated or excessive tool call')
                    self.seen.add(key)
                    output = await self.rpc(self.names[params['name']], params['arguments'])
                    encoded = json.dumps(output, allow_nan=False)
                    if len(encoded.encode()) > MAX_FRAME // 2:
                        raise ValueError('Tool output is too large')
                    result = {'content': [{'type': 'text', 'text': encoded}],
                              'isError': isinstance(output, dict) and 'error' in output}
            except asyncio.CancelledError:
                raise
            except web.HTTPException:
                raise
            except Exception:
                result = {'content': [{'type': 'text', 'text': 'Gateway request failed; not replayed'}],
                          'isError': True}
            finally:
                self.tasks.discard(task)
        else:
            return web.json_response({'jsonrpc': '2.0', 'id': request_id,
                'error': {'code': -32601, 'message': 'Method unavailable'}})
        return web.json_response({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.runner is not None:
            runner, self.runner = self.runner, None
            await runner.cleanup()
