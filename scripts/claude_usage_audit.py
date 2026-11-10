"""Offline Claude transcript audit. No model calls; explicit scope and exclusions."""
import argparse
import collections
import json
import re
from datetime import datetime, timezone
from pathlib import Path

FIELDS = {'inp':'input_tokens','cc':'cache_creation_input_tokens',
          'cr':'cache_read_input_tokens','out':'output_tokens'}


def audit(root, exclude_sessions=()):
    sessions = {}
    files = sorted(Path(root).rglob('*.jsonl'))
    excluded, malformed = [], 0
    for path in files:
        if any(value in path.stem for value in exclude_sessions):
            excluded.append(str(path))
            continue
        row = sessions.setdefault(path.stem, {'messages':{},'tools':{},'entrypoints':set(),'times':[], 'models':collections.Counter()})
        with path.open(encoding='utf-8',errors='replace') as stream:
            for line in stream:
                try:
                    data = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(data,dict):
                    continue
                if data.get('entrypoint'):
                    row['entrypoints'].add(data['entrypoint'])
                if data.get('timestamp'):
                    row['times'].append(data['timestamp'])
                message = data.get('message') or {}
                if data.get('type')!='assistant' or not isinstance(message,dict):
                    continue
                mid = message.get('id') or data.get('uuid')
                if not mid:
                    continue
                usage = message.get('usage') or {}
                old = row['messages'].setdefault(mid,{})
                # Split/duplicate assistant fragments share an ID. Counters can
                # arrive progressively; use each field's maximum, never sum them.
                for name in FIELDS.values():
                    value = usage.get(name)
                    if isinstance(value,(int,float)) and value >= 0:
                        old[name] = max(old.get(name,0),int(value))
                if message.get('model'):
                    row['models'][message['model']]+=1
                content = message.get('content') or []
                if isinstance(content,list):
                    for index,block in enumerate(content):
                        if isinstance(block,dict) and block.get('type')=='tool_use':
                            row['tools'][block.get('id') or (mid,str(index))] = block
    total, tool_total, reads, full_logs, commands = [collections.Counter() for _ in range(5)]
    rows = []
    for sid,row in sessions.items():
        if not row['messages']:
            continue
        values={short:sum(u.get(long,0) for u in row['messages'].values()) for short,long in FIELDS.items()}
        tools = collections.Counter()
        hashes = 0
        for block in row['tools'].values():
            name,arguments = block.get('name',''),block.get('input') or {}
            tools[name]+=1
            if name=='Read':
                path=arguments.get('file_path','').replace('\\','/')
                path=re.sub(r'^.*?/Antigravity_playgournd/','',path,flags=re.I)
                reads[path]+=1
                if re.search(r'\.(log|jsonl)$',path) and not arguments.get('limit') and not arguments.get('offset'):
                    full_logs[path]+=1
            if name in {'Bash','PowerShell'}:
                command=arguments.get('command') or ''
                hashes+=command.lower().count('get-filehash')
                # Raw commands and prompts are not exported by the audit.
                commands['Get-FileHash' if 'get-filehash' in command.lower() else 'other']+=1
        peak=max(sum(u.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens'))
                 for u in row['messages'].values())
        rows.append({'sid':sid[:8],'session_id':sid,'ent':','.join(sorted(row['entrypoints'])),
            'model':row['models'].most_common(1)[0][0] if row['models'] else '',
            'turns':len(row['messages']),'peak':peak,'tools':dict(tools),'get_filehash':hashes,
            'start':min(row['times']) if row['times'] else '',**values})
        total.update(values)
        tool_total.update(tools)
    return {'rows':rows,'G':dict(total),'reads':reads.most_common(50),'bash':commands.most_common(),
            'tools':tool_total.most_common(),'fulllog':full_logs.most_common(25),
            'scope':{'root':str(Path(root).resolve()),'files':len(files),'excluded_files':excluded,
                     'exclude_sessions':list(exclude_sessions),'malformed_lines':malformed,
                     'captured_at':datetime.now(timezone.utc).isoformat(),
                     'dedup':'message id per native session; max counters across fragments; unique tool id',
                     'tokens':'inp+cc+out excludes cache read; processed total also includes cr; neither is quota percent or billed cost'}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('out')
    p.add_argument('--root',default=str(Path.home()/'.claude/projects'))
    p.add_argument('--exclude-session',action='append',default=[])
    a=p.parse_args()
    Path(a.out).write_text(json.dumps(audit(a.root,a.exclude_session),indent=2,ensure_ascii=False),encoding='utf-8')


if __name__=='__main__':main()
