"""Explicit local checkpoint management; no cloud allocation or credentials."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.checkpoints import CheckpointStore


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--store',required=True,help='Durable destination outside an ephemeral runtime')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    save=sub.add_parser('publish');save.add_argument('--source',required=True)
    save.add_argument('--file',action='append',required=True);save.add_argument('--metadata',required=True,help='JSON metadata file')
    restore=sub.add_parser('restore');restore.add_argument('id');restore.add_argument('--destination',required=True)
    restore.add_argument('--expected',required=True,help='JSON file with expected code/data/config hashes')
    args=p.parse_args();store=CheckpointStore(args.store)
    if args.command=='list':result=store.list()
    elif args.command=='publish':result=store.publish(args.source,args.file,json.loads(Path(args.metadata).read_text(encoding='utf8')))
    else:
        expected=json.loads(Path(args.expected).read_text(encoding='utf8'))
        if not all(x in expected for x in ('code_sha256','data_sha256','config_sha256')):
            raise ValueError('All compatibility hashes are required')
        result=store.restore(args.id,args.destination,expected)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
