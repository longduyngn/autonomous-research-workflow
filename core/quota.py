"""Quota snapshots, probes, run ledger and the opt-in provider router.

Policy and quota probes without credential-file access. Codex supports read-only
app-server RPC with rollout fallback. Claude uses passive stream/status-line data
and an optional throttled one-turn active probe. AGY uses its configured probe and ledger.
"""
import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

WINDOWS = {'five_hour': 18000, 'seven_day': 604800}
DEFAULTS = {'enabled': False, 'poll_seconds': 120, 'reserve_percent': 10, 'stale_seconds': 900,
            'default_cooldown_seconds': 900, 'sticky_routes': True, 'light_validator': False}
CONFIDENCE = {'measured': 0, 'inferred': 1, 'estimated': 2}


@dataclass
class Snapshot:
    account: str
    window: str
    used_percent: float = None
    resets_at: float = None
    source: str = ''
    observed_at: float = 0.0
    confidence: str = 'measured'
    last_reset: float = None

    def effective(self, now, stale_seconds):
        """View at `now`: an elapsed window has reset; old measurements become estimates."""
        if self.resets_at is not None and self.resets_at <= now:
            return replace(self, used_percent=0.0 if self.used_percent is not None else None, resets_at=None,
                           confidence='inferred', last_reset=self.resets_at)
        last = self.resets_at - WINDOWS[self.window] if self.resets_at is not None and self.window in WINDOWS else None
        stale = self.confidence == 'measured' and now - self.observed_at > stale_seconds
        return replace(self, last_reset=last, confidence='estimated' if stale else self.confidence)

    def to_dict(self):
        return {k: getattr(self, k) for k in ('account', 'window', 'used_percent', 'resets_at', 'source', 'observed_at', 'confidence')}


def account_for(provider, model=''):
    """Which quota pool meters this provider/model; None when the router does not track it."""
    if provider.get('quota_account'):
        return provider['quota_account']
    kind, model = provider.get('type'), (model or '').lower()
    if kind == 'codex_cli':
        return 'codex'
    if kind == 'claude_cli':
        return 'claude'
    if kind == 'remote_claude_operator':
        return 'remote_claude'
    if kind == 'opencode_cli' and provider.get('api_profile'):
        # Metered where billing/quota happens: a model via OpenRouter is api:openrouter.
        profile = provider['api_profile']
        return profile.get('account') or f"api:{profile.get('vendor', 'unknown')}"
    if kind == 'http_llm' and provider.get('base_url'):
        from urllib.parse import urlparse
        host = urlparse(provider['base_url']).hostname or ''
        for marker, account in (('openrouter.ai', 'api:openrouter'), ('api.openai.com', 'api:openai'),
                                ('api.deepseek.com', 'api:deepseek'), ('generativelanguage.googleapis.com', 'api:gemini'),
                                ('api.anthropic.com', 'api:anthropic')):
            if host.endswith(marker):
                return account
    if kind == 'agy_cli':
        for prefix, account in (('gemini-4-argon', 'agy:gemini-4-argon'), ('gemini-3.8-flash', 'agy:gemini-3.8-flash'),
                                ('claude', 'agy:claude'), ('gpt-oss', 'agy:gpt-oss')):
            if model.startswith(prefix):
                return account
        return 'agy:default'
    return None


def usage_tokens(usage):
    """Billable-ish token count from a CLI `usage` object (cache reads excluded)."""
    if not isinstance(usage, dict):
        return 0
    return sum(int(usage.get(k) or 0) for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens')
               if isinstance(usage.get(k), (int, float)))


def _epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- parsers/probes

def parse_codex_rate_limits(limits, observed_at, source='codex_rollout'):
    """Codex `rate_limits` object (primary 5h / secondary weekly) -> snapshots."""
    if not isinstance(limits, dict) or limits.get('limit_id', limits.get('limitId')) not in (None, 'codex'):
        return []
    reached = str(limits.get('rate_limit_reached_type', limits.get('rateLimitReachedType')) or '').lower()
    out = []
    for key in ('primary', 'secondary'):
        item = limits.get(key)
        if not isinstance(item, dict) or not isinstance(item.get('used_percent', item.get('usedPercent')), (int, float)):
            continue
        minutes = item.get('window_minutes', item.get('windowDurationMins')) or (300 if key == 'primary' else 10080)
        used = float(item.get('used_percent', item.get('usedPercent')))
        # Real schema values do not identify which window was exhausted.
        if reached in {'primary', 'secondary'} and key == reached:
            used = 100.0
        out.append(Snapshot('codex', 'five_hour' if minutes <= 600 else 'seven_day', used,
                            _epoch(item.get('resets_at', item.get('resetsAt'))), source, observed_at))
    if reached and reached not in {'primary', 'secondary'} and not any(s.used_percent >= 100 for s in out):
        out.append(Snapshot('codex', 'limit', 100.0, None, source, observed_at, 'inferred'))
    return out


def parse_claude_limits(limits, observed_at, source='claude_statusline'):
    """Status-line `rate_limits` ({five_hour|seven_day: {used_percentage, resets_at}}) -> snapshots."""
    out = []
    for name in WINDOWS:
        item = (limits or {}).get(name) if isinstance(limits, dict) else None
        if isinstance(item, dict) and isinstance(item.get('used_percentage'), (int, float)):
            out.append(Snapshot('claude', name, float(item['used_percentage']), _epoch(item.get('resets_at')), source, observed_at))
    return out


