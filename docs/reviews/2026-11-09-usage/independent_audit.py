"""Read-only source/receipt audit; writes only this review directory. No providers.

Run from workspace: python -B docs/reviews/2026-11-09-usage/independent_audit.py
Never exports user messages, tool bodies, reasoning, credentials, or environment.
"""
import ast
import collections
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
HAND = ROOT / 'storage/handoffs/smile-workflow-20261109'
CAMP = ROOT / 'storage/campaigns/smile-pre-publish'
FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def total(records):
    values = {k: sum(r.get(k, 0) for r in records) for k in FIELDS}
    values['uncached_input_tokens'] = values['input_tokens'] - values['cached_input_tokens']
    values['non_reasoning_output_tokens'] = values['output_tokens'] - values['reasoning_output_tokens']
    return values


def load_raw(path):
    records, duplicates, conflicts, malformed = {}, 0, [], []
    chars = collections.defaultdict(collections.Counter)
    calls = collections.defaultdict(collections.Counter)
    turns, users = {}, {}
    turn = None
    invariant_errors = []
    for n, line in enumerate(path.open(encoding='utf-8'), 1):
        try:
            r = json.loads(line)
        except ValueError:
            malformed.append(n)
            continue
        p, kind = r.get('payload', {}), r.get('type')
        if kind == 'turn_context':
            turn = p.get('turn_id')
            turns.setdefault(turn, {'started_at': r.get('timestamp'), 'model': p.get('model'), 'effort': p.get('effort')})
        if kind == 'token_usage_record':
            rid, u = p.get('response_id'), p.get('usage', {})
            if not rid:
                raise ValueError('Missing response_id')
            if rid in records:
                duplicates += 1
                if records[rid]['usage'] != u:
                    conflicts.append(rid)
            # Latest observation, not an invented component-wise maximum.
            records[rid] = {'usage': u, 'turn_id': p.get('turn_id'), 'timestamp': r.get('timestamp'), 'line': n}
            if (u.get('total_tokens') != u.get('input_tokens', 0) + u.get('output_tokens', 0)
                    or not 0 <= u.get('cached_input_tokens', 0) <= u.get('input_tokens', 0)
                    or not 0 <= u.get('reasoning_output_tokens', 0) <= u.get('output_tokens', 0)):
                invariant_errors.append(n)
        if kind != 'response_item':
            continue
        typ = p.get('type')
        if typ in ('function_call', 'custom_tool_call'):
            name = p.get('name', 'unknown')
            body = p.get('arguments', p.get('input', ''))
            chars[turn]['tool_call_arguments'] += len(body if isinstance(body, str) else json.dumps(body))
            calls[turn][name] += 1
        elif typ in ('function_call_output', 'custom_tool_call_output'):
            body = p.get('output', '')
            body = body if isinstance(body, str) else json.dumps(body)
            chars[turn]['tool_outputs'] += len(body)
            if 'truncated' in body.lower():
                chars[turn]['outputs_with_truncated_marker'] += 1
        elif typ == 'message':
            content = p.get('content', [])
            body = '\n'.join(x.get('text', '') for x in content if isinstance(x, dict)) if isinstance(content, list) else str(content)
            role = p.get('role', 'unknown')
            key = 'assistant_' + p.get('channel', 'unspecified') if role == 'assistant' else role + '_messages'
            chars[turn][key] += len(body)
            if role == 'user' and turn not in users:
                users[turn] = body  # local-only attribution; never serialized
    return dict(records=records, duplicate_records=duplicates, conflicting_ids=conflicts,
                malformed_lines=malformed, invariant_error_lines=invariant_errors,
                turns=turns, chars=chars, calls=calls, users=users)


def select_function(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == name)
    return compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec')


