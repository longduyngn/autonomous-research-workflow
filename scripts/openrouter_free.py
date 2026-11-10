"""List/refresh zero-priced OpenRouter models (public catalog; no API key, no inference)."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.openrouter_free import FreeModelSelector  # noqa: E402

STATE = ROOT / 'storage' / 'openrouter'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['list', 'refresh', 'select'])
    parser.add_argument('--tier', default='worker', choices=['research', 'worker', 'fast'])
    parser.add_argument('--no-tools', action='store_true', help='include models without tool calling')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    selector = FreeModelSelector(STATE)
    if args.command == 'refresh':
        _, fetched = selector.catalog(refresh=True)
    ranked = selector.ranked(args.tier, need_tools=not args.no_tools)
    if args.command == 'select':
        print(ranked[0]['id'] if ranked else '')
        return
    rows = [{'id': m['id'], 'context': m.get('context_length'), 'tools': 'tools' in (m.get('supported_parameters') or []),
             'benchmarks': (m.get('benchmarks') or {}).get('artificial_analysis')} for m in ranked]
    if args.json:
        print(json.dumps({'tier': args.tier, 'cooling_down': selector.cooldowns(), 'models': rows}, indent=2))
        return
    for index, row in enumerate(rows, 1):
        print(f"{index:2}. {row['id']}  ctx={row['context']}  {row['benchmarks'] or ''}")
    if selector.cooldowns():
        print('cooling down:', selector.cooldowns())


if __name__ == '__main__':
    main()
