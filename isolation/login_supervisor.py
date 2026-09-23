"""Separate dedicated login host; never enable networking on task workers."""
import json
import os
import re
import ssl

from aiohttp import web
from isolation.supervisor import Settings, Supervisor, command, docker_command


class LoginSupervisor(Supervisor):
    def __init__(self, settings, network):
        super().__init__(settings)
        if not re.fullmatch(r'loma-login-[a-z0-9-]{1,40}', network):
            raise ValueError('A dedicated login egress network is required')
        self.network = network

    async def preflight(self, app):
        await super().preflight(app)
        info = json.loads(await command('docker', 'network', 'inspect', self.network))[0]
        if (info.get('Driver') != 'bridge' or info.get('Internal') is not False
                or info.get('Labels', {}).get('io.loma.login-egress') != 'restricted'):
            raise RuntimeError('Login network must have reviewed egress restrictions')

    def container_command(self, name):
        argv = docker_command(self.settings, name)
        argv[argv.index('--network') + 1] = self.network
        return argv


def make_app(settings, network):
    supervisor = LoginSupervisor(settings, network)
    app = web.Application(client_max_size=8192)
    app.router.add_get('/health', supervisor.health)
    app.router.add_get('/v1/run', supervisor.run)
    app.on_startup.append(supervisor.preflight)
    return app


if __name__ == '__main__':
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ['LOMA_WORKER_TLS_CERT'], os.environ['LOMA_WORKER_TLS_KEY'])
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.load_verify_locations(os.environ['LOMA_WORKER_CLIENT_CA'])
    settings = Settings.from_env()
    if settings.max_seconds > 660:
        raise ValueError('Login supervisor deadline must not exceed 660 seconds')
    web.run_app(make_app(settings, os.environ['LOMA_LOGIN_NETWORK']),
                host='0.0.0.0', port=8443, ssl_context=tls)