def parse_claude_event(event, observed_at, source='claude_stream'):
    """stream-json `rate_limit_event` (unifiedWindows.*.utilization is a 0..1 fraction) -> snapshots."""
    info = event.get('rate_limit_info') if isinstance(event, dict) and event.get('type') == 'rate_limit_event' else None
    windows = info.get('unifiedWindows') if isinstance(info, dict) else None
    out = []
    for name in WINDOWS:
        item = windows.get(name) if isinstance(windows, dict) else None
        if isinstance(item, dict) and isinstance(item.get('utilization'), (int, float)):
            used = item['utilization'] * 100 if item['utilization'] <= 1 else item['utilization']
            if item.get('status') == 'rejected':
                used = 100.0
            out.append(Snapshot('claude', name, float(used), _epoch(item.get('resetsAt')), source, observed_at))
    return out


def snapshots_from_stream(text, kind, observed_at=None):
    """Usage carried by a CLI's own JSONL stdout (codex rate_limits / claude rate_limit_event)."""
    marker = {'codex_cli': '"rate_limits"', 'claude_cli': 'rate_limit_event'}.get(kind)
    if not marker or marker not in (text or ''):
        return []
    observed_at, found = observed_at or time.time(), []
    for line in text.splitlines():
        if marker not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if kind == 'claude_cli':
            found = parse_claude_event(event, observed_at, 'claude_stream') or found
            continue
        for holder in (event, event.get('payload'), event.get('msg')):
            if isinstance(holder, dict) and isinstance(holder.get('rate_limits'), dict):
                found = parse_codex_rate_limits(holder['rate_limits'], observed_at, 'codex_exec') or found
    return found


class CodexRolloutProbe:
    """Newest rollout (by mtime, across all date directories) -> its last token_count rate_limits."""

    def __init__(self, root=None, tail_bytes=4 << 20):
        import os
        self.root = Path(root) if root else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser() / 'sessions'
        self.tail_bytes = tail_bytes

    def newest(self, count=3):
        found = []
        for path in self.root.glob('*/*/*/rollout-*.jsonl'):
            try:
                found.append((path.stat().st_mtime, path))
            except OSError:
                continue
        return [path for _, path in sorted(found, reverse=True)[:count]]

    def probe(self):
        for path in self.newest():
            snapshots = self.scan(path)
            if snapshots:
                return snapshots
        return []

    def scan(self, path):
        try:
            with open(path, 'rb') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - self.tail_bytes))
                lines = stream.read().decode('utf-8', 'replace').splitlines()
            mtime = Path(path).stat().st_mtime
        except OSError:
            return []
        for line in reversed(lines):
            if '"rate_limits"' not in line or 'token_count' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get('payload') if isinstance(event, dict) else None
            if isinstance(payload, dict) and payload.get('type') == 'token_count':
                snapshots = parse_codex_rate_limits(payload.get('rate_limits'), _epoch(event.get('timestamp')) or mtime)
                if snapshots:
                    return snapshots
        return []


