"""Bind existing Antigravity lifecycle hooks to this checkout and interpreter."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid


def configure(root, interpreter):
    path = root / '.agents/hooks.json'
    original = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {
        'cloud-colab-kaggle-lifecycle':{'enabled':True,
          'SessionStart':[{'type':'command','command':'python scripts/cloud_hooks.py SessionStart','timeout':10}],
          'PreToolUse':[{'matcher':'run_command|call_mcp_tool|mcp_ck_nb_.*','hooks':[
              {'type':'command','command':'python scripts/cloud_hooks.py PreToolUse','timeout':10}]}]}}
    updated = copy.deepcopy(original)
    lifecycle = updated['cloud-colab-kaggle-lifecycle']
    script = root / 'scripts/cloud_hooks.py'
    if not Path(interpreter).is_file() or not script.is_file():
        raise ValueError('Interpreter or hook script missing')
    for event in ('SessionStart', 'PreToolUse'):
        entries = lifecycle[event]
        for entry in entries:
            for hook in entry.get('hooks', [entry]):
                if 'scripts/cloud_hooks.py' in hook.get('command', '').replace('\\', '/'):
                    argv = [str(interpreter), str(script), event]
                    hook['command'] = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
    return path, original, updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Update launcher paths, preserving policy and backing up original')
    parser.add_argument('--check', action='store_true', help='Probe configured PreToolUse from repo and .agents cwd without cloud actions')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path, original, updated = configure(root, Path(sys.executable).resolve())
    if args.apply and original != updated:
        backup = root / 'storage/hook-backups' / (uuid.uuid4().hex + '.json')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():backup.write_bytes(path.read_bytes())
        path.parent.mkdir(exist_ok=True,parents=True)
        path.write_text(json.dumps(updated, indent=2) + '\n', encoding='utf-8')
        print('Launcher updated; original saved to ' + str(backup.relative_to(root)))
    if args.check:
        current = json.loads(path.read_text(encoding='utf-8-sig'))
        command = current['cloud-colab-kaggle-lifecycle']['PreToolUse'][0]['hooks'][0]['command']
        for cwd in (root, root / '.agents'):
            result = subprocess.run(command if os.name == 'nt' else shlex.split(command), cwd=cwd,
                input=json.dumps({'toolCall': {'name': 'read_file', 'args': {}}}),
                env={**os.environ, 'WORKFLOW_NO_ENV': '1'}, capture_output=True, text=True, timeout=10)
            if result.returncode or json.loads(result.stdout).get('decision') != 'allow':
                raise RuntimeError('Hook failed from ' + str(cwd) + ': ' + result.stderr)
            print('PreToolUse OK from ' + str(cwd))
    elif not args.apply:
        print(json.dumps(updated, indent=2))


if __name__ == '__main__':
    main()
