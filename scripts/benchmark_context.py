"""Measure the controller-side prompt size per role (P2.6-03) to decide on output-compression tools.

  python scripts/benchmark_context.py            # table of characters and ~tokens per role prompt
  python scripts/benchmark_context.py --json

Approximate tokens = characters / 4. This measures what the controller sends (role instructions +
fixed context); tool outputs are measured separately (e.g. `rtk gain` if RTK is ever enabled).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def measure():
    from main import load_config
    config = load_config()
    rows = []
    for role, cfg in config.get('roles', {}).items():
        path = ROOT / cfg.get('prompt_file', f'roles/{role}.md')
        text = path.read_text(encoding='utf-8') if path.exists() else ''
        rows.append({'role': role, 'prompt_file': str(path.relative_to(ROOT)) if path.exists() else None,
                     'chars': len(text), 'approx_tokens': len(text) // 4})
    skills = sorted((ROOT / '.agents/skills').glob('*/SKILL.md'))
    rows.append({'role': '(skills index)', 'prompt_file': f'{len(skills)} SKILL.md files',
                 'chars': sum(len(p.read_text(encoding='utf-8')) for p in skills),
                 'approx_tokens': sum(len(p.read_text(encoding='utf-8')) for p in skills) // 4})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    rows = measure()
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    for row in rows:
        print(f"{row['role']:<20} {row['chars']:>7} chars  ~{row['approx_tokens']:>6} tokens  {row['prompt_file']}")


if __name__ == '__main__':
    main()