class CodexAppServerProbe:
    """No inference: read account/rateLimits/read, throttle attempts, fall back to rollouts."""

    def __init__(self, cache_path, executable='codex', min_interval=120, timeout=25,
                 clock=time.time, fallback=None):
        self.cache = Path(cache_path)
        self.executable, self.min_interval, self.timeout = executable, min_interval, timeout
        self.clock, self.fallback = clock, fallback or CodexRolloutProbe()

    def probe(self, force=False):
        from core.codex_rpc import read_app_server
        from core.role_memory import atomic_json
        try:
            data = json.loads(self.cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        now = self.clock()
        if force or now - data.get('attempted_at', 0) >= self.min_interval:
            data['attempted_at'] = now
            # Persist the attempt before starting a child, including on failure.
            atomic_json(self.cache, data)
            try:
                result = read_app_server('account/rateLimits/read', executable=self.executable,
                                         cwd=self.cache.parent, timeout=self.timeout)
                limits = (result.get('rateLimitsByLimitId') or {}).get('codex') or result.get('rateLimits')
                snaps = parse_codex_rate_limits(limits, now, 'codex_app_server')
                if snaps:
                    # Persist only normalized usage, never auth, credits or server banners.
                    data.update(captured_at=now, snapshots=[s.to_dict() for s in snaps])
                    atomic_json(self.cache, data)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        cached = [Snapshot(**s) for s in data.get('snapshots', [])]
        fallback = self.fallback.probe()
        # Retain original timestamps: a failed refresh must not make stale data fresh.
        latest = {}
        for snap in cached + fallback:
            if snap.window not in latest or snap.observed_at > latest[snap.window].observed_at:
                latest[snap.window] = snap
        return list(latest.values())


class ClaudeStatusProbe:
    """Reads storage/quota/claude_statusline.json written by scripts/claude_statusline_quota.py."""

    def __init__(self, path):
        self.path = Path(path)

    def probe(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            observed = _epoch(data.get('captured_at')) or self.path.stat().st_mtime
        except (OSError, ValueError, AttributeError):
            return []
        return parse_claude_limits(data.get('rate_limits'), observed)


class ClaudeCliProbe:
    """Active probe: a tiny `claude -p` call whose stream-json carries `rate_limit_event`
    (the same 5h/7d utilization the API's anthropic-ratelimit-unified-* headers report).
    The CLI authenticates itself; no token is read here. Throttled by `min_interval`
    because each probe spends a small Haiku turn; an exhausted account answers with the
    rejected event at no cost."""

    def __init__(self, cache_path, executable='claude', model='haiku', min_interval=1800, timeout=120, clock=time.time, store=None):
        self.cache = Path(cache_path)
        self.executable, self.model = executable, model
        self.min_interval, self.timeout, self.clock = min_interval, timeout, clock
        self.store = store

    def cached(self):
        try:
            data = json.loads(self.cache.read_text(encoding='utf-8'))
            return data, float(data.get('captured_at', 0))
        except (OSError, ValueError, AttributeError, TypeError):
            return None, 0.0

    def probe(self, force=False):
        if self.store is not None and not force:
            passive = [s for s in self.store.snapshots('claude') if s.source == 'claude_stream'
                       and self.clock() - s.observed_at < self.min_interval]
            if {s.window for s in passive} >= set(WINDOWS):
                return passive
        data, captured = self.cached()
        if data and not force and self.clock() - data.get('attempted_at', captured) < self.min_interval:
            return parse_claude_event(data.get('event', {}), captured, 'claude_cli_probe')
        from core.executables import find_executable
        from core.processes import run_process
        resolved = find_executable(self.executable)
        if not resolved:
            return parse_claude_event(data.get('event', {}), captured, 'claude_cli_probe') if data else []
        workdir = self.cache.parent / 'claude-probe'
        workdir.mkdir(parents=True, exist_ok=True)
        from core.role_memory import atomic_json
        data = {**(data or {}), 'attempted_at': self.clock()}
        atomic_json(self.cache, data)
        result = run_process([resolved, '-p', 'Reply with the single word ok.', '--model', self.model,
                              '--output-format', 'stream-json', '--verbose', '--max-turns', '1', '--tools', ''], workdir, timeout=self.timeout,
                             input_text='')
        now = self.clock()
        for line in (result.get('stdout') or '').splitlines():
            if 'rate_limit_event' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            snapshots = parse_claude_event(event, now, 'claude_cli_probe')
            if snapshots:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(self.cache, {'captured_at': now, 'attempted_at': now, 'event': event})
                return snapshots
        return parse_claude_event(data.get('event', {}), captured, 'claude_cli_probe') if data else []


class AgyProbe:
    """Credential-free AGY readiness probe and quota account seeding.

    AGY 1.5.3 prints no numeric quota API over CLI stdout, logs (doRefreshQuota only),
    or ACP stdio server. IDE language-server GetUserStatus was investigated and rejected
    because reading it requires scraping ephemeral CSRF tokens and port numbers from process
    arguments or private files, violating credential/sandbox security boundaries.

    AgyProbe checks AGY CLI / ACP readiness (without spending any model tokens)
    and seeds/refreshes the three distinct AGY quota pools:
    - agy:gemini-4-argon (unlimited hourly tier, reserve)
    - agy:gemini-3.8-flash (5h window, light work pool)
    - agy:claude (AGY-hosted Claude pool, separate from Gemini)
    Throttled by min_interval (default 1800s) and cached in storage/quota/agy_probe.json.
    """

    def __init__(self, cache_path=None, executable='agy', min_interval=1800, timeout=15, clock=time.time):
        self.cache = Path(cache_path) if cache_path else None
        self.executable, self.min_interval = executable, min_interval
        self.timeout, self.clock = timeout, clock

    def cached(self):
        if not self.cache:
            return None, 0.0
        try:
            data = json.loads(self.cache.read_text(encoding='utf-8'))
            return data, float(data.get('captured_at', 0))
        except (OSError, ValueError, AttributeError, TypeError):
            return None, 0.0

    def probe(self, force=False):
        now = self.clock()
        data, captured = self.cached()
        if data and not force and (now - captured) < self.min_interval:
            return [Snapshot(**s) for s in data.get('snapshots', [])]
        from core.executables import find_executable
        resolved = find_executable(self.executable)
        if not resolved:
            return [Snapshot(**s) for s in data.get('snapshots', [])] if data else []
        tier_id = 'unknown'
        available_models = []
        try:
            import subprocess
            workdir = (self.cache.parent if self.cache else Path.cwd()) / 'agy-probe'
            workdir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([resolved, 'acp'], cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace')
            init_msg = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                   'params': {'clientInfo': {'name': 'agy-quota-probe', 'version': '1.0'}}}) + '\n'
            # Tier and model list arrive only with session/new; it starts no turn and spends no tokens.
            session_msg = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'session/new', 'params': {}}) + '\n'
            try:
                out, _ = proc.communicate(init_msg + session_msg, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                # Never leave an ACP server behind on every poll.
                from core.processes import stop_process_tree
                stop_process_tree(proc)
                out, _ = proc.communicate()
            for line in out.splitlines():
                try:
                    resp = json.loads(line)
                    res = resp.get('result', {})
                    tier = res.get('_meta', {}).get('antigravity', {}).get('tier', {})
                    if tier.get('id'):
                        tier_id = tier['id']
                    models = res.get('models', {}).get('availableModels', [])
                    if models:
                        available_models = [m.get('modelId') for m in models if isinstance(m, dict)]
                except ValueError:
                    continue
        except Exception:
            pass

        snaps = [
            Snapshot('agy:gemini-4-argon', 'five_hour', used_percent=None, resets_at=None,
                     source='agy_probe', observed_at=now, confidence='inferred'),
            Snapshot('agy:gemini-3.8-flash', 'five_hour', used_percent=None, resets_at=None,
                     source='agy_probe', observed_at=now, confidence='inferred'),
            Snapshot('agy:claude', 'five_hour', used_percent=None, resets_at=None,
                     source='agy_probe', observed_at=now, confidence='inferred'),
        ]
        if self.cache:
            try:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                temp = self.cache.with_suffix('.tmp')
                temp.write_text(json.dumps({
                    'captured_at': now,
                    'tier_id': tier_id,
                    'available_models': available_models,
                    'snapshots': [s.to_dict() for s in snaps]
                }), encoding='utf-8')
                temp.replace(self.cache)
            except OSError:
                pass
        return snaps


_LIMIT_PATTERNS = [re.compile(p, re.I) for p in (
    r'usage_limit_reached', r'workspace_(?:owner|member)_credits_depleted',
    r'hit your (?:\w+ )?limit', r'usage limit', r'(?:5[- ]hour|five[- ]hour|weekly|session) limit',
    r'limit (?:reached|exceeded)', r'resource_exhausted', r'quota (?:exceeded|exhausted)',
    r'(?:exhausted|exceeded) your (?:\w+ )?quota', r'out of quota', r'rate[ _-]?limit(?:ed|s)?\b',
    r'too many requests', r'insufficient[_ ]quota', r'(?:http|status|error|code)\D{0,12}429\b',
    r'resourcehasbeenexhausted', r'user_?rate_?limit_?exceeded', r'rate_?limit_?exceeded',
    r'exhausted your (?:model )?capacity', r'request limit for', r'quota will replenish',
    r'quota exceeded for quota metric')]


class LimitErrorDetector:
    """Recognises exhausted-usage failures in CLI text and, when present, when they reset."""

    @staticmethod
    def detect(text, now=None):
        text = text or ''
        match = next((m for m in (p.search(text) for p in _LIMIT_PATTERNS) if m), None)
        if not match:
            return None
        return {'exhausted': True, 'matched': match.group(0)[:80], 'resets_at': LimitErrorDetector.resets_at(text, now or time.time())}

    @staticmethod
    def resets_at(text, now):
        try:
            item = json.loads(text)
        except (ValueError, TypeError):
            item = None

        def structured(value):
            if not isinstance(value, dict):
                return None
            for key in ('resets_at', 'resetsAt', 'reset_at'):
                if _epoch(value.get(key)) is not None:
                    return _epoch(value[key])
            for key in ('error', 'payload', 'error_data', 'data'):
                reset = structured(value.get(key))
                if reset is not None:
                    return reset
            return None

        reset = structured(item)
        if reset is not None:
            return reset
        found = re.search(r'\|\s*(\d{10})\b', text)
        if found:
            return float(found.group(1))
        found = re.search(r'(?:resets?|try again)\s*(?:at|on)?\s*(\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)?)', text, re.I)
        if found and _epoch(found.group(1)):
            return _epoch(found.group(1))
        # Codex CLI: "try again at Nov 10th, 2026 3:24 AM" (local time; may wrap lines).
        found = re.search(r'(?:resets?|try again|retry)\s+(?:at|on)?\s*([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?'
                          r'\s+(?:(\d{4}),?\s+)?(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text, re.I)
        if found:
            months = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
            mon = found.group(1).lower()
            if mon in months:
                hour = int(found.group(4)) % 12 + (12 if found.group(6).lower() == 'pm' else 0)
                year = int(found.group(3) or datetime.fromtimestamp(now).year)
                try:
                    stamp = datetime(year, months.index(mon) + 1, int(found.group(2)), hour, int(found.group(5) or 0)).timestamp()
                except ValueError:
                    stamp = None
                if stamp is not None:
                    return stamp if found.group(3) or stamp > now else datetime(year + 1, months.index(mon) + 1,
                        int(found.group(2)), hour, int(found.group(5) or 0)).timestamp()
        found = re.search(r'(?:resets?|try again|retry)\s*(?:at|after)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text, re.I)
        if found:
            hour = int(found.group(1)) % 12 + (12 if found.group(3).lower() == 'pm' else 0)
            local = datetime.fromtimestamp(now).replace(hour=hour, minute=int(found.group(2) or 0), second=0, microsecond=0)
            return (local + timedelta(days=1) if local.timestamp() <= now else local).timestamp()
        found = re.search(r'(?:try again|retry|resets?|replenish(?:es)?)\s*(?:in|after)?\s*(?:(\d+)\s*h\w*)?[\s,]*(?:(\d+)\s*m\w*)?[\s,]*(?:(\d+)\s*s\w*)?', text, re.I)
        if found and any(found.groups()):
            h, m, s = (int(g or 0) for g in found.groups())
            return now + h * 3600 + m * 60 + s
        found = re.search(r'retry[- ]after\D{0,3}(\d+)', text, re.I)
        return now + int(found.group(1)) if found else None


# ---------------------------------------------------------------- store and state

class QuotaStore:
    """SQLite tables `quota_snapshots` (latest per account/window) and `quota_ledger`."""

    def __init__(self, db):
        self.db = db
        with db._connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS quota_snapshots(account TEXT NOT NULL,window TEXT NOT NULL,used_percent REAL,
              resets_at REAL,source TEXT NOT NULL,observed_at REAL NOT NULL,confidence TEXT NOT NULL,PRIMARY KEY(account,window));
            CREATE TABLE IF NOT EXISTS quota_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,task_id INTEGER,
              role TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',account TEXT NOT NULL DEFAULT '',
              tokens INTEGER NOT NULL DEFAULT 0,duration REAL NOT NULL DEFAULT 0,success INTEGER NOT NULL DEFAULT 0,
              limit_error INTEGER NOT NULL DEFAULT 0,resets_at REAL);
            ''')
            columns = {r[1] for r in c.execute('PRAGMA table_info(quota_ledger)')}
            for name, definition in {'run_key': "TEXT NOT NULL DEFAULT ''",
                                     'usage_json': "TEXT NOT NULL DEFAULT '{}'",
                                     'num_turns': 'INTEGER NOT NULL DEFAULT 0',
                                     'total_cost_usd': 'REAL NOT NULL DEFAULT 0'}.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE quota_ledger ADD COLUMN {name} {definition}')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quota_run_key ON quota_ledger(run_key) WHERE run_key!=''")

    def put(self, snapshots):
        with self.db._connect() as c:
            for s in snapshots:
                c.execute('''INSERT INTO quota_snapshots VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,window) DO UPDATE SET
                  used_percent=excluded.used_percent,resets_at=excluded.resets_at,source=excluded.source,
                  observed_at=excluded.observed_at,confidence=excluded.confidence WHERE excluded.observed_at>=quota_snapshots.observed_at''',
                          (s.account, s.window, s.used_percent, s.resets_at, s.source, s.observed_at, s.confidence))
                if s.window in WINDOWS and s.confidence == 'measured' and (s.used_percent or 0) < 100:
                    # Fresh measured headroom supersedes an older failure-derived exhaustion.
                    c.execute("DELETE FROM quota_snapshots WHERE account=? AND window='limit' AND observed_at<?", (s.account, s.observed_at))

    def snapshots(self, account=None):
        with self.db._connect() as c:
            rows = c.execute('SELECT * FROM quota_snapshots' + (' WHERE account=?' if account else '') + ' ORDER BY account,window',
                             (account,) if account else ()).fetchall()
        return [Snapshot(*[r[k] for k in ('account', 'window', 'used_percent', 'resets_at', 'source', 'observed_at', 'confidence')]) for r in rows]

    def record_run(self, *, role, provider, model='', account='', task_id=None, tokens=0, duration=0.0, success=False,
                   limit_error=False, resets_at=None, ts=None, run_key='', usage=None, num_turns=0, total_cost_usd=0):
        with self.db._connect() as c:
            c.execute('INSERT OR IGNORE INTO quota_ledger(ts,task_id,role,provider,model,account,tokens,duration,success,limit_error,resets_at,run_key,usage_json,num_turns,total_cost_usd)'
                      ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ts or time.time(), task_id, role, provider, model or '', account or '',
                      int(tokens), float(duration), int(bool(success)), int(bool(limit_error)), resets_at,
                      run_key, json.dumps(usage or {}), int(num_turns or 0), float(total_cost_usd or 0)))

    def ledger(self, account=None, since=0.0, limit=50):
        with self.db._connect() as c:
            rows = c.execute('SELECT * FROM quota_ledger WHERE ts>=?' + (' AND account=?' if account else '') + ' ORDER BY id DESC LIMIT ?',
                             (since, account, limit) if account else (since, limit)).fetchall()
        return [dict(r) for r in rows]

    def last_route(self, task_id, role):
        with self.db._connect() as c:
            row = c.execute('SELECT provider,model FROM quota_ledger WHERE task_id=? AND role=? AND success=1 ORDER BY id DESC LIMIT 1',
                            (task_id, role)).fetchone()
        return (row['provider'], row['model']) if row else None

    def ledger_estimate(self, account, budget_tokens, now):
        """Estimated 5h usage from our own runs; only when the user configured a budget."""
        rows = [r for r in self.ledger(account, now - WINDOWS['five_hour'], 10000) if r['tokens']]
        if not budget_tokens or not rows:
            return None
        used = sum(r['tokens'] for r in rows)
        oldest = min(r['ts'] for r in rows)
        return Snapshot(account, 'ledger', min(100.0, used * 100.0 / budget_tokens), oldest + WINDOWS['five_hour'], 'ledger', now, 'estimated')


def account_state(snapshots, now, reserve=10.0, stale=900, unlimited=False):
    """Collapse an account's windows into ok | reserved | exhausted with reset hints."""
    views = [s.effective(now, stale) for s in snapshots]
    remaining, blocking, state = [], [], 'ok'
    for s in views:
        if s.used_percent is None or (unlimited and s.window in WINDOWS):
            continue
        left = 100.0 - s.used_percent
        remaining.append(left)
        if left <= 0:
            state = 'exhausted'
            blocking.append(s)
        elif left <= reserve:
            state = 'reserved' if state == 'ok' else state
            blocking.append(s)
    until = None
    if blocking and all(s.resets_at is not None for s in blocking):
        until = max(s.resets_at for s in blocking)
    five = next((s for s in views if s.window == 'five_hour'), None)
    used = [s for s in views if s.used_percent is not None]
    conf = max((s.confidence for s in used), key=CONFIDENCE.get, default=None)
    if conf is None and views:
        conf = max((s.confidence for s in views), key=CONFIDENCE.get, default='unknown')
    return {'state': state, 'remaining': min(remaining) if remaining else (100.0 if unlimited else None), 'until': until,
            'last_reset': five.last_reset if five else None, 'confidence': conf or 'unknown',
            'windows': {s.window: s.to_dict() for s in views}}


class QuotaMonitor:
    """Refreshes snapshots with throttled probes; safe to call from a poller thread."""

    def __init__(self, config, store, root, clock=time.time, probes=None):
        self.store, self.clock = store, clock
        self.cfg = {**DEFAULTS, **config.get('quota_routing', {})}
        accounts = self.cfg.get('accounts', {})
        root = Path(root)
        if probes is None:
            probes = {}
            if 'codex' in accounts:
                spec = accounts['codex']
                kind = spec.get('probe', 'codex_app_server')
                if kind == 'codex_rollout':
                    probes['codex'] = CodexRolloutProbe(spec.get('sessions_dir'))
                elif kind == 'codex_app_server':
                    probes['codex'] = CodexAppServerProbe(root / spec.get('probe_cache', 'storage/quota/codex_probe.json'),
                        spec.get('executable', 'codex'), int(spec.get('probe_interval_seconds', 120)),
                        clock=clock, fallback=CodexRolloutProbe(spec.get('sessions_dir')))
            if 'claude' in accounts:
                spec = accounts['claude']
                kinds = spec.get('probe', 'claude_statusline')
                kinds = [kinds] if isinstance(kinds, str) else list(kinds)
                if 'claude_statusline' in kinds:
                    probes['claude'] = ClaudeStatusProbe(root / spec.get('statusline_file', 'storage/quota/claude_statusline.json'))
                if 'claude_cli' in kinds:
                    probes['claude_cli'] = ClaudeCliProbe(root / spec.get('probe_cache', 'storage/quota/claude_probe.json'),
                        spec.get('executable', 'claude'), spec.get('probe_model', 'haiku'),
                        int(spec.get('probe_interval_seconds', 1800)), clock=clock, store=store)
            if any(acc.startswith('agy:') for acc in accounts) or 'agy' in accounts:
                spec = accounts.get('agy', {})
                probes['agy'] = AgyProbe(root / spec.get('probe_cache', 'storage/quota/agy_probe.json'),
                                         executable=spec.get('executable', 'agy'),
                                         min_interval=int(spec.get('probe_interval_seconds', 1800)),
                                         clock=clock)
        self.probes = probes

    def refresh(self, force=False):
        now, puts, errors = self.clock(), [], {}
        for name, probe in self.probes.items():
            try:
                puts += probe.probe(force=True) if force and isinstance(probe, (ClaudeCliProbe, CodexAppServerProbe, AgyProbe)) else probe.probe()
            except Exception as exc:  # A broken probe must never stall scheduling.
                errors[name] = type(exc).__name__
        for account, spec in self.cfg.get('accounts', {}).items():
            estimate = self.store.ledger_estimate(account, spec.get('budget_tokens_5h'), now)
            if estimate:
                puts.append(estimate)
        self.store.put(puts)
        return {'snapshots': [s.to_dict() for s in puts], 'errors': errors}


# ---------------------------------------------------------------- router

@dataclass
class RouteDecision:
    action: str = 'run'          # run | wait
    provider: str = ''
    model: str = ''
    account: str = ''
    source: str = ''             # class | requires | workaround | reserve | pinned | wait
    reason: str = ''
    reserved_use: bool = False
    quota_class: str = ''
    until: float = None
    considered: list = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ('action', 'provider', 'model', 'account', 'source', 'reason', 'reserved_use', 'quota_class', 'until', 'considered')}


class QuotaRouter:
    """Chooses provider/model per role from equivalence classes; see docs/QUOTA_ROUTING.md."""

    def __init__(self, config, store, clock=time.time, cooldown_until=None):
        self.cfg = {**DEFAULTS, **config.get('quota_routing', {})}
        self.providers, self.store, self.clock = config.get('providers', {}), store, clock
        self.roles = config.get('roles', {})
        self.cooldown_until = cooldown_until or (lambda key: 0)

    @property
    def enabled(self):
        return bool(self.cfg['enabled'])

    def _entry(self, raw):
        provider = self.providers.get(raw.get('provider'), {})
        account = raw.get('account') or account_for(provider, raw.get('model', ''))
        return {'provider': raw.get('provider', ''), 'model': raw.get('model', ''), 'account': account or ''}

    def _state(self, entry, now, groups):
        account = entry['account']
        spec = self.cfg['accounts'].get(account, {})
        info = account_state(groups.get(account, []), now, self.cfg['reserve_percent'], self.cfg['stale_seconds'], bool(spec.get('unlimited')))
        for key in (entry['provider'], 'quota:' + account):
            cooling = self.cooldown_until(key)
            if cooling and cooling > now:
                info = {**info, 'state': 'exhausted', 'until': max(info['until'] or 0, cooling)}
        return info

    def _pinned(self, role, now, groups):
        cfg = self.roles.get(role, {})
        if not cfg.get('pinned'):
            return None
        chain = [{'provider': cfg.get('provider', ''), 'model': cfg.get('model', '')}] + list(cfg.get('fallbacks', []))
        considered = []
        for raw in chain:
            entry = self._entry(raw)
            info = self._state(entry, now, groups)
            considered.append({**entry, 'state': info['state'], 'remaining': info['remaining'], 'until': info['until']})
            if info['state'] in ('ok', 'reserved'):
                return RouteDecision('run', entry['provider'], entry['model'], entry['account'], 'role_pinned',
                                     f'Role {role} is pinned to its own chain', quota_class='role_pinned',
                                     considered=considered)
        first = self._entry(chain[0])
        if role != 'communicator':
            # Watchers keep their events for a later tick instead of reporting a failed analysis.
            known = [c['until'] for c in considered if c.get('until')]
            return RouteDecision('wait', '', '', first['account'], 'role_pinned',
                                 f'Every pinned entry for {role} is exhausted; event kept for a later tick',
                                 quota_class='role_pinned', until=min(known) if known else now + float(self.cfg['default_cooldown_seconds']),
                                 considered=considered)
        return RouteDecision('run', first['provider'], first['model'], first['account'], 'role_pinned',
                             f'Every pinned entry for {role} looks exhausted; the runner tries the chain and then '
                             'falls back to a no-LLM template', quota_class='role_pinned', considered=considered)

    def _class_for(self, role, task):
        name = self.cfg.get('role_classes', {}).get(role)
        if name == 'worker' and role == 'validator' and self.is_simple(task):
            return 'light'
        return name

    def is_simple(self, task):
        if self.cfg['light_validator']:
            return True
        try:
            return json.loads(getattr(task, 'plan_json', '{}') or '{}').get('simple') is True
        except (ValueError, AttributeError):
            return False

    def select(self, role, task=None, requires=None, prefer=None):
        """Return a RouteDecision, or None when this role is not routed (caller keeps its config)."""
        now = self.clock()
        if requires is None:
            try:
                plan = json.loads(getattr(task, 'plan_json', '{}') or '{}')
                requires = plan.get('requires', {}).get(role)
            except (ValueError, AttributeError):
                pass
            requires = requires or self.cfg.get('requires', {}).get(role)
        groups = {}
        for snap in self.store.snapshots():
            groups.setdefault(snap.account, []).append(snap)
        pinned = self._pinned(role, now, groups)
        if pinned is not None:
            return pinned  # D1: before task/global overrides and classes; never a wait
        override = getattr(task, 'provider_override', '') or ''
        if override:
            provider = self.providers.get(override, {})
            entry = {'provider': override, 'model': '', 'account': account_for(provider) or ''}
            info = self._state(entry, now, groups) if entry['account'] else {'state': 'ok', 'until': None, 'remaining': None}
            row = {**entry, 'state': info['state'], 'remaining': info['remaining']}
            if info['state'] == 'exhausted':
                return RouteDecision('wait', override, '', entry['account'], 'pinned', f"Explicit provider {override} has no quota (account {entry['account']}); not rerouted",
                                     quota_class='pinned', until=info['until'], considered=[row])
            return RouteDecision('run', override, '', entry['account'], 'pinned', f'Explicit provider {override} honoured',
                                 reserved_use=info['state'] == 'reserved', quota_class='pinned', considered=[row])
        name = self._class_for(role, task)
        classes = self.cfg.get('classes', {})
        if not name or name not in classes:
            return None
        considered, blockers = [], {}

        def evaluate(raw):
            entry = self._entry(raw)
            info = self._state(entry, now, groups)
            considered.append({**entry, 'state': info['state'], 'remaining': info['remaining'], 'until': info['until'],
                               'last_reset': info['last_reset'], 'confidence': info['confidence']})
            if info['state'] != 'ok':
                blockers[entry['account']] = info['until']
            return entry, info

        ordered = name in (self.cfg.get('ordered_classes') or [])

        def rank(items, index):
            entry, info = items
            if ordered:
                # Strict user-defined chain: first usable entry wins; no sticky/reset reordering.
                return (index,)
            sticky = prefer is not None and (entry['provider'], entry['model']) == tuple(prefer) and self.cfg['sticky_routes']
            return (not sticky, info['last_reset'] is None, -(info['last_reset'] or 0),
                    -(info['remaining'] if info['remaining'] is not None else 100.0), index)

        def pick(raws, source, reason, allow_reserved=False):
            evaluated = [evaluate(raw) for raw in raws]
            usable = [(e, i) for e, i in evaluated if i['state'] == 'ok' or (allow_reserved and i['state'] == 'reserved')]
            if not usable:
                return None
            order = sorted(range(len(usable)), key=lambda n: rank(usable[n], n))
            entry, info = usable[order[0]]
            return RouteDecision('run', entry['provider'], entry['model'], entry['account'], source,
                                 reason + (f" (account {entry['account']}, {info['state']}, remaining {info['remaining']})"),
                                 reserved_use=info['state'] == 'reserved', quota_class=name, considered=considered)

        entries = classes[name]
        if requires:
            need = set(requires)
            pool = [e for e in entries if self._entry(e)['account'] in need]
            # A required remote account may normally appear only as a workaround.
            for alternatives in self.cfg.get('workarounds', {}).values():
                for entry in alternatives:
                    if self._entry(entry)['account'] in need and entry not in pool:
                        pool.append(entry)
            for account in need:
                blockers[account] = self._state({'account': account, 'provider': '', 'model': ''}, now, groups)['until']
            found = pick(pool, 'requires', f'Role {role} requires account {sorted(need)}; reserve may be spent', allow_reserved=True)
            if found:
                return found
        else:
            found = pick(entries, 'class', f'{name} class has quota')
            if found:
                return found
        for account in list(blockers) or [None]:
            workarounds = self.cfg.get('workarounds', {}).get(account) or []
            found = pick(workarounds, 'workaround', f'Workaround for unavailable account {account}') if workarounds else None
            if found:
                return found
        if not requires:
            found = pick(self.cfg.get('reserve', {}).get(name, []), 'reserve', f'{name} class exhausted or reserved; using uncapped reserve')
            if found:
                return found
        known = {a: u for a, u in blockers.items() if u}
        account = min(known, key=known.get) if known else (next(iter(blockers), ''))
        until = known[account] if known else now + float(self.cfg['default_cooldown_seconds'])
        return RouteDecision('wait', '', '', account, 'wait', f'No {name} capacity for {role}; waiting for account {account} reset',
                             quota_class=name, until=until, considered=considered)


def build_router(config, db, clock=time.time, control=None):
    """Router bound to the task database; manual provider cooldowns are honoured."""
    cooldown = None
    if control is not None:
        def cooldown(key):
            with control.db._connect() as c:
                row = c.execute('SELECT until_time FROM provider_cooldowns WHERE account=?', (key,)).fetchone()
            return row[0] if row else 0
    return QuotaRouter(config, QuotaStore(db), clock, cooldown)


def resolve(router, role, explicit='', task=None, **kwargs):
    """-> (provider_override, model_override, decision) for one role call; decision None = unrouted."""
    from types import SimpleNamespace
    shim = SimpleNamespace(provider_override=explicit or getattr(task, 'provider_override', '') or '',
                           plan_json=getattr(task, 'plan_json', '{}') or '{}')
    decision = router.select(role, shim, **kwargs)
    if decision is None:
        return explicit or None, None, None
    if decision.action == 'wait' or decision.source == 'pinned':
        return explicit or None, None, decision
    return decision.provider, decision.model, decision


def record_route(db, config, role, decision, task_id=None, seen=None):
    """Log an explicit QUOTA_ROUTE event for every reroute, reserve spend, block or wait."""
    cfg = config.get('roles', {}).get(role, {})
    changed = decision.action == 'wait' or decision.reserved_use or decision.source == 'pinned' or (
        decision.provider != cfg.get('provider') or bool(decision.model and decision.model != cfg.get('model')))
    if decision.source == 'pinned' and decision.action == 'run' and not decision.reserved_use:
        changed = False
    signature = (decision.action, decision.provider, decision.model, decision.account)
    if not changed or (seen is not None and seen.get((task_id, role)) == signature):
        return False
    if seen is not None:
        seen[(task_id, role)] = signature
    target = f'{decision.provider}/{decision.model or "default"}' if decision.action == 'run' else f'wait for {decision.account}'
    db.log_event('QUOTA_ROUTE', 'quota', f"{role}: {cfg.get('provider', '?')} -> {target} [{decision.source}] {decision.reason}",
                 task_id=task_id, severity='WARN' if decision.action == 'wait' or decision.reserved_use else 'INFO',
                 payload={'role': role, 'default_provider': cfg.get('provider'), **decision.to_dict()})
    return True


def record_result(store, config, role, result, decision=None, task_id=None, clock=time.time):
    """Ledger row, passive snapshots and limit-error state after a run; returns the quota dict if exhausted."""
    data = result.structured_data or {}
    now = clock()
    store.put([Snapshot(**{k: v for k, v in s.items() if k in Snapshot.__dataclass_fields__}) for s in data.get('quota_snapshots', [])])
    quota = data.get('quota') if not result.success else None
    account = (quota or {}).get('account') or data.get('quota_account') or (decision.account if decision else '')
    resets = None
    if quota and quota.get('exhausted'):
        default = float(config.get('quota_routing', {}).get('default_cooldown_seconds', DEFAULTS['default_cooldown_seconds']))
        resets = quota.get('resets_at') or now + default
        if account:
            store.put([Snapshot(account, 'limit', 100.0, resets, 'limit_error', now, 'inferred')])
    store.record_run(role=role, provider=result.provider_name, model=data.get('model', '') or (decision.model if decision else ''),
                     account=account, task_id=task_id, tokens=usage_tokens(data.get('usage')), duration=result.duration_seconds,
                     success=result.success, limit_error=bool(quota), resets_at=resets, ts=now,
                     run_key=data.get('run_id', ''), usage=data.get('usage'),
                     num_turns=data.get('num_turns', 0), total_cost_usd=data.get('total_cost_usd', 0))
    return {**quota, 'resets_at': resets, 'account': account} if quota and quota.get('exhausted') else None


def persist_claude_run(database, result, *, task_id=None, agent_run_id=None, config=None):
    """Persist local/controller/campaign Claude receipts without duplicate ledger rows.

    run_id identifies an invocation, never a resumed native session. Replayed receipt
    imports are idempotent; unrelated invocations sharing session_id remain distinct.
    """
    import uuid
    from core.database import WorkspaceDatabase
    db = WorkspaceDatabase(database)
    data = result.structured_data
    data.setdefault('run_id', uuid.uuid4().hex)
    data.setdefault('quota_account', 'claude')
    store = QuotaStore(db)
    record_result(store, config or {}, result.role, result, task_id=int(task_id) if task_id else None)
    with db._connect() as c:
        c.execute('BEGIN IMMEDIATE')
        run = c.execute('SELECT id FROM agent_runs WHERE id=?', (int(agent_run_id),)).fetchone() if agent_run_id else None
        if agent_run_id and not run:
            raise ValueError('Unknown controller agent_run_id')
        if not run:
            label = 'claude-invocation:' + data['run_id']
            run = c.execute('SELECT id FROM agent_runs WHERE run_label=?', (label,)).fetchone()
            if not run:
                from core.models import utc_now_iso
                cursor = c.execute('INSERT INTO agent_runs(task_id,role,provider_name,status,spawned_at,run_label) VALUES(?,?,?,?,?,?)',
                    (int(task_id) if task_id else None, result.role, result.provider_name, 'RUNNING', utc_now_iso(), label))
                run = (cursor.lastrowid,)
        from core.models import utc_now_iso
        status = 'BOUNDED' if data.get('budget_exhausted') else 'COMPLETED' if result.success else 'FAILED'
        c.execute('''UPDATE agent_runs SET status=?,shutdown_at=?,duration_seconds=?,exit_code=?,
            output_preview=?,error_text=?,session_id=?,usage_json=?,num_turns=?,total_cost_usd=?,cli_log_path=? WHERE id=?''',
            (status, utc_now_iso(), result.duration_seconds, result.exit_code, result.output[:1000], result.error,
             data.get('session_id', ''), json.dumps(data.get('usage', {})), int(data.get('num_turns') or 0),
             float(data.get('total_cost_usd') or 0), result.log_file, run[0]))
    data['usage_recorded'] = True
    return run[0]


def route_call(router, db, config, role, explicit=None, seen=None):
    """Routing for callers outside the task pipeline (watchers). -> (provider_override, run_role kwargs, decision).

    A decision with action 'wait' means: skip this call now (nothing is consumed; try again next tick).
    """
    if router is None:
        return explicit, {}, None
    provider, model, decision = resolve(router, role, explicit or '')
    if decision is None:
        return explicit, {}, None
    record_route(db, config, role, decision, seen=seen)
    if decision.action == 'wait' or model is None:
        return explicit, {}, decision
    return provider, {'model_override': model}, decision
