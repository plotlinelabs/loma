"""Bounded public HTTPS downloads, without credentials or private-network access."""
import ipaddress
import socket
from urllib.parse import urlsplit

import aiohttp

MAX_DOWNLOAD_BYTES = 1_000_000


def _public_address(value):
    address = ipaddress.ip_address(value)
    # Also reject IPv4-in-IPv6 and transition mechanisms that can hide a private hop.
    if not address.is_global or getattr(address, 'ipv4_mapped', None) or getattr(address, 'sixtofour', None) or getattr(address, 'teredo', None):
        raise ValueError('Download destination must be public')


def validate_url(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 for c in url) or '\\' in url:
        raise ValueError('Invalid download URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError('Download requires public HTTPS on port 443')
    if '%' in parsed.hostname or not parsed.hostname.isascii():
        raise ValueError('Invalid download host')
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # aiohttp bypasses DNS for numeric-looking hosts, including shorthand
        # forms which ipaddress does not parse. Do not let those evade the resolver.
        if ':' in parsed.hostname or parsed.hostname.replace('.', '').isdigit():
            raise ValueError('Noncanonical numeric download host')
    else:
        _public_address(parsed.hostname)
    return parsed


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Check the exact addresses the connector will use, not an earlier DNS lookup."""
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await self.resolver.resolve(host, port, family)
        if not answers:
            raise ValueError('Download host has no addresses')
        for answer in answers:
            _public_address(answer['host'])
        return answers

    async def close(self):
        await self.resolver.close()


async def download_public(url):
    validate_url(url)
    resolver = PublicResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                timeout=aiohttp.ClientTimeout(total=60), auto_decompress=False) as session:
            # Redirects are deliberately denied: callers must supply the final URL.
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('Download did not return HTTP 200')
                if response.content_length is not None and response.content_length > MAX_DOWNLOAD_BYTES:
                    raise ValueError('Download exceeds size limit')
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > MAX_DOWNLOAD_BYTES:
                        raise ValueError('Download exceeds size limit')
                return bytes(content), response.headers.get('Content-Type', 'application/octet-stream')
    finally:
        await resolver.close()
