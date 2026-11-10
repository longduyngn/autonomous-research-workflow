"""P2.7 end-to-end acceptance.

  python scripts/verify_p2_e2e.py --offline
  python scripts/verify_p2_e2e.py --live --scenario kaggle-cpu   (needs Kaggle CLI auth; uses the user's Kaggle quota)

Offline (fakes only, no account): (a) orchestrator flow — plan approval on request, Codex quota hit with
boundary failover to Claude, independent validator, completed report; (b) compute flow — job submit through
the journal, model-free poller wake, checkpoint published to a shared store, light results collected.
Receipts go to storage/p2/acceptance/e2e/.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
RECEIPTS = ROOT / 'storage' / 'p2' / 'acceptance' / 'e2e'


def orchestrator_flow(tmp):
    from core.database import WorkspaceDatabase
    from core.logger import ContextLogManager
    from core.models import AgentRunResult
    from core.orchestrator import HybridOrchestrator
    from core.quota import QuotaStore, Snapshot

    example = json.loads((ROOT / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
    config = {k: json.loads(json.dumps(example[k])) for k in ('providers', 'roles', 'quota_routing')}
    config.update(logs_dir='logs', isolation={'enabled': True, 'directory': str(tmp / 'ws')})
    config['quota_routing']['enabled'] = True
    config['quota_routing']['classes']['worker'] = [{'provider': 'codex_cli', 'model': 'gpt-6.1-sol'},
                                                    {'provider': 'claude_cli', 'model': 'sonnet'},
                                                    {'provider': 'agy_headless_auto', 'model': 'claude-sonnet-5-5-medium'}]
    config['quota_routing']['ordered_classes'] = ['planner', 'worker']
    db = WorkspaceDatabase(tmp / 'db')
    QuotaStore(db).put([Snapshot('claude', 'five_hour', 5.0, time.time() + 3600, 'fixture', time.time())])
    calls = []

    class Runner:
        failed = False

        def run_role(self, role, task_prompt='', cli_log_path=None, provider_override=None, model_override=None):
            calls.append((role, provider_override))
            if role == 'operator' and provider_override == 'codex_cli' and not Runner.failed:
                Runner.failed = True
                return AgentRunResult(role, 'codex_cli', False, '', error='Provider quota exhausted (usage limit)',
                                      structured_data={'quota': {'exhausted': True, 'matched': 'usage limit',
                                                                 'resets_at': time.time() + 7200, 'account': 'codex'}})
            output = {'planner': 'Spec: write result.txt. Acceptance: file exists. Ralph items: 1.',
                      'validator': 'VERDICT: PASS\nEvidence: result.txt inspected'}.get(role, 'done')
            return AgentRunResult(role, provider_override or 'p', True, output,
                                  structured_data={'usage': {'input_tokens': 1, 'output_tokens': 1},
                                                   'run_id': f'{role}-{len(calls)}', 'model': model_override or ''})

    orch = HybridOrchestrator(tmp, config, db, ContextLogManager(tmp / 'logs'), Runner())
    task = orch.run_task_to_completion(orch.submit_task('write result.txt --approve-plan').id)
    steps = {'awaiting_approval': task.status == 'WAITING_APPROVAL'}
    with db._connect() as c:
        approval = c.execute("SELECT id FROM approvals WHERE task_id=? AND decision='PENDING'", (task.id,)).fetchone()
    orch.control.decide(approval['id'], 'local_user', 'APPROVED')
    orch.control.advance_waits()
    task = orch.run_task_to_completion(task.id)
    steps['quota_failover_wait'] = task.status == 'WAITING_PROVIDER' and orch.control.waits()[0]['dependency'] == ''
    with db._connect() as c:
        c.execute('UPDATE task_waits SET due=0')
    orch.control.advance_waits()
    task = orch.run_task_to_completion(task.id)
    providers = {}
    for role, provider in calls:
        providers.setdefault(role, []).append(provider)
    steps.update(completed=task.status == 'COMPLETED', retries_consumed=task.retry_count,
                 operator_providers=providers.get('operator'), validator_provider=providers.get('validator', [None])[-1],
                 validator_independent=providers.get('validator', [None])[-1] not in (providers.get('operator') or [None])[-1:])
    steps['passed'] = (steps['awaiting_approval'] and steps['quota_failover_wait'] and steps['completed']
                       and steps['retries_consumed'] == 0 and steps['operator_providers'] == ['codex_cli', 'claude_cli']
                       and steps['validator_independent'])
    return steps


def compute_flow(tmp):
    from core.artifact_store import LocalDirStore, publish_checkpoint
    from core.compute_jobs import JobController, JobRegistry, JobSpec, OutputDecl
    from core.compute_poller import ComputePoller
    from core.control import ControlPlane
    from core.database import WorkspaceDatabase
    from core.results import collect_results
    from fakes.compute_adapter import FakeJobAdapter

    db = WorkspaceDatabase(tmp / 'compute-db')
    task = db.create_task('mini-ddpm')
    store = LocalDirStore(tmp / 'drive')
    adapter = FakeJobAdapter(states=['RUNNING', 'RUNNING', 'COMPLETED'])
    registry = JobRegistry()
    registry.register('kaggle', adapter)
    now = [1000.0]
    jobs = JobController(db, registry, clock=lambda: now[0])
    handle, outcome = jobs.submit(JobSpec(task_id=task.id, backend='kaggle', entry='train.ipynb',
                                          checkpoint_uri='file:runs/1/ddpm',
                                          outputs=(OutputDecl('metrics.json', 'metrics', True),)), 'ddpm-1')
    control = ControlPlane(db)
    control.wait(task.id, 'REMOTE', 'OPERATING', 'training', handle.job_id, delay=10 ** 9, timeout=None)
    local = tmp / 'job-out'
    local.mkdir()
    (local / 'state.pt').write_bytes(b'weights' * 5000)
    (local / 'metrics.json').write_text('{"loss": 0.04}', encoding='utf-8')
    manifest = publish_checkpoint(store, 'runs/1/ddpm', 'ck-final', [local / 'state.pt', local / 'metrics.json'],
                                  {'task_id': task.id, 'logical_run_id': 'ddpm', 'job_id': handle.job_id,
                                   'backend': 'kaggle', 'global_step': 2000}, kinds={'metrics.json': 'metrics'},
                                  light=('metrics.json',))
    poller = ComputePoller(db, jobs, control=control, clock=lambda: now[0], interval=1)
    wakes = []
    for _ in range(6):
        now[0] += 2
        wakes += poller.tick()
    results = collect_results(store, manifest, tmp / 'results')
    states = {Path(a['uri']).name: a['state'] for a in results['artifacts']}
    steps = {'submit_outcome': outcome, 'wakes': [w.split(':')[1] for w in wakes],
             'task_after_wake': db.get_task(task.id).status, 'artifact_states': states,
             'checkpoint_left_remote': not (Path(results['directory']) / 'state.pt').exists(),
             'submit_effects': adapter.effects}
    steps['passed'] = (outcome == 'submitted' and steps['wakes'] == ['job_finished'] and steps['task_after_wake'] == 'OPERATING'
                       and states == {'state.pt': 'remote_verified', 'metrics.json': 'local_fetched'}
                       and steps['checkpoint_left_remote'] and adapter.effects == 1)
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--scenario', default='kaggle-cpu', choices=['kaggle-cpu', 'colab-to-kaggle', 'kaggle-to-colab', 'discord-status'])
    args = parser.parse_args()
    if args.live:
        print('DEFERRED: live scenarios need the user-approved sample job and live accounts '
              '(Kaggle CLI auth, Colab browser bridge, Discord). Run them deliberately; see docs/ralph-p2-20261110/HANDOFF.md.')
        sys.exit(2)
    if not args.offline:
        parser.error('choose --offline or --live')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'a').mkdir()
        (tmp / 'b').mkdir()
        report = {'orchestrator': orchestrator_flow(tmp / 'a'), 'compute': compute_flow(tmp / 'b')}
    report['passed'] = report['orchestrator']['passed'] and report['compute']['passed']
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    receipt = RECEIPTS / f"{time.strftime('%Y%m%d-%H%M%S')}-offline.json"
    receipt.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps(report, indent=2, default=str))
    print(f"offline e2e: {'PASS' if report['passed'] else 'FAIL'} receipt={receipt}")
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
