"""Quota router CLI: status/refresh/route plus the opt-in Claude status-line installer.

    python scripts/quota.py status [--json]
    python scripts/quota.py route operator [--task ID] [--requires claude]
    python scripts/quota.py install-claude-statusline --check     (default; changes nothing)
    python scripts/quota.py install-claude-statusline --apply     (backs up, chains an existing statusLine)
    python scripts/quota.py remove-claude-statusline --apply

Never reads credential files. `--settings` selects the Claude settings file (default ~/.claude/settings.json).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('status', 'refresh'):
        sub.add_parser(name).add_argument('--json', action='store_true')
    route = sub.add_parser('route')
    route.add_argument('role')
    route.add_argument('--task', type=int, default=None)
    route.add_argument('--requires', action='append', default=[])
    route.add_argument('--json', action='store_true')
    for name in ('install-claude-statusline', 'remove-claude-statusline'):
        item = sub.add_parser(name)
        item.add_argument('--check', action='store_true', help='report only (default)')
        item.add_argument('--apply', action='store_true', help='write the settings file (after backing it up)')
        item.add_argument('--settings', default=str(Path.home() / '.claude' / 'settings.json'))
    args = parser.parse_args(argv)
    if args.action.endswith('claude-statusline'):
        from core.quota_cli import install_statusline
        if args.apply and args.check:
            parser.error('choose --check or --apply')
        print(json.dumps(install_statusline(args.settings, ROOT, apply=args.apply, remove=args.action.startswith('remove')), indent=2))
        return 0
    from main import load_config, WORKSPACE_ROOT
    from core.database import WorkspaceDatabase
    from core.quota_cli import run
    config = load_config()
    run(args, config, WORKSPACE_ROOT, WorkspaceDatabase(WORKSPACE_ROOT / config.get('database_path', 'storage/events.db')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
