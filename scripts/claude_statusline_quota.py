"""Claude Code status-line command that records rate limits for the quota router.

Claude Code pipes a JSON document to this command on stdin. Only the
`rate_limits` object (five_hour / seven_day usage and reset times) is copied to
storage/quota/claude_statusline.json; nothing else from the payload is stored.
An optional previously configured status line is chained so it keeps rendering.
Install/remove only through `python scripts/quota.py install-claude-statusline`.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def capture(data, directory):
    """Write the rate_limits slice atomically; returns the limits dict or None."""
    limits = data.get('rate_limits') if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'claude_statusline.json'
    fd, name = tempfile.mkstemp(dir=directory, suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump({'captured_at': time.time(), 'rate_limits': limits}, stream)
    os.replace(name, target)
    return limits


def short_line(limits):
    parts = []
    for key, label in (('five_hour', '5h'), ('seven_day', '7d')):
        item = (limits or {}).get(key)
        if isinstance(item, dict) and isinstance(item.get('used_percentage'), (int, float)):
            parts.append(f"{label} {item['used_percentage']:.0f}%")
    return 'claude ' + ' '.join(parts) if parts else 'claude'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default=str(ROOT / 'storage/quota'))
    parser.add_argument('--chain-base64', default='', help='previous statusLine command, base64 (set by the installer)')
    args = parser.parse_args(argv)
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    try:
        limits = capture(data, args.directory)
    except OSError:
        limits = None  # A status line must never fail the session.
    if args.chain_base64:
        try:
            command = base64.b64decode(args.chain_base64).decode('utf-8')
            done = subprocess.run(command, shell=True, input=raw, capture_output=True, text=True, timeout=5)
            sys.stdout.write(done.stdout)
            return 0
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    sys.stdout.write(short_line(limits))
    return 0


if __name__ == '__main__':
    sys.exit(main())
