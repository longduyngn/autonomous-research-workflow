import json, glob, os, re, sys, collections as C
ROOT=os.path.expanduser('~/.claude/projects')
SELF='96476bdd-6e20-4c63-8b29-06e0b5f884e5'
files=[f for f in glob.glob(ROOT+'/**/*.jsonl',recursive=True) if SELF not in f]
rows=[]; G=C.Counter(); reads=C.Counter(); readbytes=C.Counter(); bashp=C.Counter(); tools=C.Counter(); fulllog=C.Counter()
for f in files:
    msgs={}; ent=set(); model=C.Counter(); first=None; ts=[]
    tl=C.Counter(); toolres=0
    for line in open(f,encoding='utf-8',errors='replace'):
        try: d=json.loads(line)
        except: continue
        if d.get('entrypoint'): ent.add(d['entrypoint'])
        if d.get('timestamp'): ts.append(d['timestamp'])
        m=d.get('message') or {}
        if d.get('type')=='user' and first is None and isinstance(m.get('content'),str): first=m['content'][:90]
        if d.get('type')=='user' and isinstance(m.get('content'),list):
            for b in m['content']:
                if isinstance(b,dict) and b.get('type')=='tool_result':
                    c=b.get('content'); toolres+=len(json.dumps(c)) if c else 0
        if d.get('type')!='assistant': continue
        mid=m.get('id') or d.get('uuid')
        u=m.get('usage') or {}
        msgs[mid]=u; model[m.get('model')]+=1
        for b in m.get('content') or []:
            if isinstance(b,dict) and b.get('type')=='tool_use':
                n=b['name']; i=b.get('input') or {}; tl[n]+=1; tools[n]+=1
                if n=='Read':
                    p=i.get('file_path','').replace(chr(92),'/'); k=re.sub(r'.*/(Antigravity_playgournd)/','',p,flags=re.I)
                    reads[k]+=1
                    if re.search(r'\.(log|jsonl)$',p) and not i.get('limit') and not i.get('offset'): fulllog[k]+=1
                if n in('Bash','PowerShell'):
                    cmd=(i.get('command') or '').strip()
                    first_tok=' '.join(cmd.split()[:2])[:40]
                    bashp[first_tok]+=1
    if not msgs: continue
    s=dict(inp=0,cc=0,cr=0,out=0)
    for u in msgs.values():
        s['inp']+=u.get('input_tokens',0) or 0; s['cc']+=u.get('cache_creation_input_tokens',0) or 0
        s['cr']+=u.get('cache_read_input_tokens',0) or 0; s['out']+=u.get('output_tokens',0) or 0
    peak=max(((u.get('input_tokens',0) or 0)+(u.get('cache_creation_input_tokens',0) or 0)+(u.get('cache_read_input_tokens',0) or 0)) for u in msgs.values())
    proj=os.path.basename(os.path.dirname(f)) if 'subagents' not in f else 'subagent:'+os.path.basename(os.path.dirname(os.path.dirname(f)))
    rows.append(dict(sid=os.path.basename(f)[:8],proj=proj.replace('D--Antigravity-playgournd','REPO'),ent=','.join(sorted(ent)),model=model.most_common(1)[0][0],turns=len(msgs),peak=peak,toolres=toolres,start=min(ts)[:16] if ts else '',tools=tl,first=first,**s))
    for k in s: G[k]+=s[k]
json.dump(dict(rows=[{**r,'tools':dict(r['tools'])} for r in rows],G=G,reads=reads.most_common(25),bash=bashp.most_common(25),tools=tools.most_common(25),fulllog=fulllog.most_common(10)),open(sys.argv[1],'w'),indent=1,default=str)
