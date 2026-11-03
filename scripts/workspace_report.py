"""Generate and check a factual report of the assigned task snapshot."""
import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def render(data):
    return ("# Measured workspace state\n\n"
        f"Observed at (UTC): {data['observed_at_utc']}\n\n"
        f"Workspace: `{data['workspace']}`\n\n"
        f"Python: `{data['python_executable']}` ({data['python_version']})\n\n"
        f"Platform: {data['platform']}\n\n"
        "Core Python files observed on disk:\n\n" + "\n".join(f"- `{p}`" for p in data['core_files'])
        + f"\n\nGit branch at observation: `{data['git_branch']}`\n\n"
        f"Git commit at observation: `{data['git_commit']}`\n\n"
        "This describes this task's isolated snapshot. It does not assert controller health, "
        "test success, or completion of any cloud experiment.\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    output = root / 'output'
    evidence = output / 'workspace_state.json'
    report = output / 'workspace_state.md'
    actual_files = sorted(str(p.relative_to(root).as_posix()) for p in (root / 'core').glob('*.py'))
    if args.check:
        data = json.loads(evidence.read_text(encoding='utf-8'))
        assert data['core_files'] == actual_files, 'Report does not match actual core files'
        assert data['workspace'] == str(root)
        assert data['python_version'] == platform.python_version()
        assert data['python_executable'] == sys.executable
        assert datetime.fromisoformat(data['observed_at_utc']).tzinfo is not None
        assert report.read_text(encoding='utf-8') == render(data), 'Report differs from measured evidence'
        print('PASS: report matches measured workspace files and Python environment')
        return
    def git(*argv):
        result = subprocess.run(['git', *argv], cwd=root, capture_output=True, text=True, timeout=20)
        return result.stdout.strip() if result.returncode == 0 else 'unavailable'
    data = {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'workspace': str(root),
            'python_executable': sys.executable, 'python_version': platform.python_version(),
            'platform': platform.platform(), 'core_files': actual_files,
            'git_branch': git('branch', '--show-current'), 'git_commit': git('rev-parse', 'HEAD')}
    output.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(data, indent=2), encoding='utf-8')
    report.write_text(render(data), encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
