"""Runner-side tests: fixed argv construction, policy and archive safety (no emulator needed)."""
import asyncio
import base64
import os
import plistlib
import stat
import subprocess
import sys
import zipfile
import pytest

from device_loader.runner import loma_device_runner as ldr

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + (1080).to_bytes(4, 'big') + (2400).to_bytes(4, 'big') + b'\x08\x06\x00\x00\x00'
UI_XML = b'''<?xml version="1.0"?><hierarchy rotation="0">
<node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
  <node class="android.widget.Button" text="Show modal" resource-id="com.example.demo:id/show" clickable="true" bounds="[100,200][500,300]"/>
  <node class="android.view.View" bounds="[0,0][10,10]"/>
</node></hierarchy>'''

FAKE_ADB = r'''#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_ADB_LOG'], 'a') as log:
    log.write(repr(args) + '\n')
if args[:2] == ['devices', '-l']:
    print('List of devices attached')
    print('emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 transport_id:1')
    print('R58M123ABC             device product:beyond1 model:SM_G973F transport_id:2')
    print('emulator-5556          offline')
elif args[2:] == ['shell', 'getprop', 'ro.kernel.qemu']:
    print('0')
elif args[2:] == ['shell', 'getprop', 'ro.build.version.release']:
    print('14')
elif args[2:5] == ['exec-out', 'screencap', '-p']:
    sys.stdout.buffer.write(open(os.environ['FAKE_PNG'], 'rb').read())
elif args[2:4] == ['exec-out', 'cat']:
    sys.stdout.buffer.write(open(os.environ['FAKE_UI'], 'rb').read())
elif args[2:5] == ['shell', 'uiautomator', 'dump']:
    print('UI hierchary dumped to: /sdcard/loma_ui.xml')
elif args[2] == 'shell' and len(args) == 4 and 'uiautomator dump' in args[3]:
    sys.stderr.write('UI hierchary dumped to: /sdcard/loma_ui.xml\n')
    ui = os.environ['FAKE_UI']
    screens = sorted(p for p in os.listdir(os.path.dirname(ui)) if p.startswith('ui-seq-'))
    if screens:  # a scripted sequence of screens, one per dump
        ui = os.path.join(os.path.dirname(ui), screens[0])
        data = open(ui, 'rb').read()
        if len(screens) > 1:
            os.unlink(ui)
    else:
        data = open(ui, 'rb').read()
    sys.stdout.buffer.write(data)
elif args[2:5] == ['shell', 'dumpsys', 'package']:
    state = os.environ['FAKE_ADB_LOG'] + '.installed'
    if os.path.exists(state) and open(state).read().strip() == args[5]:
        print('    lastUpdateTime=' + str(os.path.getmtime(state)))
elif args[2:7] == ['shell', 'cmd', 'package', 'resolve-activity', '--brief']:
    print('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true')
    print(args[-1] + '/.MainActivity')
elif args[2:6] == ['shell', 'pm', 'list', 'packages']:
    state = os.environ['FAKE_ADB_LOG'] + '.installed'
    print('package:com.android.settings')
    if os.path.exists(state):
        print('package:' + open(state).read().strip())
elif args[2] == 'install':
    flag = os.environ['FAKE_ADB_LOG'] + '.incompatible'
    if os.path.exists(flag) and '-r' in args:
        os.unlink(flag)
        print('adb: failed to install app.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: '
              'Existing package com.example.demo signatures do not match newer version; ignoring!]')
        sys.exit(1)
    open(os.environ['FAKE_ADB_LOG'] + '.installed', 'w').write(os.environ.get('FAKE_INSTALL_PKG', 'com.example.demo'))
    print('Success')
'''


@pytest.fixture
def adb(tmp_path, monkeypatch):
    script = tmp_path / 'adb'
    script.write_text(FAKE_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    (tmp_path / 'shot.png').write_bytes(PNG)
    (tmp_path / 'ui.xml').write_bytes(UI_XML)
    log = tmp_path / 'adb.log'
    monkeypatch.setenv('FAKE_ADB_LOG', str(log))
    monkeypatch.setenv('FAKE_PNG', str(tmp_path / 'shot.png'))
    monkeypatch.setenv('FAKE_UI', str(tmp_path / 'ui.xml'))

    def calls():
        return [eval(line) for line in log.read_text().splitlines()] if log.exists() else []
    return ldr.Android(str(script)), calls


def runner(driver, **policy):
    return ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'policy': policy,
                       'cache_dir': os.environ.get('FAKE_ADB_LOG', '/nonexistent') + '.cache'}, drivers=[driver])


@pytest.mark.asyncio
async def test_physical_devices_hidden_by_default(adb):
    driver, _ = adb
    devices = await runner(driver).refresh()
    assert [d['serial'] for d in devices] == ['emulator-5554']
    assert devices[0]['os_version'] == '14' and devices[0]['virtual'] is True
    devices = await runner(driver, allow_physical_devices=True).refresh()
    assert {d['serial'] for d in devices} == {'emulator-5554', 'R58M123ABC'}


@pytest.mark.asyncio
async def test_unknown_device_and_physical_device_are_rejected(adb):
    driver, _ = adb
    r = runner(driver)
    with pytest.raises(ldr.OpError, match='not connected'):
        await r.call('tap', 'R58M123ABC', {'x': 1, 'y': 1})
    with pytest.raises(ldr.OpError, match='Unsupported'):
        await r.call('shell', 'emulator-5554', {})