def probes():
    ns = {}
    exec(select_function(CAMP / 'scheduling_policy.py', 'operator_ready'), ns)
    ready = ns['operator_ready']
    result = {'claude_operator_ready_after_due_without_new_event': ready(1001, 1000, 'same', 'same'),
              'claude_operator_ready_before_due_with_new_event': ready(999, 1000, 'new', 'old')}
    import urllib.error
    import urllib.request
    exec(select_function(CAMP / 'scheduling_policy.py', 'review_key'), ns)
    ns.update(hashlib=hashlib, json=json)
    result['review_key_changes_for_only_new_message_id'] = (
        ns['review_key']({'operator_message': {'message_id': 'a'}}) !=
        ns['review_key']({'operator_message': {'message_id': 'b'}}))
    # Exercise the actual finish functions with fake receipts and fake filesystem checks.
    for provider, delay in [('codex', 3600), ('claude', 900)]:
        fake_path = type('FakePath', (), {
            '__init__': lambda self, *a: None,
            '__truediv__': lambda self, other: self,
            'exists': lambda self: True,
            'stat': lambda self: SimpleNamespace(st_mtime=2000),
        })
        checkpoint = {'message_id': 'm', 'completed_action': 'fixture', 'evidence': [{}]}
        raw = {'successful_tool_items': 1}
        data = {'next': 'COMPLETE'}
        rt = SimpleNamespace(read=lambda p, default=None: checkpoint, atomic=lambda *a: None,
            now=lambda: 'fixture', CAMP=fake_path(), OP_MODEL='fixture',
            codex_events=lambda p: raw,
            base=SimpleNamespace(acceptance=lambda r, n: (data, 'session')))
        context = dict(Path=fake_path, HERE=fake_path(), rt=rt,
            time=SimpleNamespace(time=lambda: 1000),
            datetime=SimpleNamespace(fromisoformat=lambda s: SimpleNamespace(timestamp=lambda: 1000)),
            MODELS={'operator': 'fixture'}, envelope=lambda *a: (data, 'session'), verify_evidence=lambda a: None)
        path = CAMP / ('codex_operator' if provider == 'codex' else 'claude_roles') / 'supervisor.py'
        exec(select_function(path, 'finish'), context)
        job = {'folder': 'fixture', 'nonce': 'fixture', 'message_id': 'm', 'started_at': 'fixture'}
        state = {'job': job} if provider == 'codex' else {'roles': {'operator': {'job': job}}}
        proc = SimpleNamespace(returncode=0)
        context['finish'](proc, state) if provider == 'codex' else context['finish']('operator', proc, state)
        row = state if provider == 'codex' else state['roles']['operator']
        result[provider + '_complete_due_seconds'] = row['next_due_unix'] - 1000
        assert result[provider + '_complete_due_seconds'] == delay
    sys.path.insert(0, str(ROOT))
    from core.role_memory import RoleMemory
    from core.providers import EphemeralAgentRunner
    error = urllib.error.HTTPError('https://example.invalid', 429, 'fixture', {'Retry-After':'120'}, None)
    with patch('urllib.request.urlopen', side_effect=error):
        try:
            EphemeralAgentRunner._post('https://example.invalid', {}, {}, 1)
        except Exception as exc:
            result['http_429_exception_from_actual_post'] = type(exc).__name__
            result['http_429_is_caught_by_http_error_handler'] = isinstance(exc, urllib.error.HTTPError)
    with tempfile.TemporaryDirectory(prefix='role-memory-audit-', dir=OUT) as tmp:
        memory = RoleMemory(tmp, 'operator')
        identity = {'provider': 'fixture'}
        state = memory.load(identity)
        memory.save(state, SimpleNamespace(success=True, output='', error='', log_file='',
            structured_data={'session_id': 'fixture', 'usage': {
                'input_tokens': 2, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 64109}}))
        result['claude_64111_input_saved_context_tokens'] = read(memory.path).get('context_tokens')
        # A cumulative counter cannot describe the current context window.
        memory.save(state, SimpleNamespace(success=True, output='', error='', log_file='',
            structured_data={'session_id': 'fixture', 'usage': {'input_tokens': 100000}}))
        result['cumulative_100000_rotates_next_load'] = not bool(memory.load(identity).get('session_id'))
    return result


