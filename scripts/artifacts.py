"""Provider-neutral artifact registration and verification."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.artifacts import register, verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['register', 'verify'])
    parser.add_argument('path', nargs='?')
    parser.add_argument('--workspace', default=os.environ.get('WORKFLOW_WORKSPACE_ROOT', '.'))
    parser.add_argument('--name', default='')
    parser.add_argument('--metadata', default='{}')
    args = parser.parse_args()
    try:
        if args.action == 'register':
            if os.environ.get('WORKFLOW_ROLE') in {'communicator', 'researcher', 'guardian'}:
                raise PermissionError('This role cannot register artifacts')
            if not args.path:
                parser.error('register requires path')
            result = register(args.workspace, args.name, args.path, json.loads(args.metadata))
        else:
            result = verify(args.workspace)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok', True) else 1
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
