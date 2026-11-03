"""Freeze an already-installed, revision-verified Colab MCP environment for startup.

No downloads, logins or GPU allocation. The original uvx bootstrap command is
retained in configuration for an explicit future reinstall.
"""
import argparse
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from main import load_config, save_config
from core.portability import environment_python,site_packages

REVISION = 'b9ab3899e0f1fa493390b1fd6d54aa2e464ecdf1'


def verify(environment):
    metadata = [p for directory in site_packages(environment) for p in directory.glob('colab_mcp-*.dist-info/direct_url.json')]
    if not metadata or json.loads(metadata[0].read_text())['vcs_info']['commit_id'] != REVISION:
        raise ValueError('Colab MCP source revision does not match the reviewed revision')
    code = ('import colab_mcp, fastmcp, platformdirs, json; from importlib.metadata import distributions; '
            'print(json.dumps({d.metadata["Name"]:d.version for d in distributions()}))')
    result = subprocess.run([str(environment_python(environment)), '-c', code],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('Installed runtime cannot import Colab MCP and its dependencies')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Existing installed uv environment; default discovers a matching cache environment')
    args = parser.parse_args()
    target = ROOT / 'storage/tools/colab-runtime'
    source = None
    if not target.exists():
        candidates = [args.source.resolve()] if args.source else [p.parent for p in sorted(
            (ROOT / 'storage/tools/uv/archive-v0').glob('*/pyvenv.cfg'), key=lambda p:p.stat().st_mtime, reverse=True)]
        for candidate in candidates:
            try:
                verify(candidate)
                source = candidate
                break
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
                continue
        if source is None:
            raise SystemExit('No valid installed Colab MCP environment found. Perform an explicit approved bootstrap install first.')
        staging = target.with_name('colab-runtime.installing-' + uuid.uuid4().hex)
        shutil.copytree(source, staging, ignore=shutil.ignore_patterns('.env', '*.env'))
        verify(staging)
        staging.rename(target)
    versions = verify(target)
    config = load_config()
    cloud = config['cloud']
    cloud.setdefault('colab_bootstrap_command', cloud['colab_command'])
    executable=environment_python(target).relative_to(ROOT).as_posix()
    cloud['colab_command'] = ['{controller}/'+executable,
                              '-c', 'from colab_mcp import main; main()']
    cloud['colab_revision'] = REVISION
    save_config(config)
    record = {'source_environment':str(source) if source else 'existing stable runtime',
              'revision':REVISION, 'verified_at_utc':datetime.now(timezone.utc).isoformat(),
              'dependency_versions':versions, 'bootstrap_command':cloud['colab_bootstrap_command']}
    (target / 'workflow-runtime.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    (target / 'requirements.lock.txt').write_text('\n'.join(f'{name}=={version}' for name,version in sorted(versions.items()))+'\n',encoding='utf-8')
    print(json.dumps({'runtime':str(target),'revision':REVISION,'dependencies':len(versions),'downloads':False}))


if __name__ == '__main__':
    main()
