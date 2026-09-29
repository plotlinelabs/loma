"""Runner-side tests: fixed argv construction, policy and archive safety (no emulator needed)."""
import base64
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from device_runner import loma_device_runner as ldr

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + (1080).to_bytes(4, 'big') + (2400).to_bytes(4, 'big') + b'\x08\x06\x00\x00\x00'
UI_XML = b'''<?xml version="1.0"?><hierarchy rotation="0">
<node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
  <node class="android.widget.Button" text="Show modal" resource-id="so.plotline.demo:id/show" clickable="true" bounds="[100,200][500,300]"/>
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
    return ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'policy': policy}, drivers=[driver])


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
    await r.call('open_url', 'emulator-5554', {'url': 'plotlinedemo://track?event=loma_test&x=1'})
    await r.call('key', 'emulator-5554', {'key': 'back'})
    shell = [c for c in calls() if c[:3] == ['-s', 'emulator-5554', 'shell']]
    assert ['-s', 'emulator-5554', 'shell', 'input', 'tap', '300', '250'] in shell
    typed = next(c for c in shell if c[3:5] == ['input', 'text'])
    # Quoted as ONE device-side shell word, spaces encoded as %s.
    assert typed[5] == "'it'\"'\"'s%sa%stest;%srm%s-rf%s/'"
    opened = next(c for c in shell if c[3:5] == ['am', 'start'])
    assert opened[-1] == "'plotlinedemo://track?event=loma_test&x=1'"
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
    driver, calls = adb
    r = runner(driver, allowed_app_ids=['so.plotline.demo'])
    await r.call('launch', 'emulator-5554', {'app_id': 'so.plotline.demo'})
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
    assert tree['elements'] == [{'type': 'Button', 'text': 'Show modal', 'id': 'so.plotline.demo:id/show',
                                 'clickable': True, 'bounds': [100, 200, 500, 300], 'center': [300, 250]}]


def test_flow_screening():
    ok = 'appId: so.plotline.demo\n---\n- launchApp\n- tapOn: "Show modal"\n- assertVisible: "Hello"\n'
    ldr.screen_flow(ok, {})
    for bad in ['- runScript: x.js', '  - evalScript: ${http.get("http://10.0.0.1")}', '- runFlow: ../x.yaml',
                '- inputText: ${output.secret}', '- addMedia:\n  - ~/Pictures/a.png']:
        with pytest.raises(ldr.OpError):
            ldr.screen_flow(ok + bad, {})
    ldr.screen_flow(ok + '- runScript: x.js', {'allow_maestro_scripts': True})
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        ldr.screen_flow('appId: com.other\n---\n- launchApp', {'allowed_app_ids': ['so.plotline.demo']})


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
