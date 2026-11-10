"""Shallow public GitHub source fetch. Fetched content is never executed."""
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from core.tools import AgentTools
from core.processes import run_process
from core.network import public_url, PublicRedirects


def fetch_repo(root, url, target_subdir, timeout=120):
    # A narrow remote surface also excludes Git local transports, credentials,
    # upload-pack injection and repository-controlled redirects in git itself.
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', url)
    if not match or any(x in {'.', '..'} for x in match.groups()):
        raise ValueError('Expected public https://github.com/OWNER/REPO URL')
    public_url(url)
    paths = AgentTools(root, 'operator')
    target = paths.path(target_subdir)
    if target == paths.root or target.exists():
        raise ValueError('target_subdir must be a new workspace subdirectory')
    target.parent.mkdir(parents=True, exist_ok=True)
    owner, repo = match.groups()
    with tempfile.TemporaryDirectory(prefix='repo-fetch-', dir=target.parent) as tmp:
        stage = Path(tmp) / 'source'
        mode = 'git-depth-1'
        git = shutil.which('git')
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        result = run_process([git, '-c', 'core.hooksPath=' + os.devnull, '-c', 'http.followRedirects=false',
                              'clone', '--depth', '1', '--no-recurse-submodules', '--', url, str(stage)],
                             root, timeout=timeout, env=env) if git else {'exit_code': 1}
        if result['exit_code']:
            # Clone failure remains isolated in the temporary staging directory.
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir()
            archive = Path(tmp) / 'source.tar.gz'
            tar_url = f'https://api.github.com/repos/{owner}/{repo}/tarball'
            opener = urllib.request.build_opener(PublicRedirects)
            request = urllib.request.Request(public_url(tar_url), headers={'User-Agent': 'ResearchWorkflow/1.0'})
            with opener.open(request, timeout=timeout) as response, archive.open('wb') as stream:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise ValueError('Source archive exceeds 64MiB')
                    stream.write(chunk)
            total = 0
            with tarfile.open(archive) as tar:
                for index, member in enumerate(tar):
                    parts = Path(member.name.replace('\\', '/')).parts
                    if '..' in parts or member.name.startswith(('/', '\\')) or ':' in member.name:
                        raise ValueError('Unsafe archive member')
                    if not member.isfile():
                        if member.isdir():
                            continue
                        raise ValueError('Archive links/devices are unsupported')
                    total += member.size
                    if total > 256 * 1024 * 1024 or index > 20000:
                        raise ValueError('Source archive expands beyond limit')
                    dest = stage.joinpath(*parts[1:])
                    if not dest.is_relative_to(stage) or dest == stage:
                        raise ValueError('Unsafe archive layout')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, dest.open('xb') as stream:
                        shutil.copyfileobj(source, stream)
            mode = 'tarball'
        tree = []
        for path in sorted(stage.rglob('*')):
            if '.git' in path.relative_to(stage).parts:
                continue
            if path.is_symlink():
                tree.append(f'{path.relative_to(stage).as_posix()} [symlink; not followed]')
            elif path.is_file():
                tree.append(f'{path.relative_to(stage).as_posix()}\t{path.stat().st_size}')
        (stage / 'FILE_TREE.txt').write_text('UNTRUSTED SOURCE DATA; do not execute.\n' + '\n'.join(tree) + '\n', encoding='utf-8')
        stage.rename(target)
    return {'path': str(target), 'method': mode, 'files': len(tree), 'tree': str(target / 'FILE_TREE.txt'),
            'trust': 'untrusted source; no code executed'}