@pytest.mark.asyncio
async def test_input_ops_build_fixed_argv(adb):
    driver, calls = adb
    r = runner(driver)
    await r.call('tap', 'emulator-5554', {'x': 300, 'y': 250})
    await r.call('type', 'emulator-5554', {'text': "it's a test; rm -rf /"})
    await r.call('open_url', 'emulator-5554', {'url': 'exampledemo://track?event=loma_test&x=1'})
    await r.call('key', 'emulator-5554', {'key': 'back'})
    shell = [c for c in calls() if c[:3] == ['-s', 'emulator-5554', 'shell']]
    assert ['-s', 'emulator-5554', 'shell', 'input', 'tap', '300', '250'] in shell
    typed = next(c for c in shell if c[3:5] == ['input', 'text'])
    # Quoted as ONE device-side shell word, spaces encoded as %s.
    assert typed[5] == "'it'\"'\"'s%sa%stest;%srm%s-rf%s/'"
    opened = next(c for c in shell if c[3:5] == ['am', 'start'])
    assert opened[-1] == "'exampledemo://track?event=loma_test&x=1'"
    assert ['-s', 'emulator-5554', 'shell', 'input', 'keyevent', '4'] in shell


@pytest.mark.asyncio
async def test_argument_validation(adb):
    driver, _ = adb
    r = runner(driver)
    for op, args in [('tap', {'x': -1, 'y': 0}), ('tap', {'x': '1', 'y': 0}), ('tap', {'x': True, 'y': 0}),
                     ('open_url', {'url': 'http://a b'}), ('open_url', {'url': "x://a'b"}),
                     ('open_url', {'url': 'no-scheme'}), ('type', {'text': 'x' * 501}),
                     ('launch', {'app_id': 'com.x; reboot'}), ('launch', {}), ('key', {'key': 'reboot'}),
                     ('logs', {'clear': 'yes'})]:
        with pytest.raises(ldr.OpError):
            await r.call(op, 'emulator-5554', args)


@pytest.mark.asyncio
async def test_non_ascii_text_rejected_on_android(adb):
    driver, _ = adb
    with pytest.raises(ldr.OpError, match='ASCII'):
        await runner(driver).call('type', 'emulator-5554', {'text': 'héllo'})


@pytest.mark.asyncio
async def test_allowed_app_ids_policy(adb):
    driver, _ = adb
    r = runner(driver, allowed_app_ids=['com.example.demo'])
    await r.call('launch', 'emulator-5554', {'app_id': 'com.example.demo'})
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await r.call('launch', 'emulator-5554', {'app_id': 'com.android.settings'})
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await r.call('install', 'emulator-5554', {'blob_id': 'abcdefgh1', 'sha256': 'a' * 64, 'filename': 'a.apk'})
    # Non-app ops never need an app id.
    await r.call('tap', 'emulator-5554', {'x': 1, 'y': 1})


@pytest.mark.asyncio
async def test_screenshot_and_ui_tree(adb):
    driver, _ = adb
    r = runner(driver)
    shot = await r.call('screenshot', 'emulator-5554', {})
    assert (shot['width'], shot['height']) == (1080, 2400)
    assert base64.b64decode(shot['png_base64']) == PNG
    tree = await r.call('ui_tree', 'emulator-5554', {})
    assert tree['units'] == 'pixels'
    assert tree['elements'] == [{'type': 'Button', 'text': 'Show modal', 'id': 'com.example.demo:id/show',
                                 'clickable': True, 'bounds': [100, 200, 500, 300], 'center': [300, 250]}]


def test_flow_scripts_opt_in():
    ok = 'appId: com.example.demo\n---\n- launchApp\n- tapOn: "Show modal"\n- assertVisible: "Hello"\n'
    ldr.screen_flow(ok, {})
    ldr.screen_flow(ok + '- runScript: x.js', {'allow_maestro_scripts': True})


def test_zip_slip_and_symlinks_rejected(tmp_path):
    evil = tmp_path / 'evil.zip'
    with zipfile.ZipFile(evil, 'w') as zf:
        zf.writestr('../../outside.txt', 'x')
    with pytest.raises(ldr.OpError, match='unsafe'):
        ldr.safe_extract(evil, tmp_path / 'out')
    link = tmp_path / 'link.zip'
    with zipfile.ZipFile(link, 'w') as zf:
        info = zipfile.ZipInfo('app.apk')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, '/etc/passwd')
    with pytest.raises(ldr.OpError, match='symlink'):
        ldr.safe_extract(link, tmp_path / 'out2')
    assert not (tmp_path.parent / 'outside.txt').exists()


def test_find_file_handles_nested_ios_artifact(tmp_path):
    inner = tmp_path / 'inner.zip'
    with zipfile.ZipFile(inner, 'w') as zf:
        info = zipfile.ZipInfo('DemoApp.app/DemoApp')
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(info, 'binary')
        zf.writestr('DemoApp.app/Info.plist', 'plist')
    outer = tmp_path / 'app-native-ios.zip'
    with zipfile.ZipFile(outer, 'w') as zf:
        zf.write(inner, 'DemoApp.zip')
        zf.writestr('__MACOSX/._DemoApp.zip', 'AppleDouble, not a zip')
    app = ldr.find_file(outer, '.app')
    assert app.name == 'DemoApp.app'
    assert os.access(app / 'DemoApp', os.X_OK)
    apk_zip = tmp_path / 'app-native-android.zip'
    with zipfile.ZipFile(apk_zip, 'w') as zf:
        zf.writestr('build/outputs/app-debug.apk', 'apk')
    assert ldr.find_file(apk_zip, '.apk').name == 'app-debug.apk'
    with pytest.raises(ldr.OpError, match='No .apk'):
        ldr.find_file(outer, '.apk')


def test_server_url_normalization():
    assert ldr.normalize_server('https://loma.example.com/') == 'https://loma.example.com'
    assert ldr.normalize_server('http://localhost:3000') == 'http://localhost:3000'
    with pytest.raises(SystemExit):
        ldr.normalize_server('http://loma.example.com')
    with pytest.raises(SystemExit):
        ldr.normalize_server('ftp://x')


