"""Provider readiness: pinned CLI version drift (offline) and a throttled daily live smoke.

  python scripts/verify_p2_providers.py --offline          # versions only; no model call
  python scripts/verify_p2_providers.py --live-due         # live smoke for profiles not run today

The live smoke currently covers the API profile (``scripts/verify_p2_api.py --live``). It runs at most
once per profile per day and never catches up missed days. Schedule it yourself (e.g. Windows Task
Scheduler) only if you want it; the controller never schedules it implicitly.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.provider_versions import SmokeSchedule, drift_report  # noqa: E402

STATE = ROOT / 'storage' / 'p2' / 'smoke' / 'schedule.json'
LIVE_PROFILES = {'opencode_api_openrouter': [sys.executable, str(ROOT / 'scripts' / 'verify_p2_api.py'),
                                             '--live', '--profile', 'opencode_api_openrouter', '--attempts', '2']}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--live-due', action='store_true')
    args = parser.parse_args()
    if not (args.offline or args.live_due):
        parser.error('choose --offline and/or --live-due')
    ok = True
    if args.offline:
        report = drift_report()
        print(json.dumps(report, indent=2))
        drift = [name for name, row in report.items() if row['status'] == 'drift']
        if drift:
            print('WARNING: installed CLI differs from the pin for: ' + ', '.join(drift) +
                  '. Verify before relying on it, then update config/provider_versions.json.')
        ok &= not drift
    if args.live_due:
        schedule = SmokeSchedule(STATE)
        for profile, command in LIVE_PROFILES.items():
            if not schedule.due(profile):
                print(f'{profile}: already ran today; skipped')
                continue
            done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            schedule.record(profile, done.returncode == 0, done.stdout.strip().splitlines()[-1:] or '')
            print(f"{profile}: {'PASS' if done.returncode == 0 else 'FAIL'}")
            ok &= done.returncode == 0
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
