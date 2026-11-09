"""Independent, offline verification of downloaded cloud Claude pilot artifacts."""
import argparse
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def pure_metric(path):
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    allowed=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.Call,ast.Name,
             ast.Load,ast.Store,ast.BinOp,ast.Mult,ast.Add,ast.Constant,ast.GeneratorExp,ast.comprehension)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef) or tree.body[0].name!='sum_squares':
        raise ValueError('Unexpected CPU probe source')
    for node in ast.walk(tree):
        if not isinstance(node,allowed):raise ValueError('Probe contains a disallowed AST node')
        if isinstance(node,ast.Name) and node.id not in {'n','i','sum','range'}:raise ValueError('Unexpected name')
        if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Name) or node.func.id not in {'sum','range'}):
            raise ValueError('Unexpected call')
    env={'__builtins__':{},'sum':sum,'range':range}
    exec(compile(tree,str(path),'exec'),env)
    return env['sum_squares']


def verify(directory,secret=None):
    d=Path(directory)
    manifest=load(d/'download-manifest.json')
    for name,info in manifest['files'].items():
        path=(d/name).resolve()
        if not path.is_relative_to(d.resolve()):raise ValueError('Invalid manifest path')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=info['sha256']:raise ValueError('Artifact hash mismatch')
        if secret and secret.encode() in data:raise ValueError('Credential found in exported artifacts')
    row=load(d/'state.json')
    assert row['state']=='COMPLETED' and row['completed']==row['total']==6 and row.get('finished_at')
    assert row['boot_id']==manifest['boot_id']
    assert row['baseline_validation']['verdict']=='FAIL' and row['final_validation']['verdict']=='PASS'
    for version,expected_verdict in [('v1','FAIL'),('v2','PASS')]:
        sub=d/'snapshots'/version
        hashes=load(sub/'manifest.json')
        for name,digest in hashes.items():assert hashlib.sha256((sub/name).read_bytes()).hexdigest()==digest
        fn=pure_metric(sub/'metric.py')
        validation=load(sub/'validation.json')
        assert validation['source_sha256']==hashes['metric.py']
        assert [c['n'] for c in validation['cases']]==[0,1,2,10,10000]
        independently_correct=[]
        for case in validation['cases']:
            n=case['n'];actual=fn(n);expected=n*(n+1)*(2*n+1)//6
            assert case['actual']==actual and case['expected']==expected
            assert case['pass']==(actual==expected)
            independently_correct.append(actual==expected)
        assert ('PASS' if all(independently_correct) else 'FAIL')==expected_verdict
        assert validation['verdict']==expected_verdict
        assert validation['published']==load(sub/'result.json')
    assert load(d/'operator/result.json')=={'n':10000,'sum_squares':sum(i*i for i in range(1,10001))}
    assert row['roles']['operator']['session_id']!=row['roles']['validator']['session_id']
    assert row['handshake']['native']
    stats={};all_sends={};native_turns=0
    for role in ('operator','validator'):
        events=[json.loads(x) for x in (d/(role+'.native.jsonl')).read_text(encoding='utf-8').splitlines()]
        results=[x for x in events if x.get('type')=='result']
        assert results and {x['session_id'] for x in results}=={row['roles'][role]['session_id']}
        calls=[];acks={}
        for event in events:
            message=event.get('message',{})
            content=message.get('content',[]) if isinstance(message,dict) else []
            for part in content:
                if not isinstance(part,dict):continue
                if part.get('type')=='tool_use' and part.get('name')=='SendMessage':calls.append(part)
                if part.get('type')=='tool_result':acks[part['tool_use_id']]=part
        assert calls and all(c['id'] in acks and not acks[c['id']].get('is_error') for c in calls)
        all_sends[role]=[c['input'] for c in calls]
        last=results[-1].get('modelUsage',{})
        assert set(last)=={'claude-sonnet-5-5'}
        stats[role]={'session_id':row['roles'][role]['session_id'],'native_turns':len(results),
                     'messages_sent':len(calls),'cumulative_model_usage':last}
        native_turns+=len(results)
    assert row['handshake']['nonce'] in json.dumps(all_sends['operator'])
    assert 'PAIR_ACK:'+row['handshake']['nonce'] in json.dumps(all_sends['validator'])
    assert 'FEEDBACK' in json.dumps(all_sends['validator']) and 'off-by-one' in json.dumps(all_sends['validator'])
    receipt={'passed':True,'real_claude':True,'synthetic':False,
        'scope':'Disclosed CPU fault-injection pilot; both Claude roles and their supervisor ran on Colab',
        'job_id':row['job_id'],'boot_id':row['boot_id'],'wall_seconds':round(row['finished_at']-row['started_at'],3),
        'baseline_verdict':'FAIL','final_verdict':'PASS','test_cases':len(row['final_validation']['cases']),
        'independently_recomputed':True,'roles':stats,'native_turns':native_turns,
        'files_hash_verified':len(manifest['files']),'credential_scan_passed':bool(secret),
        'limitations':['CPU pilot, not research benchmark','Native sessions survived workflow turns, not a VM replacement',
                       'CLI costUSD is a list-price estimate, not a subscription invoice']}
    fields=('inputTokens','outputTokens','cacheCreationInputTokens','cacheReadInputTokens')
    receipt['cumulative_model_usage']={field:sum(
        r['cumulative_model_usage']['claude-sonnet-5-5'].get(field,0) for r in stats.values()) for field in fields}
    from core.role_memory import atomic_json
    atomic_json(d/'verification.json',receipt)
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--job-id',default='claude-pair-20261107-1')
    a=p.parse_args()
    from main import load_dotenv
    load_dotenv(ROOT/'.env');load_dotenv(ROOT/'config/.env')
    receipt=verify(ROOT/'storage/remote-claude'/a.job_id,os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'))
    summary={k:receipt[k] for k in ('passed','job_id','wall_seconds','baseline_verdict','final_verdict',
        'test_cases','independently_recomputed','native_turns','files_hash_verified','credential_scan_passed')}
    summary['messages_sent']={r:v['messages_sent'] for r,v in receipt['roles'].items()}
    print(json.dumps(summary))


if __name__=='__main__':main()
