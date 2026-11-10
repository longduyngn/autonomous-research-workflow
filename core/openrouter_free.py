"""Discover and rank zero-priced OpenRouter models (P2.1-02b).

Listing models uses the public catalog endpoint and costs no inference quota.
Selection never returns a priced model: ``free_only`` is a hard guarantee.
Per-model cooldowns (rate limit / daily free limit) are kept beside the cache
so the next call moves to the next free model instead of retrying the same one.
"""
import json
import time
import urllib.request
from pathlib import Path

CATALOG_URL = 'https://openrouter.ai/api/v1/models'
DEFAULT_TTL = 6 * 3600
# Opaque routers hide which model answered; usable only as an explicit last resort.
ROUTER_IDS = {'openrouter/free', 'openrouter/auto'}
TIER_WEIGHTS = {
    # Agentic/coding ability weighs most for tool-using roles, general intelligence for chat.
    'research': {'agentic_index': 2.0, 'coding_index': 1.5, 'intelligence_index': 1.0},
    'worker': {'agentic_index': 2.0, 'coding_index': 1.5, 'intelligence_index': 1.0},
    'fast': {'intelligence_index': 2.0, 'coding_index': 1.0, 'agentic_index': 1.0},
}


def is_zero_priced(model):
    pricing = model.get('pricing') or {}
    try:
        return all(float(pricing.get(key, 1)) == 0.0 for key in ('prompt', 'completion')) and \
            all(float(pricing[key]) == 0.0 for key in ('request', 'image') if key in pricing)
    except (TypeError, ValueError):
        return False


def supports_tools(model):
    return 'tools' in (model.get('supported_parameters') or [])


def _index(model, name):
    value = ((model.get('benchmarks') or {}).get('artificial_analysis') or {}).get(name)
    return float(value) if isinstance(value, (int, float)) else None


def rank_key(model, tier='worker'):
    """Sort key (ascending = better): weighted published benchmark indexes (missing = 0),
    then context length, then newest. Unbenchmarked models rank after benchmarked ones."""
    weights = TIER_WEIGHTS.get(tier, TIER_WEIGHTS['worker'])
    score = sum(weight * (_index(model, name) or 0.0) for name, weight in weights.items())
    return (-score, -(model.get('context_length') or 0), -(model.get('created') or 0))


def candidates(catalog, tier='worker', need_tools=True, min_context=32768, allow_router=False, exclude=()):
    """Zero-priced models usable for ``tier`` in best-first order."""
    models = catalog.get('data', catalog) if isinstance(catalog, dict) else catalog
    pool = []
    for model in models or []:
        model_id = model.get('id', '')
        if not model_id or model_id in exclude or not is_zero_priced(model):
            continue
        if model_id in ROUTER_IDS and not allow_router:
            continue
        if need_tools and not supports_tools(model):
            continue
        if (model.get('context_length') or 0) < min_context:
            continue
        pool.append(model)
    pool.sort(key=lambda m: rank_key(m, tier))
    return pool


class FreeModelSelector:
    """Cached catalog + cooldowns under ``state_dir``; never selects a priced model."""

    def __init__(self, state_dir, fetch=None, clock=time.time, ttl=DEFAULT_TTL):
        self.state_dir = Path(state_dir)
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.ttl = ttl

    @staticmethod
    def _fetch(timeout=30):
        request = urllib.request.Request(CATALOG_URL, headers={'Accept': 'application/json',
                                                               'User-Agent': 'workflow-orchestrator'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))

    @property
    def cache_path(self):
        return self.state_dir / 'free_models.json'

    @property
    def cooldown_path(self):
        return self.state_dir / 'free_model_cooldowns.json'

    def _read(self, path, default):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, indent=1), encoding='utf-8')
        tmp.replace(path)

    def catalog(self, refresh=False):
        """Return (catalog, fetched_at). A failed refresh falls back to the stale cache."""
        cached = self._read(self.cache_path, {})
        fresh = cached.get('fetched_at', 0) + self.ttl > self.clock()
        if cached.get('data') and fresh and not refresh:
            return cached, cached['fetched_at']
        try:
            raw = self.fetch()
        except Exception:
            if cached.get('data'):
                return cached, cached.get('fetched_at', 0)
            raise
        keep = ('id', 'name', 'created', 'context_length', 'pricing', 'supported_parameters', 'benchmarks')
        snapshot = {'fetched_at': self.clock(), 'source': CATALOG_URL,
                    'data': [{k: m.get(k) for k in keep} for m in raw.get('data', []) if is_zero_priced(m)]}
        self._write(self.cache_path, snapshot)
        return snapshot, snapshot['fetched_at']

    def cooldowns(self):
        now = self.clock()
        return {k: v for k, v in self._read(self.cooldown_path, {}).items() if v > now}

    def cool_down(self, model_id, until):
        data = self.cooldowns()
        data[model_id] = max(float(until), data.get(model_id, 0))
        self._write(self.cooldown_path, data)

    def ranked(self, tier='worker', need_tools=True, min_context=32768, allow_router=False):
        catalog, _ = self.catalog()
        return candidates(catalog, tier, need_tools, min_context, allow_router, exclude=set(self.cooldowns()))

    def select(self, tier='worker', need_tools=True, min_context=32768, allow_router=False):
        """Best free model id not cooling down, or None when every candidate is cooling down."""
        pool = self.ranked(tier, need_tools, min_context, allow_router)
        return pool[0]['id'] if pool else None

    def next_reset(self):
        values = list(self.cooldowns().values())
        return min(values) if values else None

    def is_free(self, model_id):
        catalog, _ = self.catalog()
        return any(m.get('id') == model_id and is_zero_priced(m) for m in catalog.get('data', []))