def test_config_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(ldr, 'CONFIG_DIR', tmp_path / 'cfg')
    monkeypatch.setattr(ldr, 'CONFIG_PATH', tmp_path / 'cfg' / 'config.json')
    ldr.save_config({'server': 'https://x', 'runner_id': 'r', 'secret': 's'})
    assert stat.S_IMODE(ldr.CONFIG_PATH.stat().st_mode) == 0o600
    assert ldr.load_config()['policy'] == {}
    ldr.CONFIG_PATH.chmod(0o644)
    with pytest.raises(SystemExit, match='readable'):
        ldr.load_config()


@pytest.mark.parametrize('flow', [
    '- {runScript: x.js}',
    '- "addMedia": [~/a.png]',
    '- runFlow: other.yaml',
    '- evalScript: ${1+1}',
    '- inputText: ${output.secret}',
    '- startRecording: rec',
    '- assertTrue: ${true}',
    '- takeScreenshot: ../../etc/x',
    '- openLink: file:///Users/me/.ssh/id_rsa',
    '- repeat:\n    times: 2\n    commands:\n      - runScript: x.js',
    '- retry:\n    maxRetries: 2\n    file: /tmp/other.yaml',
    'appId: ${http.get("http://10.0.0.1")}\n---\n- launchApp',
    'appId: com.example.demo\nonFlowStart:\n  - runScript: x.js\n---\n- launchApp',
    'not: [valid',
])
def test_flow_screening_blocks_bypasses(flow):
    with pytest.raises(ldr.OpError):
        ldr.screen_flow(flow, {})


@pytest.mark.parametrize('flow', [
    '- launchApp: com.other',
    '- launchApp:\n    appId: com.other',
    '- stopApp: com.other',
    '- clearState: com.other',
    'appId: com.other\n---\n- launchApp',
])
def test_flow_app_allowlist(flow):
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        ldr.screen_flow(flow, {'allowed_app_ids': ['com.example.demo']})


def test_flow_allows_normal_commands():
    ldr.screen_flow('appId: com.example.demo\n---\n- launchApp\n- tapOn:\n    id: show\n'
                    '- repeat:\n    times: 2\n    commands:\n      - swipe:\n          direction: UP\n'
                    '- openLink: exampledemo://track?event=x\n- takeScreenshot: after_modal\n',
                    {'allowed_app_ids': ['com.example.demo']})


def test_file_urls_rejected():
    with pytest.raises(ldr.OpError, match='not allowed'):
        ldr.check_url('file:///etc/passwd')
    assert ldr.check_url('exampledemo://x') == 'exampledemo://x'


def test_dotdot_member_rejected(tmp_path):
    archive = tmp_path / 'a.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('a/../b.apk', 'x')
    with pytest.raises(ldr.OpError, match='unsafe'):
        ldr.safe_extract(archive, tmp_path / 'out')


def test_nested_zip_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(ldr, 'MAX_EXTRACT_BYTES', 1000)
    inner = tmp_path / 'inner.zip'
    with zipfile.ZipFile(inner, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('big.bin', b'0' * 800)
    outer = tmp_path / 'outer.zip'
    with zipfile.ZipFile(outer, 'w') as zf:
        zf.write(inner, 'one.zip')
        zf.write(inner, 'two.zip')
    with pytest.raises(ldr.OpError, match='size limit'):
        ldr.find_file(outer, '.apk')


@pytest.mark.asyncio
async def test_cancelled_run_kills_child(tmp_path):
    marker = tmp_path / 'done'
    task = asyncio.create_task(ldr.run([sys.executable, '-c',
        f'import time; time.sleep(3); open({str(marker)!r}, "w").write("x")'], timeout=30))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(3.5)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_install_allowlist_verifies_real_package(adb, tmp_path, monkeypatch):
    driver, calls = adb
    apk = tmp_path / 'app.apk'
    apk.write_bytes(b'apk')
    monkeypatch.setenv('FAKE_INSTALL_PKG', 'com.evil')
    with pytest.raises(ldr.OpError, match='not in allowed_app_ids'):
        await driver.install('emulator-5554', apk, 'com.example.demo', ('com.example.demo',))
    assert ['-s', 'emulator-5554', 'uninstall', 'com.evil'] in calls()
    install = next(c for c in calls() if c[2:3] == ['install'])
    assert '-r' not in install
    os.unlink(os.environ['FAKE_ADB_LOG'] + '.installed')
    monkeypatch.setenv('FAKE_INSTALL_PKG', 'com.example.demo')
    result = await driver.install('emulator-5554', apk, 'com.example.demo', ('com.example.demo',))
    assert result['installed'] == 'app.apk'


def test_ios_app_bundle_id_and_executable_bit(tmp_path):
    app = tmp_path / 'Demo.app'
    app.mkdir()
    (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.example.demo',
                                                     'CFBundleExecutable': 'Demo'}, fmt=plistlib.FMT_BINARY))
    (app / 'Demo').write_bytes(b'binary')
    (app / 'Demo').chmod(0o644)  # CI artifact zips drop unix modes
    found, bundle_id = ldr.prepare_app_bundle(app)
    assert found == app and bundle_id == 'com.example.demo'
    assert (app / 'Demo').stat().st_mode & 0o111


def test_flow_rejects_yaml_aliases():
    bomb = 'a: &a ["x", "x"]\nb: &b [*a, *a]\n---\n- launchApp\n'
    with pytest.raises(ldr.OpError, match='aliases'):
        ldr.screen_flow(bomb, {})


@pytest.mark.asyncio
async def test_calls_past_the_server_deadline_are_dropped(monkeypatch):
    class Slow(ldr.Runner):
        async def call(self, op, serial, args):
            await asyncio.sleep(30)

    class WS:
        closed, sent = False, []

        async def send_str(self, data):
            self.sent.append(data)

    runner, ws = Slow({'policy': {}}, drivers=[]), WS()
    runner.send_lock = asyncio.Lock()
    original = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        assert timeout == 55  # a 60 s server deadline leaves the runner 55 s
        return await original(awaitable, 0.01)

    monkeypatch.setattr(ldr.asyncio, 'wait_for', fast_wait_for)
    await runner.handle_call(ws, {'id': 'c1', 'op': 'tap', 'device': 'e', 'args': {}, 'timeout': 60})
    assert 'Timed out on the runner' in ws.sent[-1]


