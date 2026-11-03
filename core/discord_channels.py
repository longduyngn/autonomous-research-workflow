"""Guild-scoped Discord channel discovery and credential-preserving env updates."""
import json
import os
from pathlib import Path
import re
import urllib.request
import uuid

ALIASES={
    'task_proposal':{'task_proposal','task_proposals'},
    'approval_list':{'approve_list','approval_list','approvals','approval'},
    'task_log':{'task_log','task_logs'},
}
ENV_KEYS={'guild_id':'DISCORD_GUILD_ID','category_id':'DISCORD_CATEGORY_ID',
          **{key:'DISCORD_'+key.upper()+'_CHANNEL_ID' for key in ALIASES}}


def normalize(value):
    return re.sub(r'[\s_-]+','_',value.strip().lower()).strip('_')


def request(token,path,data=None):
    req=urllib.request.Request('https://discord.com/api/v10'+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization':'Bot '+token,'Content-Type':'application/json','User-Agent':'ResearchWorkflow/1.0'})
    with urllib.request.urlopen(req,timeout=20) as response:
        return json.load(response)


def discover(token, guild_id='', create=False):
    guilds=[{'id':guild_id}] if guild_id else request(token,'/users/@me/guilds')
    matches=[]
    for guild in guilds:
        channels=request(token,f"/guilds/{guild['id']}/channels")
        for category in channels:
            if category.get('type')==4 and normalize(category.get('name',''))=='antigravity_playground':
                matches.append((str(guild['id']),category,channels))
    if len(matches)!=1:
        raise ValueError('Select a guild with exactly one antigravity_playground category; discovery was missing or ambiguous')
    gid,category,channels=matches[0]
    result={'guild_id':gid,'category_id':str(category['id'])}
    for key,aliases in ALIASES.items():
        candidates=[c for c in channels if c.get('type')==0 and str(c.get('parent_id'))==result['category_id'] and normalize(c.get('name','')) in aliases]
        if len(candidates)>1:raise ValueError('Ambiguous Discord channel role: '+key)
        if not candidates and create:
            candidates=[request(token,f'/guilds/{gid}/channels',{'name':key.replace('_','-'),'type':0,'parent_id':result['category_id']})]
        if candidates:result[key]=str(candidates[0]['id'])
    return result


def update_env(path, mapping):
    """Update only allowlisted IDs; preserve every other byte, including secrets."""
    path=Path(path)
    values={ENV_KEYS[k]:str(v) for k,v in mapping.items() if k in ENV_KEYS}
    if mapping.get('task_proposal'):
        values['DISCORD_CHANNEL_ID']=str(mapping['task_proposal'])
    if any(not value.isdigit() for value in values.values()):raise ValueError('Channel settings must be numeric IDs')
    before=path.read_bytes() if path.exists() else b''
    bom=b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b''
    newline=b'\r\n' if b'\r\n' in before else b'\n'
    lines=before[len(bom):].splitlines(keepends=True);seen=set();after=[]
    for line in lines:
        match=re.match(rb'(?:export\s+)?([A-Z_0-9]+)\s*=',line)
        key=match[1].decode('ascii') if match else ''
        if key in values:
            if key in seen:raise ValueError('Duplicate channel key; resolve before updating: '+key)
            seen.add(key);after.append(key.encode()+b'='+values[key].encode()+newline)
        else:after.append(line)
    content=bom+b''.join(after)
    for key in values.keys()-seen:
        if content and not content.endswith((b'\n',b'\r')):content+=newline
        content+=key.encode()+b'='+values[key].encode()+newline
    # No secret-bearing backup is created. Retain existing permissions and replace atomically.
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
        if path.exists():os.chmod(temporary,path.stat().st_mode)
        if (path.read_bytes() if path.exists() else b'')!=before:
            raise RuntimeError('Env changed during channel update; retry rather than overwrite')
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return sorted(values)
