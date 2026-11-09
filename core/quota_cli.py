"""Quota CLI helpers; status honours probe throttles, refresh may spend one Claude turn."""
import base64
import json
import shlex
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from core.quota import QuotaMonitor, QuotaStore, account_state, resolve

STATUSLINE_MARK = 'claude_statusline_quota.py'


def effective_config(config, root):
    """Config with a quota_routing block; falls back to the shipped example for personal configs without one."""
    if config.get('quota_routing'):
        return config, 'config'
    example = json.loads((Path(root) / 'config/workflow_config.example.json').read_text(encoding='utf-8'))
    return {**config, 'quota_routing': {**example['quota_routing'], 'enabled': False}}, 'example defaults (quota_routing absent from config)'


def _iso(epoch):
    return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S') if epoch else '-'


def status(config, root, db, refresh=True, clock=time.time, force=False):
    config, source = effective_config(config, root)
    store, now = QuotaStore(db), clock()
    block = config['quota_routing']
    monitor = QuotaMonitor(config, store, root, clock)
    refreshed = monitor.refresh(force=force) if refresh else {'errors': {}}
    groups = {}
    for snap in store.snapshots():
        groups.setdefault(snap.account, []).append(snap)
    accounts = {}
    for account in sorted(set(block.get('accounts', {})) | set(groups)):
        spec = block.get('accounts', {}).get(account, {})
        info = account_state(groups.get(account, []), now, block.get('reserve_percent', 10), block.get('stale_seconds', 900), bool(spec.get('unlimited')))
        accounts[account] = {**info, 'unlimited': bool(spec.get('unlimited')), 'until_text': _iso(info['until']),
                             'last_reset_text': _iso(info['last_reset'])}
    from core.control import ControlPlane
    ControlPlane(db)  # ensures the cooldown table exists on a fresh database
    with db._connect() as c:
        cooldowns = [dict(r) for r in c.execute('SELECT account,until_time,reason FROM provider_cooldowns WHERE until_time>?', (now,))]
    return {'routing_enabled': bool(block.get('enabled')), 'config_source': source, 'reserve_percent': block.get('reserve_percent', 10),
            'observed_at': now, 'accounts': accounts, 'cooldowns': cooldowns, 'probe_errors': refreshed['errors'],
            'probes': sorted(monitor.probes)}


def format_status(report):
    lines = [f"quota routing: {'ENABLED' if report['routing_enabled'] else 'disabled (opt-in)'}; reserve last {report['reserve_percent']}%; "
             f"config: {report['config_source']}",
             f"{'account':<24}{'state':<11}{'left%':>7}  {'5h used':>8} {'5h resets':<20}{'week used':>10}  confidence"]
    for account, info in report['accounts'].items():
        five, week = info['windows'].get('five_hour'), info['windows'].get('seven_day')
        left = '-' if info['remaining'] is None else f"{info['remaining']:.0f}"
        mark = ' (uncapped)' if info['unlimited'] else ''
        limit = ' LIMIT-ERROR until ' + _iso(info['windows']['limit']['resets_at']) if 'limit' in info['windows'] and info['windows']['limit']['used_percent'] else ''
        lines.append(f"{account:<24}{info['state']:<11}{left:>7}  "
                     f"{'-' if not five or five['used_percent'] is None else format(five['used_percent'], '.0f'):>8} "
                     f"{_iso(five['resets_at']) if five else '-':<20}"
                     f"{'-' if not week or week['used_percent'] is None else format(week['used_percent'], '.0f'):>10}  {info['confidence']}{mark}{limit}")
    for item in report['cooldowns']:
        lines.append(f"cooldown {item['account']} until {_iso(item['until_time'])}: {item['reason']}")
    for name, error in report['probe_errors'].items():
        lines.append(f'probe error {name}: {error}')
    return '\n'.join(lines)


