"""Runs INSIDE the secrets-free worker image, never in the backend.

With runsc --network=none there is no external network device. Two fixed Unix
socket bridges make only the authenticated broker/gateway reachable. No CONNECT
proxy, DNS resolver, configurable destination, or host TCP forwarding is exposed.
"""
import asyncio
import os
import signal
import sys
from urllib.parse import urlsplit


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def bridge(reader, writer, *, unix=None, port=None):
    tasks = []
    peer = None
    try:
        if unix:
            other, peer = await asyncio.wait_for(asyncio.open_unix_connection(unix), 10)
        else:
            other, peer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 10)
        tasks = [asyncio.create_task(pipe(reader, peer)), asyncio.create_task(pipe(other, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        if peer:
            peer.close()


def local_port(url):
    value = urlsplit(url)
    if (value.scheme != 'http' or value.hostname != '127.0.0.1' or not value.port
            or value.username or value.password or value.path or value.query or value.fragment):
        raise ValueError('Only local sandbox transports are supported')
    return value.port


async def main(argv):
    servers = []
    try:
        for name, default, socket_name in (
            ('LOMA_BROKER_URL', 'http://127.0.0.1:3100', 'broker'),
            ('LOMA_GATEWAY_URL', 'http://127.0.0.1:3101', 'gateway'),
        ):
            port = local_port(os.environ.get(name, default))
            async def forward(reader, writer, path=f'/run/loma/{socket_name}.sock'):
                await bridge(reader, writer, unix=path)
            servers.append(await asyncio.start_server(forward, '127.0.0.1', port, limit=65536))
        ingress = os.environ.get('LOMA_SANDBOX_INGRESS_PORT')
        if ingress:
            port = int(ingress)
            if not 1024 <= port <= 65535:
                raise ValueError('Invalid sandbox ingress port')
            async def inward(reader, writer):
                await bridge(reader, writer, port=port)
            servers.append(await asyncio.start_unix_server(
                inward, path=os.path.join(os.environ['HOME'], '.loma-ingress.sock')))
        process = await asyncio.create_subprocess_exec(*argv, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            def forward_signal(sig=sig):
                if process.returncode is None:
                    process.send_signal(sig)
            loop.add_signal_handler(sig, forward_signal)
        return await process.wait()
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1:])))
