"""Shallow source fetch shared by native CLI agents."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.repo_fetch import fetch_repo


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('url')
    p.add_argument('target_subdir')
    p.add_argument('--workspace', default='.')
    a = p.parse_args()
    try:
        print(json.dumps(fetch_repo(a.workspace, a.url, a.target_subdir)))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
