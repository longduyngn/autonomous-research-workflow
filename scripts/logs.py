"""Bounded log views for native CLI roles."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.tools import AgentTools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['tail_log', 'search_log'])
    parser.add_argument('path')
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--pattern', default='')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()
    parameters = {'path': args.path}
    parameters.update({'n_lines': args.limit} if args.action == 'tail_log' else
                      {'pattern': args.pattern, 'max_matches': args.limit})
    try:
        print(json.dumps(AgentTools(args.workspace, 'communicator').call(args.action, parameters)))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