def native_inventory():
    rows, missing = [], []
    patterns = ['claude_roles/turns/*/stdout.json', 'agy/turns/*/stdout.json',
        'coordination/turns/*/stdout.json', 'communication/turns/*/stdout.json',
        'guardian/*.raw.json', 'validation/*_stdout.json', 'researcher_opus.raw.json']
    for pattern in patterns:
        for path in sorted(CAMP.glob(pattern)):
            try:
                raw = read(path)
            except (ValueError, UnicodeError):
                missing.append({'path': str(path.relative_to(ROOT)), 'reason': 'empty_or_invalid_json'})
                continue
            if not isinstance(raw, dict) or not isinstance(raw.get('usage'), dict):
                missing.append({'path': str(path.relative_to(ROOT)), 'reason': 'no_native_usage'})
                continue
            usage = raw['usage']
            claude = 'modelUsage' in raw
            sid = raw.get('session_id') or raw.get('conversation_id')
            i, o = usage.get('input_tokens', 0), usage.get('output_tokens', 0)
            cr = usage.get('cache_read_input_tokens', usage.get('cache_read_tokens', 0))
            cw = usage.get('cache_creation_input_tokens', 0)
            receipt_path = path.parent / 'receipt.json'
            receipt = read(receipt_path) if receipt_path.exists() else {}
            rows.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path),
                group=str(path.relative_to(CAMP)).split('/')[0].split('\\')[0],
                role=receipt.get('sender') or ('validator' if 'validation' in path.parts else 'unknown'),
                receipt_at=receipt.get('at'),
                provider='claude' if claude else 'agy_or_unknown_wrapper', session_id=sid,
                native_uuid=raw.get('uuid'),
                model_keys=list(raw.get('modelUsage', {})),
                is_error=raw.get('is_error'), status=raw.get('status'),
                num_turns=raw.get('num_turns'), next=receipt.get('next'),
                input_native=i, cache_read_native=cr, cache_write_native=cw, output_native=o,
                thinking_native=usage.get('thinking_tokens', usage.get('output_tokens_details', {}).get('thinking_tokens')),
                total_native=usage.get('total_tokens'),
                claude_input_all=i+cr+cw if claude else None,
                cache_greater_than_native_input=cr > i,
                duration_ms=raw.get('duration_ms'), duration_api_ms=raw.get('duration_api_ms')))
    groups = []
    for group, provider in sorted({(r['group'], r['provider']) for r in rows}):
        subset = [r for r in rows if (r['group'], r['provider']) == (group, provider)]
        sums = {k: sum(r[k] or 0 for r in subset) for k in
                ('input_native', 'cache_read_native', 'cache_write_native', 'output_native', 'thinking_native')}
        groups.append(dict(group=group, provider=provider, files=len(subset),
            sessions=len({r['session_id'] for r in subset if r['session_id']}),
            receipt_field_sums=sums, cache_gt_input_files=sum(r['cache_greater_than_native_input'] for r in subset),
            warning='Receipt field sums only: not account usage; AGY schema/overlap unverified; Claude aggregate horizon needs validation'))
    candidates = []
    for group in sorted({r['group'] for r in rows if r['provider'] != 'claude'}):
        subset = [r for r in rows if r['group'] == group and r['provider'] != 'claude']
        by_sid = collections.defaultdict(list)
        for r in subset:
            if r['session_id']: by_sid[r['session_id']].append(r)
        keys = ['input_native', 'output_native', 'cache_read_native', 'thinking_native', 'total_native']
        checks = []
        for sid, xs in by_sid.items():
            xs.sort(key=lambda r: (r['num_turns'] or 0, r['receipt_at'] or ''))
            decreasing = {k: sum((a[k] or 0) > (b[k] or 0) for a,b in zip(xs, xs[1:])) for k in keys}
            checks.append(dict(session_id=sid, receipts=len(xs),
                num_turns=[r['num_turns'] for r in xs], decreasing_fields=decreasing,
                maxima={k:max(r[k] or 0 for r in xs) for k in keys}))
        candidates.append(dict(group=group, per_session_checks=checks,
            per_session_maxima_sums={k:sum(x['maxima'][k] for x in checks) for k in keys},
            raw_receipt_sums={k:sum(r[k] or 0 for r in subset) for k in keys},
            warning='Observed cumulative-counter hypothesis; not a verified provider billing total. Cache input relationship unresolved.'))
    # Claude message-ID reconciliation, scoped only to IDs exposed by campaign receipts.
    # Whole logs may contain more invocations than the selected receipt patterns.
    claude_rows = [r for r in rows if r['provider'] == 'claude']
    raw_messages, claude_sessions, conflicting = {}, [], set()
    fieldmap = {'input_tokens':'input_native','cache_creation_input_tokens':'cache_write_native',
                'cache_read_input_tokens':'cache_read_native','output_tokens':'output_native'}
    for sid in sorted({r['session_id'] for r in claude_rows if r['session_id']}):
        paths = list((Path.home()/'.claude/projects').rglob(sid+'.jsonl'))
        messages, occurrences = {}, 0
        for path in paths:
            for line in path.open(encoding='utf-8'):
                try: event = json.loads(line)
                except ValueError: continue
                m = event.get('message', {})
                if event.get('type') != 'assistant' or not m.get('id') or not m.get('usage'): continue
                occurrences += 1
                key = m['id']; usage = m['usage']
                if key in raw_messages and raw_messages[key] != usage: conflicting.add(key)
                messages[key] = usage; raw_messages[key] = usage
        native = [r for r in claude_rows if r['session_id'] == sid]
        observed = {k:sum(u.get(k,0) for u in messages.values()) for k in fieldmap}
        expected = {k:sum(r[v] for r in native) for k,v in fieldmap.items()}
        claude_sessions.append(dict(session_id=sid, paths=[str(p) for p in paths],
            sha256=[sha(p) for p in paths], receipt_files=len(native), assistant_occurrences=occurrences,
            unique_message_ids=len(messages), raw_message_totals=observed,
            selected_receipt_sums=expected, exact_match=bool(paths) and observed==expected))
    claude_roles = []
    for role in sorted({r['role'] for r in claude_rows}):
        xs=[r for r in claude_rows if r['role']==role]
        claude_roles.append(dict(role=role, files=len(xs),
            sums={k:sum(r[v] for r in xs) for k,v in fieldmap.items()}))
    return {'files': rows, 'groups': groups, 'missing': missing,
        'agy_cumulative_candidates': candidates, 'claude_receipt_role_sums':claude_roles,
        'claude_raw_session_reconciliation':claude_sessions,
        'claude_whole_session_superset':{'unique_message_ids':len(raw_messages),
            'conflicting_message_ids':list(conflicting),
            'sums':{k:sum(u.get(k,0) for u in raw_messages.values()) for k in fieldmap},
            'warning':'Session superset, not campaign-attributed. Do not add to native receipts.'}}


