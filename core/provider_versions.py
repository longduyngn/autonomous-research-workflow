"""Pinned CLI versions and drift detection (P2.4-04). Reads ``--version`` only; never calls a model."""
import json
import re
import subprocess
import time
from pathlib import Path

from core.executables import find_executable

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / 'config' / 'provider_versions.json'


def load_pins(path=PINS):
    return json.loads(Path(path).read_text(encoding='utf-8')).get('providers', {})


def installed_version(executable, pattern, run=None, timeout=30):
    """Parsed ``<executable> --version`` or None when missing/unparseable."""
    resolved = find_executable(executable)
    if not resolved:
        return None
    run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                              stdin=subprocess.DEVNULL).stdout)
    try:
        text = run([resolved, '--version']) or ''
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(pattern or r'(\S+)', text.strip(), re.M)
    return match.group(1) if match else None


def drift_report(pins=None, run=None):
    """{provider: {pinned, installed, status}} with status ok | drift | missing."""
    report = {}
    for name, spec in (pins or load_pins()).items():
        installed = installed_version(spec.get('executable', ''), spec.get('version_pattern'), run=run)
        status = 'missing' if installed is None else ('ok' if installed == spec.get('version') else 'drift')
        report[name] = {'pinned': spec.get('version'), 'installed': installed, 'status': status}
    return report


class SmokeSchedule:
    """At most one live smoke per profile per calendar day; missed days are never caught up."""

    def __init__(self, state_path, clock=time.time):
        self.path, self.clock = Path(state_path), clock

    def _state(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def due(self, profile):
        last = self._state().get(profile, {}).get('day')
        return last != time.strftime('%Y-%m-%d', time.localtime(self.clock()))

    def record(self, profile, passed, receipt=''):
        state = self._state()
        state[profile] = {'day': time.strftime('%Y-%m-%d', time.localtime(self.clock())), 'passed': bool(passed),
                          'receipt': str(receipt), 'at': self.clock()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2), encoding='utf-8')
