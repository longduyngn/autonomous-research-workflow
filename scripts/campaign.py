"""One provider-neutral entry point for campaign digest, status and checkpoints."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.campaign_tools import status_digest, update_status, write_checkpoint


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', default=os.environ.get('WORKFLOW_WORKSPACE_ROOT', '.'))
    p.add_argument('--campaign', default='.')
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('status_digest')
    status = sub.add_parser('update-status')
    status.add_argument('--changes', required=True, help='JSON object of changed top-level fields')
    checkpoint = sub.add_parser('checkpoint')
    checkpoint.add_argument('--message-id', required=True)
    checkpoint.add_argument('--completed-action', required=True)
    checkpoint.add_argument('--evidence', nargs='+', required=True)
    checkpoint.add_argument('--extra', default='{}')
    a = p.parse_args()
    try:
        if a.action == 'status_digest':
            print(status_digest(a.workspace, a.campaign))
            return 0
        if os.environ.get('WORKFLOW_ROLE', 'operator') != 'operator':
            raise PermissionError('Only operator may update campaign status/checkpoints')
        if a.action == 'update-status':
            result = update_status(a.workspace, a.campaign, json.loads(a.changes))
        else:
            result = write_checkpoint(a.workspace, a.campaign, message_id=a.message_id,
                completed_action=a.completed_action, evidence=a.evidence, extra=json.loads(a.extra))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