def route(config, root, db, role, task_id=None, requires=None, clock=time.time):
    config, source = effective_config(config, root)
    from core.quota import build_router
    from core.control import ControlPlane
    router = build_router(config, db, clock, control=ControlPlane(db))
    task = db.get_task(task_id) if task_id else None
    if task_id and task is None:
        raise ValueError('Task not found')
    prefer = router.store.last_route(task_id, role) if task_id else None
    provider, model, decision = resolve(router, role, '', task, requires=requires or None, prefer=prefer)
    return {'role': role, 'task_id': task_id, 'routing_enabled': router.enabled, 'config_source': source,
            'note': None if router.enabled else 'what-if only: quota_routing.enabled is false, runs use the configured provider',
            'decision': decision.to_dict() if decision else None}


# ------------------------------------------------------------------ status-line installer

def statusline_command(root, python=None, previous=''):
    parts = [str(python or sys.executable), str(Path(root) / 'scripts' / STATUSLINE_MARK)]
    if previous:
        parts += ['--chain-base64', base64.b64encode(previous.encode('utf-8')).decode('ascii')]
    return shlex.join(part.replace('\\', '/') for part in parts)  # Git Bash strips backslashes


def _chained_previous(command):
    """Decode the command we chained, if `command` is one of ours."""
    if '--chain-base64' not in command:
        return ''
    try:
        return base64.b64decode(shlex.split(command)[-1]).decode('utf-8')
    except (ValueError, IndexError):
        return ''


def plan_statusline(settings, root, python=None, remove=False):
    """Pure transformation of a settings dict: only `statusLine` can change. Returns (new_settings, info)."""
    current = settings.get('statusLine')
    current = current if isinstance(current, dict) else None
    command = (current or {}).get('command', '') if current else ''
    ours = STATUSLINE_MARK in command.replace('\\', '/')
    new = dict(settings)
    if remove:
        if not ours:
            return settings, {'action': 'none', 'reason': 'statusLine is not ours'}
        previous = _chained_previous(command)
        if previous:
            new['statusLine'] = {**{k: v for k, v in current.items() if k != 'command'}, 'command': previous}
        else:
            new.pop('statusLine')
        return new, {'action': 'remove', 'restores_previous': bool(previous)}
    if ours:
        wanted = statusline_command(root, python, _chained_previous(command))
        if wanted == command:
            return settings, {'action': 'none', 'reason': 'already installed'}
        new['statusLine'] = {**current, 'command': wanted}
        return new, {'action': 'update', 'chains_existing': bool(_chained_previous(command))}
    if current and current.get('type', 'command') != 'command':
        return settings, {'action': 'none', 'reason': 'existing statusLine is not a command; refusing to replace it'}
    new['statusLine'] = {**(current or {}), 'type': 'command', 'command': statusline_command(root, python, command)}
    return new, {'action': 'install', 'chains_existing': bool(command)}


def install_statusline(settings_path, root, apply=False, remove=False, python=None, clock=time.time):
    """--check (default) reports the change; --apply backs the file up first and writes only statusLine."""
    path = Path(settings_path).expanduser()
    settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('settings file is not a JSON object')
    new, info = plan_statusline(settings, root, python, remove)
    info = {**info, 'settings_file': str(path), 'applied': False}
    if apply and info['action'] != 'none':
        backup = None
        if path.exists():
            backup = path.with_name(path.name + f'.quota-backup-{int(clock())}')
            shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.quota-tmp')
        temporary.write_text(json.dumps(new, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.replace(path)
        info.update(applied=True, backup=str(backup) if backup else None)
    return info


def run(args, config, root, db):
    """Dispatch for argparse namespaces carrying action/role/task/requires/json."""
    if args.action in {'status', 'refresh'}:
        # `refresh` forces active probes (one small Claude turn); `status` honours their throttle.
        report = status(config, root, db, refresh=True, force=args.action == 'refresh')
        print(json.dumps(report, indent=2, default=str) if args.json else format_status(report))
    elif args.action == 'route':
        if not args.role:
            raise SystemExit('quota route needs a ROLE')
        print(json.dumps(route(config, root, db, args.role, args.task, args.requires), indent=2, default=str))
    return 0
