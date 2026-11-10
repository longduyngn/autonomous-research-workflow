"""P2.1-07 API acceptance: offline (real OpenCode CLI vs fake server) and a tiny live smoke.

  python scripts/verify_p2_api.py --offline --with-cli
  python scripts/verify_p2_api.py --live --profile opencode_api_openrouter --case tiny-tool-task

The live case uses the configured profile (OpenRouter free models by default, free_only), one
tiny tool task in a throwaway workspace, and writes a receipt with model/account/usage/cost
under storage/p2/acceptance/api/. Credential values are never read beyond presence.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
RECEIPTS = ROOT / 'storage' / 'p2' / 'acceptance' / 'api'
TASK_PROMPT = 'Read numbers.txt (two integers). Write their sum as plain digits to sum.txt. Then reply DONE.'
ROLE_TEXT = ('You are a careful coding agent working only inside the given workspace. Use the file tools '
             'to read and write files. Keep answers short.')


def write_receipt(kind, payload):
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    path = RECEIPTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return path


def tiny_workspace(base):
    from core.integrations import prepare_integrations
    workspace = Path(base) / 'ws'
    workspace.mkdir()
    prepare_integrations(ROOT, workspace, workspace)
    (workspace / 'numbers.txt').write_text('17 25\n', encoding='utf-8')
    prompt = Path(base) / 'tiny_operator.md'
    prompt.write_text(ROLE_TEXT, encoding='utf-8')
    return workspace, prompt


def check(workspace):
    target = workspace / 'sum.txt'
    value = target.read_text(encoding='utf-8').strip() if target.exists() else None
    return {'sum_file': value, 'passed': value == '42'}


def offline(with_cli):
    tests = ['test_p2_opencode_version.py', 'test_p2_api_profiles.py', 'test_p2_opencode_execution.py',
             'test_p2_opencode_session.py', 'test_p2_api_quota.py', 'test_p2_http_errors.py']
    results = []
    for pattern in tests:
        done = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern],
                              cwd=ROOT, capture_output=True, text=True)
        results.append({'pattern': pattern, 'exit_code': done.returncode, 'tail': done.stderr.strip().splitlines()[-1:]})
    cli = None
    if with_cli:
        if not shutil.which('opencode'):
            cli = {'passed': False, 'error': 'opencode not installed'}
        else:
            from core.providers import EphemeralAgentRunner
            from fakes.api_server import ScriptedAPIServer
            with tempfile.TemporaryDirectory() as tmp:
                workspace, prompt = tiny_workspace(tmp)
                script = [{'need_tools': True, 'tool': 'read', 'arguments': {'filePath': 'numbers.txt'}},
                          {'need_tools': True, 'tool': 'write', 'arguments': {'filePath': 'sum.txt', 'content': '42'}},
                          {'need_tools': True, 'text': 'DONE'}]
                with ScriptedAPIServer(script) as server:
                    provider = {'type': 'opencode_cli', 'models': {'worker': 'fake-model'},
                                'api_profile': {'vendor': 'local', 'base_url': server.base_url}}
                    config = {'providers': {'local': provider},
                              'roles': {'operator': {'provider': 'local', 'tier': 'worker', 'timeout_seconds': 300,
                                                     'prompt_file': str(prompt)}}}
                    runner = EphemeralAgentRunner(workspace, config)
                    runner.instructions_root = ROOT
                    first = runner.run_role('operator', TASK_PROMPT)
                    resumed = runner.run_role('operator', 'Confirm sum.txt exists and reply DONE.')
                cli = {**check(workspace), 'success': first.success, 'error': first.error,
                       'tools': first.structured_data.get('native_tools'),
                       'resume_same_session': first.structured_data.get('session_id') ==
                       resumed.structured_data.get('session_id') and resumed.success}
                cli['passed'] = cli['passed'] and first.success and cli['resume_same_session']
    passed = all(r['exit_code'] == 0 for r in results) and (cli is None or cli['passed'])
    receipt = write_receipt('offline', {'mode': 'offline', 'evidence': 'fixture/fake-server only', 'unit': results,
                                        'cli': cli, 'passed': passed})
    return passed, receipt


def live(profile_name, attempts):
    from main import load_config, load_dotenv
    from core.providers import EphemeralAgentRunner
    load_dotenv(ROOT / '.env')  # same loader as main.py; values are only passed to the child CLI
    config = load_config()
    if profile_name not in config.get('providers', {}):
        raise SystemExit(f'Unknown provider profile {profile_name}')
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        workspace, prompt = tiny_workspace(tmp)
        config = {**config, 'roles': {**config['roles'], 'operator': {
            'provider': profile_name, 'tier': 'worker', 'timeout_seconds': 900, 'prompt_file': str(prompt)}}}
        runner = EphemeralAgentRunner(workspace, config)
        runner.instructions_root = ROOT
        for attempt in range(1, attempts + 1):
            started = time.time()
            result = runner.run_role('operator', TASK_PROMPT)
            data = result.structured_data
            row = {'attempt': attempt, 'success': result.success, 'error': (result.error or '')[:300],
                   'model': data.get('model'), 'quota_account': data.get('quota_account'),
                   'usage': data.get('usage'), 'cost_usd': data.get('total_cost_usd'),
                   'tools': data.get('native_tools'), 'error_class': data.get('error_class'),
                   'cooled_down': data.get('free_model_cooled_down'), 'seconds': round(time.time() - started, 1),
                   **check(workspace)}
            rows.append(row)
            if row['passed'] and result.success:
                break
            if not data.get('quota') and not data.get('free_model_cooled_down'):
                break  # not a limit problem: do not burn more calls
    passed = any(r['passed'] and r['success'] for r in rows)
    receipt = write_receipt('live', {'mode': 'live', 'profile': profile_name, 'case': 'tiny-tool-task',
                                     'evidence': 'live account call (one vendor/profile only)', 'attempts': rows,
                                     'passed': passed})
    return passed, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--with-cli', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--profile', default='opencode_api_openrouter')
    parser.add_argument('--case', default='tiny-tool-task', choices=['tiny-tool-task'])
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args()
    if not (args.offline or args.live):
        parser.error('choose --offline and/or --live')
    ok = True
    if args.offline:
        passed, receipt = offline(args.with_cli)
        print(f'offline: {"PASS" if passed else "FAIL"} receipt={receipt}')
        ok &= passed
    if args.live:
        passed, receipt = live(args.profile, max(1, args.attempts))
        print(f'live: {"PASS" if passed else "FAIL"} receipt={receipt}')
        ok &= passed
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
