"""Worker-side loopback HTTP bridge, with no upstream network or credentials.

Runtime adapters can point their model base URL at this disposable server. Only
three exact POST paths are supported. Request headers, URL query parameters,
credentials, redirects and arbitrary HTTP targets never enter broker frames.
All authority remains in the backend ModelRelay. This is not a forward proxy.
"""
import asyncio
import base64
import binascii

from aiohttp import ClientConnectionError, web

from isolation.protocol import MAX_FRAME

PATHS = {'responses': '/v1/responses', 'messages': '/v1/messages', 'chat': '/v1/chat/completions'}


class ModelBridge:
    def __init__(self, protocol, rpc, *, native_messages=False):
        if (protocol not in PATHS or not callable(rpc) or type(native_messages) is not bool
                or (native_messages and protocol != 'messages')):
            raise ValueError('A supported protocol and broker RPC are required')
        self.protocol, self.rpc = protocol, rpc
        self.native_messages = native_messages and protocol == "messages"
        self.runner = None
        self.origin = None
        self.busy = False
        self.idle = asyncio.Event()
        self.idle.set()
        self.waiter = None
        self.failed = False
        self.tasks = set()

    async def serve(self):
        if self.runner is not None:
            raise RuntimeError('Model bridge already started')
        app = web.Application(client_max_size=MAX_FRAME - 1024)
        app.router.add_post(PATHS[self.protocol], self.handle)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
        await self.runner.setup()
        try:
            # This listener exists only inside the disposable worker's private
            # network namespace. Never bind to all interfaces or publish a port.
            site = web.TCPSite(self.runner, '127.0.0.1', 0)
            await site.start()
            self.origin = 'http://127.0.0.1:' + str(self.runner.addresses[0][1])
            return self.origin
        except BaseException:
            await self.close()
            raise

    async def handle(self, request):
        if ((request.query_string and not (self.native_messages and request.query_string == 'beta=true'))
                or request.headers.get('Content-Encoding', 'identity') != 'identity'):
            raise web.HTTPBadRequest(text='Unsupported model request')
        if self.failed:
            raise web.HTTPBadRequest(text="Model gateway failed; start a new authorized run")
        if self.busy:
            # Native clients start their next tool turn after the terminal SSE
            # event, before the backend finishes durable EOF settlement. Allow
            # one bounded waiter, never overlapping provider calls or retries.
            if self.waiter is not None:
                raise web.HTTPConflict(text='Only one model request may wait')
            self.waiter = asyncio.current_task()
            try:
                await asyncio.wait_for(self.idle.wait(), 10)
            except TimeoutError:
                raise web.HTTPConflict(text='Previous model request has not settled') from None
            finally:
                self.waiter = None
            if self.failed:
                raise web.HTTPBadRequest(text='Model gateway failed; start a new authorized run')
            if self.busy:
                raise web.HTTPConflict(text='Only one model request may be active')
        self.busy = True
        self.idle.clear()
        task = asyncio.current_task()
        self.tasks.add(task)
        stream_id = None
        response = None
        try:
            try:
                body = await request.json()
            except (ValueError, UnicodeError):
                raise web.HTTPBadRequest(text='Invalid JSON') from None
            started = await self.rpc('model.start', {'body': body})
            if (not isinstance(started, dict) or not isinstance(started.get('stream_id'), str)
                    or started.get('content_type') != 'text/event-stream'):
                raise RuntimeError('Model stream unavailable')
            stream_id = started['stream_id']
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream',
                                                    'Cache-Control': 'no-store'})
            await response.prepare(request)
            while True:
                chunk = await self.rpc('model.read', {'stream_id': stream_id})
                if (not isinstance(chunk, dict) or set(chunk) != {'data', 'eof'}
                        or not isinstance(chunk['data'], str) or type(chunk['eof']) is not bool
                        or len(chunk['data']) > 45000):
                    raise RuntimeError('Invalid model chunk')
                try:
                    data = base64.b64decode(chunk['data'], validate=True)
                except (ValueError, binascii.Error):
                    raise RuntimeError('Invalid model bytes') from None
                if chunk['eof']:
                    if data:
                        raise RuntimeError('Invalid model end marker')
                    stream_id = None  # backend has already closed the stream
                    try:
                        await response.write_eof()
                    except (ConnectionResetError, ClientConnectionError):
                        # Native clients may close HTTP after the terminal SSE
                        # event and immediately start their next tool turn.
                        # The backend has already drained/settled this stream;
                        # failure to write the HTTP trailer is not a lost model
                        # outcome. Earlier disconnects still poison the bridge.
                        pass
                    return response
                if not data:
                    raise RuntimeError('Empty model chunk')
                await response.write(data)
        except web.HTTPException:
            raise
        except asyncio.CancelledError:
            self.failed = True
            raise
        except Exception:
            self.failed = True
            # Provider or broker error text can include privileged diagnostics.
            # After headers have gone out, abort the HTTP connection rather than
            # produce an apparently successful, truncated SSE response.
            if response is not None and response.prepared:
                if request.transport is not None:
                    request.transport.close()
                return response
            raise web.HTTPBadRequest(text='Model gateway unavailable; not retried') from None
        finally:
            try:
                if stream_id is not None:
                    # Bounded cleanup. Full-run teardown also closes ModelRelay;
                    # this HTTP request cannot keep privileged streams alive.
                    async with asyncio.timeout(5):
                        await self.rpc('model.close', {'stream_id': stream_id})
            except Exception:
                pass
            finally:
                self.busy = False
                self.idle.set()
                self.tasks.discard(task)

    async def drain(self):
        """Finish the final broker exchange before emitting worker completion."""
        await asyncio.wait_for(self.idle.wait(), 10)
        if self.failed or self.waiter is not None:
            raise RuntimeError('Model exchange did not settle')

    async def close(self):
        self.failed = True
        self.idle.set()
        current = asyncio.current_task()
        active = self.tasks | ({self.waiter} if self.waiter else set())
        tasks = [t for t in active if t != current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.runner is not None:
            runner, self.runner = self.runner, None
            self.origin = None
            await runner.cleanup()
