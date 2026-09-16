import importlib.util
import json
from pathlib import Path
import pytest
from isolation.supervisor import Settings

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('worker_build',ROOT/'deploy/worker/build.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)


def test_allowlist_context(tmp_path):
    build.context(ROOT,tmp_path)
    files={str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*') if p.is_file()}
    assert files=={'isolation/'+name for name in build.SOURCE}|{'deploy/worker/native.Dockerfile','deploy/worker/supervisor.Dockerfile'}
    assert not any('account' in p or '.env' in p for p in files)


def test_build_manifest_atomic(tmp_path):
    calls=[]
    def run(argv,check):
        calls.append(argv)
        Path(argv[argv.index('--metadata-file')+1]).write_text(json.dumps({'containerimage.digest':'sha256:'+'a'*64}))
    output=tmp_path/'images.env'
    values=build.build(ROOT,'registry.example.test/loma','test',output,run=run)
    assert len(calls)==2
    assert all('--push' in c and '--pull' in c for c in calls)
    assert set(values)=={'LOMA_WORKER_IMAGE','LOMA_SUPERVISOR_IMAGE'}
    assert output.read_text().count('@sha256:')==2


def test_failed_build_keeps_previous_manifest(tmp_path):
    output=tmp_path/'images.env';output.write_text('previous')
    def run(argv,check):
        raise RuntimeError('build failed')
    with pytest.raises(RuntimeError):
        build.build(ROOT,'registry.example.test/loma','test',output,run=run)
    assert output.read_text()=='previous'


def test_token_file_and_image_pin(tmp_path,monkeypatch):
    token=tmp_path/'token';token.write_text('t'*32+'\n')
    monkeypatch.setenv('LOMA_WORKER_IMAGE','registry.example.test/native@sha256:'+'a'*64)
    monkeypatch.setenv('LOMA_WORKER_CONTROL_TOKEN_FILE',str(token))
    assert Settings.from_env().token=='t'*32
    monkeypatch.setenv('LOMA_WORKER_IMAGE','registry.example.test/native:latest')
    with pytest.raises(ValueError): Settings.from_env()