def main():
    headline = read(HAND / 'CODEX_CAMPAIGN_TOTAL.json')
    snapshot = read(HAND / 'CODEX_TOKEN_USAGE.json')
    discovered = read(HAND / 'DISCOVERED_CAMPAIGN_USAGE.json')
    source_by_id = {snapshot['primary_thread']['metadata']['id']: snapshot['primary_thread']['source_path']}
    source_by_id.update({x['metadata']['id']: x['source_path'] for x in snapshot['linked_threads']})
    source_by_id.update({x['thread_id']: x['source_path'] for x in snapshot['historical_operator_threads']})
    source_by_id.update({x['thread_id']: x['path'] for x in discovered['threads']})
    selected_turns = {x['thread_id']: set(x['matched_turn_ids']) for x in discovered['threads']}
    packaging_turn = snapshot['primary_thread']['per_turn'][-1]['turn_id']
    raw_by_id, components, all_ids, cross_duplicates, per_turn = {}, [], set(), [], []
    for component in headline['components']:
        sid = component['thread']
        path = Path(source_by_id[sid]); raw = load_raw(path); raw_by_id[sid] = raw
        eligible = {rid: r for rid, r in raw['records'].items()
                    if (component['name'] != 'interactive_root_before_packaging' or r['turn_id'] != packaging_turn)
                    and (component['name'] != 'older_campaign_turns_only' or r['turn_id'] in selected_turns[sid])}
        for rid in eligible:
            if rid in all_ids: cross_duplicates.append(rid)
            all_ids.add(rid)
        observed = total([r['usage'] for r in eligible.values()])
        expected = component['metrics']
        components.append(dict(name=component['name'], thread=sid, source_path=str(path), sha256=sha(path),
            responses=len(eligible), expected=expected, observed=observed,
            match=all(observed[k] == expected[k] for k in FIELDS),
            duplicate_records_whole_source=raw['duplicate_records'], conflicts_whole_source=raw['conflicting_ids'],
            invariant_errors_whole_source=raw['invariant_error_lines'], malformed_whole_source=raw['malformed_lines']))
        for tid in dict.fromkeys(r['turn_id'] for r in eligible.values()):
            records = [r['usage'] for r in eligible.values() if r['turn_id'] == tid]
            sums = total(records)
            per_turn.append(dict(component=component['name'], thread=sid, turn=tid,
                **raw['turns'].get(tid, {}), responses=len(records), **sums,
                mean_input_per_response=round(sums['input_tokens'] / len(records), 2),
                exposed_chars=dict(raw['chars'][tid]), tool_calls=dict(raw['calls'][tid])))
    bundle = []
    for line in (HAND / 'BUNDLE_SHA256.txt').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        path = HAND / relative
        bundle.append({'path': relative, 'match': path.exists() and sha(path) == expected})
    source_checks = []
    snippet_text = (HAND / 'SOURCE_EVIDENCE.md').read_text(encoding='utf-8')
    for match in re.finditer(r'## (.*?):\d+-\d+\nSHA256 ([a-f0-9]{64})', snippet_text):
        path = ROOT / match[1]
        source_checks.append({'path':match[1], 'recorded_sha256':match[2],
            'observed_sha256':sha(path) if path.exists() else None,
            'match':path.exists() and sha(path)==match[2]})
    raw_root = raw_by_id[headline['components'][0]['thread']]
    cutoff = dt.datetime.fromisoformat(snapshot['generated_at'])
    package = total([r['usage'] for r in raw_root['records'].values() if r['turn_id'] == packaging_turn
                     and dt.datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00')) <= cutoff])
    source_files = ['core/providers.py','core/role_memory.py','core/orchestrator.py',
        'core/conversation.py','gateways/discord_bot.py',
        'storage/campaigns/smile-pre-publish/codex_operator/supervisor.py',
        'storage/campaigns/smile-pre-publish/claude_roles/supervisor.py',
        'storage/campaigns/smile-pre-publish/communication/supervisor.py',
        'storage/campaigns/smile-pre-publish/coordination/supervisor.py',
        'storage/campaigns/smile-pre-publish/scheduling_policy.py']
    report = dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        scope='Pinned campaign component/turn manifest; numeric local audit; not all accounts or all providers',
        components=components, combined=total([r['observed'] for r in components]),
        cross_component_duplicate_response_ids=cross_duplicates,
        packaging_snapshot_cutoff=snapshot['generated_at'], packaging_recomputed=package,
        packaging_matches=all(package[k] == snapshot['current_request_so_far'][k] for k in FIELDS),
        per_turn=per_turn, bundle_hashes=bundle, source_snippet_hashes=source_checks, offline_probes=probes(),
        inspected_source_sha256={p:sha(ROOT/p) for p in source_files},
        native_receipts=native_inventory(),
        limitations=['Exposed character counts are not model tokens or repeated-context allocation.',
            'Semantic source/system/tool/history token shares are not supplied by the raw usage records.',
            'Mixed matching turns are fully attributed; campaign coverage is neither exact nor a mathematical lower bound.',
            'No new inference, GPU, Discord, credential access, or live quota check.'])
    (OUT / 'INDEPENDENT_EVIDENCE.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'INDEPENDENT_TURNS.csv').open('w', encoding='utf-8-sig', newline='') as f:
        rows = [{k:v for k,v in r.items() if k not in ('tool_calls','exposed_chars')} for r in per_turn]
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(json.dumps({'total': report['combined'], 'component_matches': [r['match'] for r in components],
        'unique_responses': len(all_ids), 'cross_duplicates': len(cross_duplicates),
        'packaging_matches': report['packaging_matches'],
        'hash_matches': sum(x['match'] for x in bundle), 'hash_files': len(bundle),
        'probes': report['offline_probes'],
        'native_receipt_files':len(report['native_receipts']['files'])}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