@pytest.mark.asyncio
async def test_ios_log_clear_starts_a_new_window(monkeypatch):
    seen = []

    async def fake_run(args, **kwargs):
        seen.append(args)
        return 0, b'line\n', ''

    monkeypatch.setattr(ldr, 'run', fake_run)
    ios = ldr.IOS()
    assert await ios.logs('SIM', 10, True) == []
    await ios.logs('SIM', 10, False)
    assert '--start' in seen[-1] and '--last' not in seen[-1]
    await ios.logs('OTHER', 10, False)
    assert '--last' in seen[-1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every runner path (config, venv, installed script, service files) at a temp HOME."""
    config_dir = tmp_path / '.loma-device-runner'
    monkeypatch.setenv('HOME', str(tmp_path))
    for name, value in {'CONFIG_DIR': config_dir, 'CONFIG_PATH': config_dir / 'config.json',
                        'VENV': config_dir / 'venv', 'INSTALLED_SCRIPT': config_dir / 'loma_device_runner.py'}.items():
        monkeypatch.setattr(ldr, name, value)
    return config_dir


def fake_cloudflared(tmp_path, monkeypatch, server):
    fake = tmp_path / 'bin' / 'cloudflared'
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(f'#!/bin/sh\n[ "$3" = "-app={server}" ] && echo eyJ.tok.en && exit 0\n'
                    'echo "Unable to find token" >&2; exit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', f'{fake.parent}:{os.environ["PATH"]}')


async def serve_enroll(handler, fn):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    app = web.Application()
    app.router.add_post('/device-runner/enroll', handler)
    async with TestServer(app) as server:
        return await fn(str(server.make_url('')).rstrip('/'))


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body,expected', [
    (302, '<html>Sign in</html>', 'exempt /device-runner/* from SSO'),
    (200, '<html>Sign in</html>', 'not a Loma response'),
    (400, '[1]', 'Enrollment failed: 400'),
])
async def test_enroll_reports_sso_redirects_and_non_json(home, status, body, expected):
    from aiohttp import web

    async def handler(request):
        headers = {'Location': 'https://sso.example.com/login'} if status == 302 else {}
        return web.Response(status=status, text=body, headers=headers)

    with pytest.raises(SystemExit) as exc:
        await serve_enroll(handler, lambda base: ldr.enroll(base, 'lde_x', 'Mac'))
    assert expected in str(exc.value)
    assert not ldr.CONFIG_PATH.exists()


@pytest.mark.asyncio
async def test_enroll_detects_cloudflare_access_and_keeps_policy(home, tmp_path, monkeypatch):
    from aiohttp import web

    async def handler(request):
        if request.headers.get('cf-access-token') != 'eyJ.tok.en':
            return web.Response(status=302, headers={'Location': 'https://team.cloudflareaccess.com/login'})
        return web.json_response({'runner_id': 'r_0123456789abcdef', 'secret': 's3cret', 'name': 'Mac'})

    ldr.save_config({'policy': {'allowed_app_ids': ['com.example.demo']}})

    async def enroll(base):
        fake_cloudflared(tmp_path, monkeypatch, base)
        await ldr.enroll(base, 'lde_x', 'Mac')

    await serve_enroll(handler, enroll)
    config = ldr.load_config()
    assert config['cf_access'] is True and config['runner_id'] == 'r_0123456789abcdef'
    assert config['policy'] == {'allowed_app_ids': ['com.example.demo']}


def test_cf_access_headers(monkeypatch, tmp_path):
    assert ldr.cf_access_headers({'server': 'https://x.example.com'}) == {}
    fake_cloudflared(tmp_path, monkeypatch, 'https://x.example.com')
    assert ldr.cf_access_headers({'server': 'https://x.example.com', 'cf_access': True}) == {'cf-access-token': 'eyJ.tok.en'}
    with pytest.raises(ldr.CfAccessError, match='cloudflared access login https://y.example.com'):
        ldr.cf_access_headers({'server': 'https://y.example.com', 'cf_access': True})


class Exec(Exception):
    pass


def record_subprocess(monkeypatch, returncode=0):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, '', 'boom')

    monkeypatch.setattr(ldr.subprocess, 'run', fake_run)
    return calls


def test_setup_needs_a_token_or_an_existing_enrollment(home):
    with pytest.raises(SystemExit, match='Not enrolled yet'):
        ldr.main(['setup'])
    with pytest.raises(SystemExit, match='--server is required'):
        ldr.main(['setup', '--token', 'lde_x'])


def test_setup_bootstraps_private_env_then_reexecs(home, monkeypatch):
    calls = record_subprocess(monkeypatch)

    def fake_execv(path, argv):
        raise Exec(argv)

    monkeypatch.setattr(ldr.os, 'execv', fake_execv)
    argv = ['setup', '--server', 'https://loma.example.com', '--token', 'lde_x']
    with pytest.raises(Exec) as exc:
        ldr.main(argv)
    python = str(home / 'venv' / 'bin' / 'python3')
    assert calls[0] == [sys.executable, '-m', 'venv', str(home / 'venv')]
    assert calls[1][:4] == [python, '-m', 'pip', 'install'] and 'aiohttp>=3.9,<4' in calls[1]
    assert exc.value.args[0] == [python, str(ldr.INSTALLED_SCRIPT), *argv]
    assert ldr.INSTALLED_SCRIPT.read_bytes() == open(ldr.__file__, 'rb').read()


@pytest.mark.parametrize('system', ['linux', 'darwin'])
def test_install_service_writes_and_starts(home, monkeypatch, system):
    monkeypatch.setattr(ldr.sys, 'platform', system)
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    calls = record_subprocess(monkeypatch)
    home.mkdir()
    ldr.install_service()
    unit = ldr.service_file()
    if system == 'linux':
        text = unit.read_text()
        assert f'ExecStart={sys.executable} {ldr.INSTALLED_SCRIPT} run' in text
        assert f'"LOMA_DEVICE_RUNNER_HOME={home}"' in text
        assert calls[-1] == ['systemctl', '--user', 'restart', 'loma-device-runner']
    else:
        plist = plistlib.loads(unit.read_bytes())
        assert plist['ProgramArguments'] == [sys.executable, str(ldr.INSTALLED_SCRIPT), 'run']
        assert calls[-1][:2] == ['launchctl', 'bootstrap']


def test_install_service_failure_points_to_foreground(home, monkeypatch):
    monkeypatch.setattr(ldr.sys, 'platform', 'linux')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    monkeypatch.setattr(ldr.time, 'sleep', lambda s: None)
    record_subprocess(monkeypatch, returncode=1)
    with pytest.raises(SystemExit, match='Run it in the foreground instead'):
        ldr.install_service()


def test_uninstall_removes_service_and_config(home, monkeypatch):
    monkeypatch.setattr(ldr.sys, 'platform', 'linux')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    calls = record_subprocess(monkeypatch)
    ldr.save_config({'server': 'https://loma.example.com'})
    ldr.service_file().parent.mkdir(parents=True)
    ldr.service_file().write_text('unit')
    ldr.uninstall()
    assert not home.exists() and not ldr.service_file().exists()
    assert ['systemctl', '--user', 'disable', '--now', 'loma-device-runner'] in calls


def test_extend_path_adds_default_tool_dirs_once(monkeypatch, tmp_path):
    monkeypatch.setenv('ANDROID_HOME', str(tmp_path / 'sdk'))
    monkeypatch.setenv('PATH', '/usr/bin')
    ldr.extend_path()
    path = ldr.extend_path().split(os.pathsep)
    assert path[0] == '/usr/bin' and path.count(str(tmp_path / 'sdk' / 'platform-tools')) == 1


def test_second_runner_instance_is_refused(home):
    import fcntl
    home.mkdir()
    with open(home / 'runner.lock', 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit, match='already running'):
            ldr.run_forever()


def test_install_ios_tools_installs_idb_client_and_trusted_companion(home, monkeypatch, capsys):
    monkeypatch.setattr(ldr.sys, 'platform', 'darwin')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: None if tool == 'idb_companion' else '/usr/bin/' + tool)
    calls = record_subprocess(monkeypatch)
    ldr.install_ios_tools()
    assert calls[0][:4] == [sys.executable, '-m', 'pip', 'install'] and calls[0][-1] == 'fb-idb'
    assert calls[1:] == [['brew', 'tap', 'facebook/fb'], ['brew', 'trust', '--formula', ldr.IDB_COMPANION],
                         ['brew', 'install', ldr.IDB_COMPANION]]


def test_install_ios_tools_is_best_effort(home, monkeypatch, capsys):
    monkeypatch.setattr(ldr.sys, 'platform', 'darwin')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: None if tool == 'idb_companion' else '/usr/bin/' + tool)
    calls = record_subprocess(monkeypatch, returncode=1)
    ldr.install_ios_tools()  # must not raise
    assert len(calls) == 1 and 'could not install' in capsys.readouterr().out


def test_install_ios_tools_skips_without_xcode_or_when_present(home, monkeypatch):
    calls = record_subprocess(monkeypatch)
    monkeypatch.setattr(ldr.sys, 'platform', 'linux')
    ldr.install_ios_tools()
    monkeypatch.setattr(ldr.sys, 'platform', 'darwin')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: None if tool == 'xcrun' else '/usr/bin/' + tool)
    ldr.install_ios_tools()
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    (home / 'venv' / 'bin').mkdir(parents=True)
    (home / 'venv' / 'bin' / 'idb').write_text('')
    ldr.install_ios_tools()
    assert calls == []


def test_extend_path_finds_private_idb_and_homebrew(home, monkeypatch):
    monkeypatch.setattr(ldr.sys, 'platform', 'darwin')
    monkeypatch.setenv('PATH', '/usr/bin')
    path = ldr.extend_path().split(os.pathsep)
    assert path[0] == '/usr/bin' and str(home / 'venv' / 'bin') in path and '/opt/homebrew/bin' in path


# ── Device ops v2: launch extras, compound ops, faster ui_tree, install reuse ──

FORM_XML = b'''<?xml version="1.0"?><hierarchy rotation="0">
<node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
  <node class="android.widget.EditText" text="https://old.example.com" resource-id="com.example.demo:id/endpoint" clickable="true" bounds="[40,400][1040,520]"/>
  <node class="android.widget.Button" text="Save" resource-id="com.example.demo:id/save" clickable="true" bounds="[40,600][540,700]"/>
  <node class="android.widget.TextView" text="Save settings below" bounds="[40,800][1040,860]"/>
</node></hierarchy>'''


def shell_calls(calls):  # device shell commands, without the inventory refresh probes
    return [c[3:] for c in calls() if c[:3] == ['-s', 'emulator-5554', 'shell'] and c[3] != 'getprop']


@pytest.mark.asyncio
async def test_ui_tree_is_one_adb_round_trip_with_screen_size(adb):
    driver, calls = adb
    tree = await runner(driver).call('ui_tree', 'emulator-5554', {})
    assert tree['screen'] == [1080, 2400] and tree['elements'][0]['text'] == 'Show modal'
    assert shell_calls(calls) == [[ldr.ANDROID_UI_DUMP]]  # was rm + dump + cat
    assert not any('exec-out' in c for c in calls())


@pytest.mark.asyncio
async def test_android_launch_with_extras_quotes_each_value(adb):
    driver, calls = adb
    r = runner(driver)
    await r.call('launch', 'emulator-5554', {'app_id': 'com.example.demo'})
    assert ['monkey', '-p', 'com.example.demo', '-c', 'android.intent.category.LAUNCHER', '1'] in shell_calls(calls)
    result = await r.call('launch', 'emulator-5554', {
        'app_id': 'com.example.demo', 'extras': {'api_endpoint': "https://a-b.example.com/x?y=1&z='q' $(id)"},
        'bool_extras': {'clearCache': True}})
    assert result['component'] == 'com.example.demo/.MainActivity'
    start = next(c for c in shell_calls(calls) if c[:2] == ['am', 'start'])
    assert start[:6] == ['am', 'start', '-W', '-S', '-n', "com.example.demo/.MainActivity"]
    assert start[6:9] == ['--es', 'api_endpoint', "'https://a-b.example.com/x?y=1&z='\"'\"'q'\"'\"' $(id)'"]
    assert start[9:] == ['--ez', 'clearCache', 'true']
    for bad in [{'extras': {'a;b': 'x'}}, {'extras': {'k': 5}}, {'bool_extras': {'k': 'yes'}},
                {'activity': '.Main; reboot'}, {'extras': {f'k{i}': 'v' for i in range(21)}}]:
        with pytest.raises(ldr.OpError):
            await r.call('launch', 'emulator-5554', {'app_id': 'com.example.demo', **bad})


@pytest.mark.asyncio
async def test_ios_launch_arguments_and_console(monkeypatch, tmp_path):
    assert ldr.IOS.launch_arguments({'api_endpoint': 'https://x'}, {'debug': True, 'b': False}) == [
        '-api_endpoint', 'https://x', '-debug', 'YES', '-b', 'NO']
    seen = []

    async def fake_run(args, **kwargs):
        seen.append(args)
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    ios = ldr.IOS()
    await ios.launch('UDID-1', 'com.example.demo', extras={'demo_user_id': 'u1'})
    assert seen[-1] == ['xcrun', 'simctl', 'launch', '--terminate-running-process', 'UDID-1', 'com.example.demo',
                        '-demo_user_id', 'u1']
    await ios.launch('UDID-1', 'com.example.demo')
    assert seen[-1] == ['xcrun', 'simctl', 'launch', 'UDID-1', 'com.example.demo']
    # Console capture: logs reads the app's stdout file, and clear truncates it.
    log = tmp_path / 'UDID-1.log'
    log.write_text('DemoSDK: widget skeleton shown\r\nDemoSDK: widget loaded\n')
    ios.consoles['UDID-1'] = (None, log)
    assert await ios.logs('UDID-1', 1, False) == ['DemoSDK: widget loaded']
    assert await ios.logs('UDID-1', 10, True) == [] and log.read_text() == ''
    with pytest.raises(ldr.OpError, match='console=true'):
        await ldr.IOS().logs('UDID-2', 10, False, 'console')


@pytest.mark.asyncio
async def test_set_text_focuses_clears_and_types_in_three_calls(adb, tmp_path):
    driver, calls = adb
    (tmp_path / 'ui.xml').write_bytes(FORM_XML)
    result = await runner(driver).call('set_text', 'emulator-5554', {
        'match': 'endpoint', 'by': 'id', 'text': 'https://my-endpoint.example.com/v1'})
    assert result['focused']['id'] == 'com.example.demo:id/endpoint' and result['typed'] == 34
    shell = shell_calls(calls)[1:]  # after the one ui_tree dump
    assert shell[0] == ['input', 'tap', '540', '460']
    keys = shell[1]
    assert keys[:3] == ['input', 'keyevent', '123'] and keys.count('67') == len('https://old.example.com') + 8
    assert shell[2] == ['input', 'text', 'https://my-endpoint.example.com/v1'] and len(shell) == 3


@pytest.mark.asyncio
async def test_clear_text_and_ascii_rules(adb):
    driver, calls = adb
    r = runner(driver)
    await r.call('clear_text', 'emulator-5554', {})
    keys = shell_calls(calls)[-1]
    assert keys.count('67') == 64 and keys[2] == '123'
    await r.call('set_text', 'emulator-5554', {'text': 'a b', 'clear': False})
    assert shell_calls(calls)[-1] == ['input', 'text', 'a%sb']
    with pytest.raises(ldr.OpError, match='%s'):
        await r.call('set_text', 'emulator-5554', {'text': '100%sure'})


@pytest.mark.asyncio
async def test_wait_for_and_tap_text(adb, tmp_path):
    driver, calls = adb
    (tmp_path / 'ui.xml').write_bytes(FORM_XML)
    r = runner(driver)
    found = await r.call('wait_for', 'emulator-5554', {'match': 'save', 'exact': True, 'timeout_s': 0})
    assert found['found'] and found['element']['text'] == 'Save' and found['matches'] == 1
    ranked = await r.call('wait_for', 'emulator-5554', {'match': 'save', 'timeout_s': 0})
    assert ranked['element']['text'] == 'Save' and ranked['matches'] == 2  # exact + clickable first
    missing = await r.call('wait_for', 'emulator-5554', {'match': 'Nope', 'timeout_s': 0})
    assert missing['found'] is False and 'Save' in missing['visible']
    gone = await r.call('wait_for', 'emulator-5554', {'match': 'Nope', 'gone': True, 'timeout_s': 0})
    assert gone['found'] is True
    tapped = await r.call('tap_text', 'emulator-5554', {'match': 'Save', 'exact': True})
    assert tapped['tapped'] == [290, 650] and ['input', 'tap', '290', '650'] in shell_calls(calls)
    with pytest.raises(ldr.OpError, match='Visible'):
        await r.call('tap_text', 'emulator-5554', {'match': 'Nope', 'timeout_s': 0})
    for bad in [{'match': 'x', 'by': 'xpath'}, {'match': 'x', 'timeout_s': 121}, {'match': ''}, {'match': 'x', 'gone': 1}]:
        with pytest.raises(ldr.OpError):
            await r.call('wait_for', 'emulator-5554', bad)


@pytest.mark.asyncio
async def test_scroll_until_visible_uses_fixed_slow_swipes(adb, tmp_path):
    driver, calls = adb
    (tmp_path / 'ui-seq-1.xml').write_bytes(UI_XML)
    (tmp_path / 'ui-seq-2.xml').write_bytes(FORM_XML)
    result = await runner(driver).call('scroll_until_visible', 'emulator-5554', {'match': 'Save', 'exact': True})
    assert result['found'] and result['swipes'] == 1
    swipes = [c for c in shell_calls(calls) if c[:2] == ['input', 'swipe']]
    assert swipes == [['input', 'swipe', '540', '1680', '540', '720', str(ldr.SWIPE_MS)]]


@pytest.mark.asyncio
async def test_wakeup_key(adb):
    driver, calls = adb
    await runner(driver).call('key', 'emulator-5554', {'key': 'wakeup'})
    assert shell_calls(calls)[-1] == [ldr.ANDROID_WAKEUP]
    assert (await ldr.IOS().key('UDID', 'wakeup'))['note']


@pytest.mark.asyncio
async def test_install_updates_in_place_and_reinstalls_only_on_signature_change(adb, tmp_path):
    driver, calls = adb
    apk = tmp_path / 'app.apk'
    apk.write_bytes(b'apk')
    result = await driver.install('emulator-5554', apk, 'com.example.demo')
    assert result['data_kept'] is True
    assert not any(c[2:3] == ['uninstall'] for c in calls())
    assert next(c for c in calls() if c[2:3] == ['install'])[3:7] == ['-r', '-d', '-t', '-g']
    open(os.environ['FAKE_ADB_LOG'] + '.incompatible', 'w').close()
    result = await driver.install('emulator-5554', apk, None)  # package taken from the adb error
    assert result['data_kept'] is False
    assert ['-s', 'emulator-5554', 'uninstall', 'com.example.demo'] in calls()


@pytest.mark.asyncio
async def test_same_build_is_not_downloaded_or_installed_twice(adb, tmp_path, monkeypatch):
    driver, calls = adb
    r = runner(driver)
    downloads = []

    async def fake_download(args, target):
        downloads.append(args['blob_id'])
        path = target / args['filename']
        path.write_bytes(b'apk')
        return path
    monkeypatch.setattr(r, 'download', fake_download)
    args = {'blob_id': 'blob12345', 'sha256': 'a' * 64, 'filename': 'app.apk', 'app_id': 'com.example.demo',
            'grant_appops': ['SCHEDULE_EXACT_ALARM']}
    first = await r.call('install', 'emulator-5554', args)
    assert first['granted'] == ['SCHEDULE_EXACT_ALARM']
    assert ['appops', 'set', 'com.example.demo', 'SCHEDULE_EXACT_ALARM', 'allow'] in shell_calls(calls)
    second = await r.call('install', 'emulator-5554', args)
    assert second['skipped'] is True and downloads == ['blob12345']
    await r.call('install', 'emulator-5554', {**args, 'force': True})
    await r.call('install', 'emulator-5554', {**args, 'sha256': 'b' * 64})
    assert len(downloads) == 3
    for bad in [['schedule_exact_alarm'], ['X; reboot'], 'SCHEDULE_EXACT_ALARM']:
        with pytest.raises(ldr.OpError, match='grant'):
            await r.call('install', 'emulator-5554', {**args, 'grant_appops': bad})


@pytest.mark.asyncio
async def test_download_cache_by_checksum(tmp_path):
    import hashlib
    r = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'cache_dir': str(tmp_path / 'c')},
                   drivers=[])
    sha = hashlib.sha256(b'build').hexdigest()
    src = tmp_path / 'x.apk'
    src.write_bytes(b'build')
    r._store_in_cache(src, sha)
    target = tmp_path / 't'
    target.mkdir()
    path = await r.download({'blob_id': 'blob12345', 'sha256': sha, 'filename': 'app.apk'}, target)  # no session
    assert path.read_bytes() == b'build' and path.parent == target
    for i in range(6):
        r._store_in_cache(src, f'{i:064x}')
    assert len(list((tmp_path / 'c').iterdir())) == r.CACHE_KEEP


class TimedDriver:
    platform = 'android'

    def __init__(self):
        self.launched = []

    async def capture(self, serial):
        await asyncio.sleep(0.05)
        return 'png', PNG

    async def launch(self, serial, app_id, **options):
        self.launched.append((app_id, options))


@pytest.mark.asyncio
async def test_burst_is_timed_on_the_runner_and_can_launch_first():
    driver = TimedDriver()
    r = runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    result = await r.call('burst', 'emulator-5554', {'count': 4, 'interval_ms': 100, 'app_id': 'com.example.demo',
                                                     'extras': {'api_endpoint': 'https://x'}})
    offsets = [f['at_ms'] for f in result['frames']]
    assert len(offsets) == 4 and all(abs(at - i * 100) < 60 for i, at in enumerate(offsets))
    assert driver.launched == [('com.example.demo', {'extras': {'api_endpoint': 'https://x'}, 'bool_extras': {},
                                                     'activity': None, 'console': False})]
    for bad in [{'count': 1}, {'count': 13}, {'count': 2, 'interval_ms': 50}]:
        with pytest.raises(ldr.OpError):
            await r.call('burst', 'emulator-5554', bad)


def test_flow_screenshots_are_collected_before_cleanup(tmp_path):
    (tmp_path / 'after_dismiss.png').write_bytes(PNG)
    (tmp_path / 'report.xml').write_text('<x/>')
    shots = ldr.collect_screenshots(tmp_path)
    assert [s['name'] for s in shots] == ['after_dismiss.png'] and base64.b64decode(shots[0]['png_base64']) == PNG


def test_find_elements_ranking():
    elements = [{'text': 'Save settings'}, {'text': 'save', 'clickable': True}, {'id': 'com.app:id/save'},
                {'label': 'Saved'}]
    assert ldr.find_elements(elements, 'save', 'any', True) == [elements[1], elements[2]]
    assert ldr.find_elements(elements, 'SAVE')[:2] == [elements[1], elements[2]]
    assert ldr.find_elements(elements, 'save', 'label') == [elements[3]]


# ── Review fixes: recording size, orphaned screenrecord, cache integrity, package mismatch ──


def fake_record_run(monkeypatch, size, calls):
    async def fake_run(args, timeout=60, check=True, **kwargs):
        calls.append(args)
        if 'screenrecord' in args:
            await asyncio.sleep(0.05)
        elif 'pull' in args:
            with open(args[-1], 'wb') as handle:
                handle.write(b'\0' * size)
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)


@pytest.mark.asyncio
async def test_android_record_pulls_the_file_and_refuses_oversized(monkeypatch):
    calls = []
    monkeypatch.setattr(ldr, 'MAX_MEDIA_BYTES', 1000)
    fake_record_run(monkeypatch, 900, calls)
    assert len(await ldr.Android('adb').record('emulator-5554', 1)) == 900
    assert ['adb', '-s', 'emulator-5554', 'pull', '/sdcard/loma_rec.mp4'] == calls[1][:5]
    assert not any('exec-out' in c for c in calls)  # stdout is capped at MAX_OUTPUT: never read the mp4 from it
    calls.clear()
    fake_record_run(monkeypatch, 1001, calls)
    with pytest.raises(ldr.OpError, match='over the 0 MB limit'):
        await ldr.Android('adb').record('emulator-5554', 1)
    assert calls[-1][-3:] == ['rm', '-f', '/sdcard/loma_rec.mp4']


@pytest.mark.asyncio
async def test_android_record_stops_screenrecord_when_launch_fails(monkeypatch):
    calls, recording = [], {}

    async def fake_run(args, timeout=60, check=True, **kwargs):
        calls.append(args)
        if 'screenrecord' in args:
            recording['task'] = asyncio.current_task()
            await asyncio.sleep(30)
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)

    async def failing_launch():
        raise ldr.OpError('launch failed')
    with pytest.raises(ldr.OpError, match='launch failed'):
        await ldr.Android('adb').record('emulator-5554', 5, failing_launch)
    assert recording['task'].cancelled()
    assert ['adb', '-s', 'emulator-5554', 'shell', 'pkill -INT screenrecord'] in calls
    assert calls[-1][-3:] == ['rm', '-f', '/sdcard/loma_rec.mp4']


@pytest.mark.asyncio
async def test_cached_build_is_reverified_and_redownloaded_on_mismatch(tmp_path):
    import hashlib
    r = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'cache_dir': str(tmp_path / 'c')},
                   drivers=[])
    sha = hashlib.sha256(b'build').hexdigest()
    src = tmp_path / 'x.apk'
    src.write_bytes(b'build')
    r._store_in_cache(src, sha)
    assert [p.name for p in (tmp_path / 'c').iterdir()] == [sha]  # no temp files left behind
    (tmp_path / 'c' / sha).write_bytes(b'tampered')

    class Response:
        status = 200

        class content:
            @staticmethod
            async def iter_chunked(size):
                yield b'build'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    fetched = []

    class Session:
        def get(self, url, **kwargs):
            fetched.append(url)
            return Response()
    r.session = Session()
    target = tmp_path / 't'
    target.mkdir()
    path = await r.download({'blob_id': 'blob12345', 'sha256': sha, 'filename': 'app.apk'}, target)
    assert path.read_bytes() == b'build' and fetched == ['https://loma.test/device-runner/blobs/blob12345']
    assert (tmp_path / 'c' / sha).read_bytes() == b'build'  # the re-download replaced the bad entry


@pytest.mark.asyncio
async def test_signature_mismatch_never_uninstalls_a_different_package(adb, tmp_path):
    driver, calls = adb
    apk = tmp_path / 'app.apk'
    apk.write_bytes(b'apk')
    open(os.environ['FAKE_ADB_LOG'] + '.incompatible', 'w').close()
    with pytest.raises(ldr.OpError, match='installs com.example.demo, not app_id com.other.app'):
        await driver.install('emulator-5554', apk, 'com.other.app')
    assert not any(c[2:3] == ['uninstall'] for c in calls())
    open(os.environ['FAKE_ADB_LOG'] + '.incompatible', 'w').close()
    result = await driver.install('emulator-5554', apk, 'com.example.demo')
    assert result['data_kept'] is False and ['-s', 'emulator-5554', 'uninstall', 'com.example.demo'] in calls()


def test_am_start_error_detection_ignores_activity_names():
    assert ldr.AM_ERROR.search('Starting: Intent { cmp=com.x/.Main }\nError: Activity class {com.x/.Main} does not exist.')
    assert ldr.AM_ERROR.search('Starting: Intent {...}\nError type 3\nError: Activity class does not exist')
    assert not ldr.AM_ERROR.search('Starting: Intent { cmp=com.x/.ErrorReportActivity }\nStatus: ok\n'
                                   'Activity: com.x/.ErrorReportActivity\nComplete')


@pytest.mark.asyncio
async def test_scroll_until_visible_retries_when_the_ui_is_not_idle():
    class Flaky:
        platform = 'android'

        def __init__(self):
            self.dumps, self.swipes = 0, 0

        async def ui_tree(self, serial, frozen=False):
            self.dumps += 1
            if self.dumps == 1:
                raise ldr.OpError('uiautomator could not capture the screen (UI not idle?)')
            return {'screen': [1080, 2400], 'elements': [{'text': 'Save', 'center': [1, 2]}]}

    driver = Flaky()
    r = runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    result = await r.call('scroll_until_visible', 'emulator-5554', {'match': 'Save'})
    assert result['found'] and driver.dumps == 2
