#!/usr/bin/env python3
"""Build/publish isolated images from a temporary allowlisted context.

Run on the approved build host with an already-authenticated Docker client.
No backend env, credentials, .git, caches, or account stores enter the context.
Writes digest-only deployment references after BOTH builds succeed.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SOURCE = ('__init__.py', 'protocol.py', 'model_bridge.py', 'codex_worker.py',
          'claude_worker.py', 'opencode_worker.py', 'mcp_bridge.py', 'worker_entry.py',
          'workspace_tools.py', 'workspace.py', 'artifacts.py', 'supervisor.py')


def context(root, destination):
    (destination/'isolation').mkdir(parents=True)
    (destination/'deploy/worker').mkdir(parents=True)
    for name in SOURCE:
        shutil.copyfile(root/'isolation'/name, destination/'isolation'/name)
    for name in ('native.Dockerfile', 'supervisor.Dockerfile'):
        shutil.copyfile(root/'deploy/worker'/name, destination/'deploy/worker'/name)


def build(root, repository, tag, output, *, run=subprocess.run):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._:/-]*', repository) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', tag):
        raise ValueError('Invalid image repository or tag')
    result = {}
    with tempfile.TemporaryDirectory(prefix='loma-worker-build-') as tmp:
        folder = Path(tmp); context(root,folder)
        for kind, key in (('native','LOMA_WORKER_IMAGE'),('supervisor','LOMA_SUPERVISOR_IMAGE')):
            ref = repository+'/'+kind
            metadata = folder/(kind+'-metadata.json')
            run(['docker','buildx','build','--platform','linux/amd64','--pull','--push',
                '--tag',ref+':'+tag,'--metadata-file',str(metadata),
                '--file',str(folder/'deploy/worker'/f'{kind}.Dockerfile'),str(folder)],check=True)
            digest = json.loads(metadata.read_text()).get('containerimage.digest','')
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):
                raise ValueError('Build did not return an immutable image digest')
            result[key] = ref+'@'+digest
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    # Atomic replacement: a partial build never overwrites the last good manifest.
    with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,delete=False) as stream:
        stream.write(''.join(k+'='+v+'\n' for k,v in result.items()))
        staged = Path(stream.name)
    staged.replace(output)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True,help='Approved registry prefix, e.g. registry.example.test/loma')
    parser.add_argument('--tag',required=True)
    parser.add_argument('--output',required=True,help='Digest-only env file; contains no secrets')
    args=parser.parse_args()
    build(Path(__file__).resolve().parents[2],args.repository,args.tag,args.output)
