"""Reuse campaign row export/bundle scripts and structured checkpoint writer."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.campaign_tools import write_checkpoint
from core.tools import AgentTools


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--campaign', default='storage/campaigns/smile-pre-publish')
    sub = p.add_subparsers(dest='action', required=True)
    cp = sub.add_parser('checkpoint')
    cp.add_argument('--message-id', required=True)
    cp.add_argument('--completed-action', required=True)
    cp.add_argument('--evidence', nargs='+', required=True)
    cp.add_argument('--extra', default='{}')
    export = sub.add_parser('export-row')
    export.add_argument('remote_dir')
    export.add_argument('job_id')
    export.add_argument('export_name')
    bundle = sub.add_parser('bundle-row')
    bundle.add_argument('export_name')
    bundle.add_argument('job_id')
    bundle.add_argument('suffix')
    bundle.add_argument('--dry', action='store_true')
    a = p.parse_args(argv)
    try:
        if os.environ.get('WORKFLOW_ROLE', 'operator') != 'operator':
            raise PermissionError('Only operator can use this helper')
        paths = AgentTools(a.workspace, 'operator')
        camp = paths.path(a.campaign)
        if a.action == 'checkpoint':
            print(json.dumps(write_checkpoint(a.workspace, a.campaign, message_id=a.message_id,
                completed_action=a.completed_action, evidence=a.evidence, extra=json.loads(a.extra))))
            return 0
        # Existing scripts enforce exact job identities, seal hashes and refusal
        # to overwrite/replay. This entry point adds no experiment launcher.
        for value in (a.job_id, a.export_name):
            if not value or not all(c.isalnum() or c in '-_.' for c in value) or value in {'.', '..'}:
                raise ValueError('Expected a simple job/export identifier')
        if a.action == 'export-row':
            script, args = 'export_row.py', [a.remote_dir, a.job_id, a.export_name]
        else:
            if not a.suffix.isalnum():
                raise ValueError('suffix must be alphanumeric')
            script, args = 'build_bundle_row.py', [a.export_name, a.job_id, a.suffix]
            if a.dry:
                args.append('--dry')
        # Historical row scripts target this campaign explicitly. Refuse a
        # redirected root rather than let an apparently isolated call touch it.
        if camp != Path(__file__).resolve().parents[1] / 'storage/campaigns/smile-pre-publish':
            raise ValueError('Existing row adapters are limited to the SMILE campaign')
        return subprocess.run([sys.executable, str(camp / 'claude_roles/operator' / script), *args], cwd=camp).returncode
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
