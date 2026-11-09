"""Read-only audit of the Ralph handoff and saved artifacts; never contacts services."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIED = {'source_verified', 'reused_verified', 'live_verified'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def check_review(task, evidence, ledger, digest):
    name = task['id']
    require(evidence.get('task_id') == name, f'{name}: mismatched evidence ID')
    require(evidence.get('review_passed') is True, f'{name}: review did not pass')
    require(evidence.get('status') in VERIFIED, f'{name}: unverified status')
    require(not task['live_gate'] or evidence['status'] == 'live_verified', f'{name}: missing live evidence')
    checks = evidence.get('checks')
    require(isinstance(checks, list) and bool(checks), f'{name}: missing checks')
    require(all(isinstance(c, dict) and c.get('passed') is True and c.get('exit_code') == 0
                for c in checks), f'{name}: failed or incomplete check')
    require(ledger.get('status') == evidence['status'], f'{name}: ledger status differs')
    require(ledger.get('evidence_sha256') == digest, f'{name}: evidence changed after review')


def check_artifact(base, receipt):
    base = Path(base).resolve()
    path = (base / receipt['path']).resolve()
    require(path.is_relative_to(base), 'Artifact escapes workspace')
    content = path.read_bytes()
    require(len(content) == receipt['bytes'], f'{path.name}: size mismatch')
    require(hashlib.sha256(content).hexdigest() == receipt['sha256'], f'{path.name}: hash mismatch')


def check_continuity(data):
    for provider, item in data.items():
        a, b, c = item['wake1'], item['wake2'], item['wake3_rotated']
        require(all(w['success'] is True and w.get('session_id') for w in (a, b, c)), f'{provider}: failed wake')
        require(a['session_id'] == b['session_id'] and b['resume_requested'] is True,
                f'{provider}: session was not resumed')
        require(a['generation'] == b['generation'] < c['generation'] and c['session_id'] != b['session_id'],
                f'{provider}: rotation did not occur')
        require(c['handover']['previous_generation'] == b['generation'], f'{provider}: missing handover')


def audit(root):
    root = Path(root)
    tasks = read(root / 'docs/ralph-next/tasks.json')
    for relative, digest in read(root / 'docs/ralph-next/manifest.json')['files_sha256'].items():
        require(hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest,
                f'Handoff manifest differs: {relative}')
    verdict = (root / 'docs/ralph-next/FINAL_VERDICT_20261104.md').read_text(encoding='utf8')
    state = root / 'storage/ralph-next'
    latest = {}
    for line in (state / 'ledger.jsonl').read_text(encoding='utf8').splitlines():
        row = json.loads(line)
        latest[row['task_id']] = row
    ids = {t['id'] for t in tasks['tasks']}
    require(len(ids) == len(tasks['tasks']) == tasks['task_count'], 'Task count/IDs differ')
    for task in tasks['tasks']:
        path = state / 'evidence' / (task['id'] + '.json')
        evidence = read(path)
        check_review(task, evidence, latest.get(task['id'], {}), hashlib.sha256(path.read_bytes()).hexdigest())
        require(f"| `{task['id']}` | `{task['profile']}` | `{evidence['status']}` |" in verdict,
                f"{task['id']}: verdict matrix differs")
        require(all(d in ids and latest.get(d, {}).get('status') in VERIFIED for d in task['depends_on']),
                f"{task['id']}: unverified dependency")

    probes = state / 'probes'
    for name in ('B02', 'B03'):
        receipt = read(probes / name / 'receipt.json')
        workspace = probes / name / 'workspaces/task_00001'
        require(receipt['success'] is True and receipt['exit_code'] == 0, f'{name}: failed CLI')
        require(read(workspace / 'fixture-result.json') == receipt['fixture_result'], f'{name}: fixture differs')
        require(receipt['fixture_result']['sum'] == 19, f'{name}: wrong sum')
        require({'PreToolUse', 'PostToolUse', 'Stop'} <= set(receipt['structured_data']['native_hook_events']),
                f'{name}: missing native hooks')
    check_continuity(read(probes / 'C05/receipt.json'))

    d05 = read(probes / 'D05/receipt.json')
    d04 = read(probes / 'D04/receipt.json')
    store = root / d04['destination']['store_dir_relative'] / d05['phase1']['published_checkpoint_id']
    manifest = read(store / 'manifest.json')
    require(manifest == d05['phase1']['published_manifest'], 'D05: durable manifest differs')
    for receipt in manifest['files']:
        check_artifact(store / 'files', receipt)
    for directory, expected in [('downloaded_step3', d05['reference_step3_sha256']),
                                ('restored_from_durable/downloaded_step3', d05['reference_step3_sha256']),
                                ('final_resumed_step6', d05['reference_step6_sha256']),
                                ('reference_uninterrupted', d05['reference_step6_sha256'])]:
        for name, digest in expected.items():
            path = probes / 'D05/workspace' / directory / name
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'D05: {directory}/{name} differs')
    require(d05['phase2']['ownership_generation'] > d05['phase1']['ownership_generation'], 'D05: stale generation')
    require(d05['phase1']['published_checkpoint_id'] == d05['phase2']['restored_checkpoint_id'], 'D05: checkpoint differs')
    for phase in ('phase1', 'phase2'):
        observation = d05[phase]['shutdown_observation']
        require(observation['runtime_stopped'] is True and observation['active_session_count'] == 0,
                'D05: missing shutdown observation')

    f03 = read(probes / 'F03/receipt.json')
    workspace = probes / 'F03/workspaces/task_00001'
    batch = read(probes / 'F03/sensor_batches.json')['fallback_calibrated_batch']
    expected = dict(source='fallback_calibrated_batch', mean=sum(batch)/len(batch), sum=sum(batch), count=len(batch))
    require(read(workspace / 'step2_metrics.json') == f03['step2_metrics'] == expected, 'F03: incorrect metrics')
    require(f03['plan_revision'] == 2 and f03['event_review_count'] == 1, 'F03: missing replan')
    researchers = [r['session_id'] for r in f03['agent_runs'] if r['role'] == 'researcher']
    require(len(researchers) == 2 and researchers[0] == researchers[1], 'F03: researcher session differs')

    j03 = read(probes / 'J03/receipt.json')
    workspace = probes / 'J03/workspaces/task_00001'
    summary = read(workspace / 'telemetry_summary.json')
    require(summary == j03['telemetry_summary'], 'J03: summary differs')
    rows = read(probes / 'J03/telemetry_runs.json')['runs']
    for model in {r['model'] for r in rows}:
        group = [r for r in rows if r['model'] == model]
        expected = dict(count=len(group), mean_latency_ms=sum(r['latency_ms'] for r in group)/len(group),
                        max_latency_ms=max(r['latency_ms'] for r in group),
                        accuracy=sum(r['correct'] for r in group)/len(group), total_tokens=sum(r['tokens'] for r in group))
        require(summary[model] == expected, f'J03: incorrect {model} metrics')
    require(bool(j03['artifact_receipts']), 'J03: missing artifact hashes')
    for receipt in j03['artifact_receipts']:
        check_artifact(workspace, receipt)
    for relative in read(probes / 'I03/receipt.json')['artifacts'].values():
        require((root / relative).is_file(), f'I03: missing {relative}')
    return {'task_records_verified': len(ids), 'saved_artifacts_verified': True,
            'limitations': ['Historical receipts do not establish current account/service availability.',
                            'J03 synthetic ingress and undelivered replies do not prove real Discord delivery.',
                            'D05 has no second boot ID; distinct VM replacement is not established.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = audit(args.root)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        parser.exit(1, f'Audit failed: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
