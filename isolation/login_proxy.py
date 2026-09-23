"""Bounded CONNECT-only TLS passthrough. Never log tokens, headers or traffic.

Resolve once, reject non-global addresses, connect to that exact IP: DNS
rebinding cannot turn an allowed hostname into access to LAN/metadata services.
TLS remains end-to-end between official Claude Code and Anthropic.
"""
import asyncio
import ipaddress
import socket

HOSTS = frozenset({'claude.ai', 'platform.claude.com', 'console.anthropic.com', 'api.anthropic.com'})


def destination(raw):
    lines = raw.decode('ascii').split('\r\n')
    parts = lines[0].split(' ')
    if len(parts) != 3 or parts[0] != 'CONNECT' or parts[2] != 'HTTP/1.1':
        raise ValueError('CONNECT required')
    host, sep, port = parts[1].partition(':')
    if not sep or host not in HOSTS or port != '443':
        raise ValueError('Destination forbidden')
    return host


async def connect(host):
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise ValueError('Non-public address')
    # No second DNS lookup: numeric address and explicit family only.
    info = infos[0]
    return await asyncio.open_connection(info[4][0], 443, family=info[0])


async def pipe(reader, writer):
    total = 0
    while data := await reader.read(16384):
        total += len(data)
        if total > 8 * 1024 * 1024:
            raise ValueError('Tunnel budget exceeded')
        writer.write(data)
        await writer.drain()


class Proxy:
    def __init__(self):
        self.active = 0

    async def handle(self, reader, writer):
        upstream = None
        tasks = []
        if self.active >= 64:
            writer.close()
            return
        self.active += 1
        try:
            async with asyncio.timeout(630):
                raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
                host = destination(raw)
                remote, upstream = await asyncio.wait_for(connect(host), 15)
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                tasks = [asyncio.create_task(pipe(reader, upstream)), asyncio.create_task(pipe(remote, writer))]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (ValueError, OSError, TimeoutError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass  # close fail-closed, with no reflection/logging of request content
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
            self.active -= 1


async def main():
    server = await asyncio.start_server(Proxy().handle, '0.0.0.0', 3128, limit=8192)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
