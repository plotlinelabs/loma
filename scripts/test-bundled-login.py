"""Run INSIDE the ephemeral CI login container; never on a production host.

No subscriptions, model calls or backend credentials. Exercises real pinned CLI
startup and cancellation, then chroot/UID/network denials on the built image.
"""
import asyncio
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import aiohttp


async def smoke():
    root = Path('/sandbox/sessions')
    assert not list(root.iterdir()), 'Only run on a fresh CI login container'
    connector = aiohttp.UnixConnector(path='/run/loma-login/login.sock')
    async with aiohttp.ClientSession(connector=connector) as client:
        async with client.get('http://login/health') as response:
            assert response.status == 200
        async with asyncio.timeout(90):
            async with client.ws_connect('http://login/v1/run') as ws:
                await ws.send_json({'type': 'start', 'input': {'runtime': 'claude-login'}})
                text = ''
                async for message in ws:
                    assert message.type == aiohttp.WSMsgType.TEXT
                    frame = json.loads(message.data)
                    if frame['type'] == 'text':
                        text += frame['text']
                        if '/oauth/authorize?' in text and 'code_challenge=' in text and 'state=' in text:
                            break  # deliberately cancel; do not print PKCE/state
                else:
                    # This isolated stack has no credentials and never submitted
                    # a login code. Still redact provider links/PKCE parameters.
                    safe = re.sub(r'https?://\S+', '[URL redacted]', text)
                    safe = re.sub(r'(state|code_challenge)=[^\s&]+', r'\1=[redacted]', safe)
                    raise AssertionError('Official CLI failed before authorization: ' + safe[-1200:])
        for _ in range(100):
            if not list(root.iterdir()):
                break
            await asyncio.sleep(.1)
        else:
            raise AssertionError('Cancelled login left temporary credentials/processes')
        async with client.get('http://login/health') as response:
            assert response.status == 200, 'Cleanup must not leave broker unhealthy'
    # No root-owned namespace waiter may survive cancellation (UID-only checks
    # would miss it). Check argv without logging any process environments.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try: argv = (entry / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError): continue
        assert b'/opt/login/isolation/bundled_login_child.py' not in argv, 'Orphaned login launcher'
    print('PASS: official CLI authorization URL, Unix transport, cancellation/cleanup')


def boundaries():
    root = Path('/sandbox/sessions')
    for name, uid in [('probe', 50000), ('peer', 50001)]:
        path = root / name; path.mkdir(mode=0o700); os.chown(path, uid, uid)
    (root / 'peer/credential').write_text('synthetic')
    code = r'''
const fs = require('fs'), net = require('net'), assert = require('assert');
for (const path of ['/app/.env', '/run/loma-login/login.sock', '/sessions/peer/credential']) {
  assert.throws(() => fs.readFileSync(path));
}
assert(fs.readFileSync('/proc/self/maps', 'utf8').includes('[stack]'));
assert.deepStrictEqual(fs.readdirSync('/proc').filter(x => /^\d+$/.test(x)), ['1']);
assert.strictEqual(process.pid, 1);
assert.strictEqual(process.getuid(), 50000);
const status = fs.readFileSync('/proc/self/status', 'utf8');
for (const cap of ['CapEff', 'CapPrm', 'CapInh', 'CapAmb']) assert(new RegExp(cap + ':\\s+0+\\n').test(status));
assert(/NoNewPrivs:\s+1/.test(status));
const mount = fs.readFileSync('/proc/mounts', 'utf8').split('\n').find(l => l.split(' ')[1] === '/proc');
assert(mount && ['ro','nosuid','nodev','noexec'].every(f => mount.split(' ')[3].split(',').includes(f)));
assert.throws(() => fs.writeFileSync('/proc/sys/kernel/hostname', 'bad'));
assert.throws(() => fs.writeFileSync('/usr/local/escape', 'bad'));
fs.writeFileSync('/sessions/probe/allowed', 'ok');
async function denied(host, port) {
  await new Promise((resolve, reject) => {
    const socket = net.connect({host, port});
    socket.on('connect', () => {socket.destroy(); reject(new Error('Forbidden connection succeeded'));});
    socket.on('error', resolve);
    socket.setTimeout(800, () => {socket.destroy(); resolve();});
  });
}
(async () => {
  for (const host of ['127.0.0.1', '169.254.169.254', '10.0.0.1', '1.1.1.1', '::1']) await denied(host, 443);
  console.log('PASS: private PID/proc, zero CLI capabilities, chroot, peer credentials, read-only runtime, direct egress denied');
})().catch(e => {console.error(e.message); process.exit(1);});
'''
    # Fresh interpreter, same chroot and UID removal as launcher; intentionally
    # substitutes a fixed test script for the CLI to probe the actual boundary.
    launch = """import os,sys
from isolation.bundled_login_child import guard_parent, isolate_processes, drop_privileges
guard_parent()
parent_fd = isolate_processes()
os.chroot('/sandbox'); os.chdir('/sessions/probe')
drop_privileges(50000)
guard_parent(parent_fd); os.close(parent_fd)
os.execve('/usr/local/bin/node',['node','-e',sys.argv[1]],{'HOME':'/sessions/probe'})
"""
    subprocess.run([sys.executable, '-c', launch, code], check=True, timeout=20)
    import shutil
    for name in ('probe', 'peer'): shutil.rmtree(root / name)


asyncio.run(smoke())
boundaries()
