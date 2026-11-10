"""P2 autopilot: continue the approved P2 plan when the interactive coordinator stops.

A scheduled, model-free tick (Windows Task Scheduler, every 10 minutes):

1. exits when P2 is done, another autopilot run holds the lock, the interactive
   coordinator heartbeat is fresh, or a recorded wait has not expired;
2. asks the quota router for the planner/frontier route (Claude Opus, Codex
   Astra, AGY Opus; reserve AGY Argon) and skips accounts that are exhausted
   or locally cooled down;
3. runs ONE bounded coordinator session in the repository with the handoff
   prompt; the agent edits files but never commits;
4. runs gate G itself (unit tests, doctor, diff check) and commits only when it
   passes. On a quota error the account is marked exhausted and the next route
   is tried in the same tick.

Commands: ``tick`` (scheduled), ``heartbeat`` (interactive coordinator),
``status``, ``install``/``uninstall`` (scheduled task), ``done``.
State lives in storage/p2/autopilot/ (ignored by git). Credentials are never read.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = ROOT / 'storage' / 'p2' / 'autopilot'
HEARTBEAT = ROOT / 'storage' / 'p2' / 'coordinator.heartbeat'
PLAN_DIR = ROOT / 'docs' / 'ralph-p2-20261110'
TASK_NAME = 'AntigravityP2Autopilot'
PYTHON = ROOT / '.conda' / 'python.exe'
DEFAULTS = {'heartbeat_stale_seconds': 2700, 'run_timeout_seconds': 10800, 'max_routes_per_tick': 3,
            'failure_cooldown_seconds': 3600, 'gate_timeout_seconds': 1800}


def now():
    return time.time()


def append(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / name).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'at_unix': now(), **value}, ensure_ascii=False) + '\n')


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', f'PID eq {int(pid)}', '/NH'], capture_output=True, text=True)
        return str(int(pid)) in out.stdout
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def heartbeat_age():
    try:
        return now() - HEARTBEAT.stat().st_mtime
    except OSError:
        return None


def touch_heartbeat(note=''):
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({'at_unix': now(), 'pid': os.getpid(), 'note': note}), encoding='utf-8')


def gate_should_skip(settings):
    """-> reason string when this tick must not start work, else None."""
    if (STATE / 'DONE').exists():
        return 'p2_done'
    lock = read_json(STATE / 'lock.json', {})
    if lock and pid_alive(lock.get('pid')):
        return 'autopilot_running'
    if lock:
        append('ledger.jsonl', {'event': 'stale_lock_cleared', 'lock': lock})
        (STATE / 'lock.json').unlink(missing_ok=True)
    age = heartbeat_age()
    if age is not None and age < settings['heartbeat_stale_seconds']:
        return f'coordinator_active ({int(age)}s ago)'
    wait = read_json(STATE / 'wait.json', {})
    if wait.get('until', 0) > now():
        return f"waiting_for_quota until {time.strftime('%Y-%m-%d %H:%M', time.localtime(wait['until']))}"
    return None


def local_cooldowns():
    data = read_json(STATE / 'cooldowns.json', {})
    return {k: v for k, v in data.items() if v > now()}


def set_cooldown(key, until):
    data = local_cooldowns()
    data[key] = max(until, data.get(key, 0))
    write_json(STATE / 'cooldowns.json', data)


def build_router():
    from main import load_config
    from core.database import WorkspaceDatabase
    from core.quota import QuotaRouter, QuotaStore
    config = load_config()
    database = Path(config.get('database_path', 'storage/workspace.db'))
    if not database.is_absolute():
        database = ROOT / database
    store = QuotaStore(WorkspaceDatabase(database))
    cooldowns = local_cooldowns()
    return config, store, QuotaRouter(config, store, now, lambda key: cooldowns.get(key, 0))


def prompt_text(run_dir, previous):
    lines = [
        'You are the autonomous P2 COORDINATOR for this repository (the interactive Claude Code coordinator stopped,',
        'for example because its quota ran out). The user approved the P2 plan and asked for fully automatic execution.',
        '',
        'Read first: CLAUDE.md, docs/ralph-p2-20261110/EXECUTION_LOG.md, docs/ralph-p2-20261110/USER_DECISIONS_VI.md',
        '(overrides), docs/ralph-p2-20261110/PLANNER_PLAN_VI.md, and docs/ralph-p2-20261110/HANDOFF.md if present.',
        '',
        'Rules:',
        '- Do ONE to THREE consecutive Ralph iterations from the plan, in plan order, each fully verified.',
        '- For each iteration: implement, run its listed unit tests, then gate G:',
        '  .conda/python.exe -m unittest discover -s tests -v ; .conda/python.exe main.py --no-env doctor ; git diff --check',
        '- Update docs/ralph-p2-20261110/EXECUTION_LOG.md (status + concrete evidence) and HANDOFF.md (next step,',
        '  open risks). Use statuses ready/in_progress/verified/reused_verified/deferred.',
        '- DO NOT run git commit/push/reset/checkout; the autopilot commits after it re-runs gate G itself.',
        '- Never read or print credentials, .env values or tokens. Never change hooks, permissions or .git.',
        '- Do not start Discord listeners, daemons, GPU jobs, Colab/Kaggle sessions or paid API calls.',
        '- Live gates needing the user (rclone/Drive login, Kaggle secrets, live account smoke) -> mark deferred',
        '  with the exact user action needed, then continue with the next independent iteration.',
        '- If the whole plan is verified or only deferred items remain, write the line P2_AUTOPILOT_COMPLETE',
        '  at the end of HANDOFF.md.',
        '- End your answer with a one-paragraph summary: iterations done, tests run, results.',
    ]
    if previous:
        lines += ['', 'Previous autopilot run did NOT pass gate G; its changes are still uncommitted in the working tree.',
                  'Fix them first. Gate failure tail:', previous[-3000:]]
    text = '\n'.join(lines)
    (run_dir / 'prompt.md').write_text(text, encoding='utf-8')
    return text


def command_for(provider_name, provider, model, run_dir):
    kind = provider.get('type')
    prompt_file = run_dir / 'prompt.md'
    short = f'Read {prompt_file} and carry out its instructions exactly. Repository: {ROOT}'
    if kind == 'claude_cli':
        cmd = [provider.get('executable', 'claude'), '-p', '--output-format', 'json',
               '--permission-mode', 'acceptEdits', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash']
        return cmd + (['--model', model] if model else []), prompt_file.read_text(encoding='utf-8')
    if kind == 'codex_cli':
        cmd = [provider.get('executable', 'codex'), 'exec', '--skip-git-repo-check', '-s', 'workspace-write',
               '-C', str(ROOT), '-o', str(run_dir / 'final.txt')]
        return cmd + (['-m', model] if model else []) + ['-'], prompt_file.read_text(encoding='utf-8')
    if kind == 'agy_cli':
        cmd = [provider.get('executable', 'agy'), '--mode', 'accept-edits', '--output-format', 'json',
               '--log-file', str(run_dir / 'agy.log')]
        if provider.get('skip_permissions'):
            cmd.append('--dangerously-skip-permissions')
        return cmd + (['--model', model] if model else []) + ['-p', short], None
    raise ValueError(f'Provider {provider_name} type {kind} is not supported by the autopilot')


def run_gate(settings):
    steps = [[str(PYTHON), '-m', 'unittest', 'discover', '-s', 'tests'],
             [str(PYTHON), 'main.py', '--no-env', 'doctor'], ['git', 'diff', '--check']]
    log = []
    for step in steps:
        try:
            done = subprocess.run(step, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=settings['gate_timeout_seconds'])
            code, out = done.returncode, (done.stdout + done.stderr)
        except subprocess.TimeoutExpired:
            code, out = 124, 'gate step timed out'
        log.append({'cmd': ' '.join(step), 'exit_code': code, 'tail': out[-2000:]})
        if code:
            return False, log
    return True, log


def commit(route, summary):
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout
    paths = [line[3:].strip().strip('"') for line in status.splitlines() if line.strip()]
    paths = [p for p in paths if not p.startswith('.claude/worktrees')]
    if not paths:
        return None
    subprocess.run(['git', 'add', '--', *paths], cwd=ROOT, check=True)
    first = (summary or 'continue approved plan').strip().splitlines()[0][:60]
    message = (f'P2 autopilot: {first}\n\nAutomatic P2 coordinator run via quota router '
               f'({route["provider"]}/{route.get("model") or "default"}); gate G passed before commit.\n\n'
               + summary.strip()[:1500])
    subprocess.run(['git', 'commit', '-q', '-F', '-'], cwd=ROOT, input=message, text=True, check=True)
    return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def tick(settings):
    reason = gate_should_skip(settings)
    if reason:
        append('ticks.jsonl', {'event': 'skip', 'reason': reason})
        return reason
    write_json(STATE / 'lock.json', {'pid': os.getpid(), 'started_unix': now()})
    try:
        from core.quota import LimitErrorDetector, Snapshot
        config, store, router = build_router()
        tried = []
        for _ in range(settings['max_routes_per_tick']):
            # Planner chain (D3, ordered): Codex -> Claude Opus -> AGY Argon high; legacy name as fallback.
            decision = router.select('planner') or router.select('researcher')
            if decision is None or decision.action == 'wait':
                until = getattr(decision, 'until', None) or now() + 1800
                write_json(STATE / 'wait.json', {'until': until, 'reason': getattr(decision, 'reason', 'unrouted')})
                append('ledger.jsonl', {'event': 'wait', 'until': until, 'tried': tried})
                return 'waiting'
            route = {'provider': decision.provider, 'model': decision.model, 'account': decision.account}
            run_dir = STATE / 'runs' / time.strftime('%Y%m%d-%H%M%S')
            run_dir.mkdir(parents=True, exist_ok=True)
            prompt_text(run_dir, read_json(STATE / 'last_gate_failure.json', {}).get('tail', ''))
            provider = config['providers'][decision.provider]
            cmd, stdin = command_for(decision.provider, provider, decision.model, run_dir)
            append('ledger.jsonl', {'event': 'start', 'route': route, 'run_dir': str(run_dir)})
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'WORKFLOW_P2_AUTOPILOT': '1'}
            try:
                done = subprocess.run(cmd, cwd=ROOT, input=stdin, capture_output=True, text=True, encoding='utf-8',
                                      errors='replace', timeout=settings['run_timeout_seconds'], env=env)
                code, out, err = done.returncode, done.stdout, done.stderr
            except subprocess.TimeoutExpired as exc:
                code, out, err = 124, str(exc.stdout or ''), 'autopilot run timed out'
            (run_dir / 'stdout.log').write_text(out or '', encoding='utf-8')
            (run_dir / 'stderr.log').write_text(err or '', encoding='utf-8')
            final = (run_dir / 'final.txt').read_text(encoding='utf-8') if (run_dir / 'final.txt').exists() else ''
            summary = final or out[-3000:]
            limit = LimitErrorDetector.detect('\n'.join([err[-4000:], out[-4000:]])) if code else None
            if limit and limit.get('exhausted', True):
                resets = limit.get('resets_at') or now() + float(config.get('quota_routing', {}).get('default_cooldown_seconds', 900))
                if route['account']:
                    store.put([Snapshot(route['account'], 'limit', 100.0, resets, 'limit_error', now(), 'inferred')])
                set_cooldown('quota:' + route['account'], resets)
                tried.append({**route, 'result': 'quota', 'resets_at': resets})
                append('ledger.jsonl', {'event': 'quota', 'route': route, 'resets_at': resets})
                config, store, router = build_router()
                continue
            if code:
                set_cooldown(route['provider'], now() + settings['failure_cooldown_seconds'])
                tried.append({**route, 'result': f'exit {code}'})
                append('ledger.jsonl', {'event': 'failed', 'route': route, 'exit_code': code, 'stderr_tail': err[-1500:]})
                config, store, router = build_router()
                continue
            passed, gate = run_gate(settings)
            write_json(run_dir / 'gate.json', gate)
            if not passed:
                write_json(STATE / 'last_gate_failure.json', {'tail': json.dumps(gate[-1], ensure_ascii=False), 'run_dir': str(run_dir)})
                append('ledger.jsonl', {'event': 'gate_failed', 'route': route, 'run_dir': str(run_dir)})
                return 'gate_failed'
            (STATE / 'last_gate_failure.json').unlink(missing_ok=True)
            sha = commit(route, summary)
            append('ledger.jsonl', {'event': 'committed' if sha else 'no_changes', 'route': route, 'commit': sha})
            handoff = PLAN_DIR / 'HANDOFF.md'
            if handoff.exists() and 'P2_AUTOPILOT_COMPLETE' in handoff.read_text(encoding='utf-8'):
                (STATE / 'DONE').write_text(time.strftime('%Y-%m-%d %H:%M'), encoding='utf-8')
            return 'committed' if sha else 'no_changes'
        write_json(STATE / 'wait.json', {'until': now() + 1800, 'reason': 'all tried routes failed'})
        return 'all_routes_failed'
    finally:
        (STATE / 'lock.json').unlink(missing_ok=True)


def install():
    exe = ROOT / '.conda' / 'pythonw.exe'
    exe = exe if exe.exists() else PYTHON
    command = f'"{exe}" "{Path(__file__).resolve()}" tick'
    return subprocess.run(['schtasks', '/Create', '/F', '/SC', 'MINUTE', '/MO', '10', '/TN', TASK_NAME, '/TR', command],
                          capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=['tick', 'heartbeat', 'status', 'install', 'uninstall', 'done'])
    parser.add_argument('--note', default='')
    args = parser.parse_args()
    settings = {**DEFAULTS, **read_json(STATE / 'settings.json', {})}
    if args.command == 'heartbeat':
        touch_heartbeat(args.note)
        print('heartbeat updated')
    elif args.command == 'tick':
        print(tick(settings))
    elif args.command == 'status':
        print(json.dumps({'skip_reason': gate_should_skip(settings), 'heartbeat_age_s': heartbeat_age(),
                          'cooldowns': local_cooldowns(), 'wait': read_json(STATE / 'wait.json', {}),
                          'done': (STATE / 'DONE').exists()}, indent=2))
    elif args.command == 'install':
        result = install()
        print(result.stdout or result.stderr)
    elif args.command == 'uninstall':
        result = subprocess.run(['schtasks', '/Delete', '/F', '/TN', TASK_NAME], capture_output=True, text=True)
        print(result.stdout or result.stderr)
    elif args.command == 'done':
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / 'DONE').write_text(time.strftime('%Y-%m-%d %H:%M'), encoding='utf-8')
        print('marked done')


if __name__ == '__main__':
    main()
