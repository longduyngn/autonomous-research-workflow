"""Run a bounded T4 notebook and preserve remote evidence before disconnecting.

Run inside a prepared workflow workspace. --check verifies saved evidence without
allocating another runtime. Execution never substitutes local compute for Colab.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONTROLLER = Path(os.environ.get('WORKFLOW_CONTROLLER_ROOT', str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(CONTROLLER))
from core.cloud import CloudTools
from main import load_config, load_dotenv


def utc():
    return datetime.now(timezone.utc).isoformat()


def unpack(result):
    if result.get('isError'):
        raise RuntimeError('Colab MCP call returned an error: ' + str(result.get('content', ''))[:1000])
    if result.get('structuredContent') is not None:
        return result['structuredContent']
    for item in result.get('content', []):
        if item.get('type') == 'text':
            try:
                return json.loads(item['text'])
            except ValueError:
                pass
    return result


def text_outputs(cell):
    parts = []
    for output in cell.get('outputs', []):
        text = output.get('text', '')
        parts.append(''.join(text) if isinstance(text, list) else str(text))
    return ''.join(parts)


def check(root, seconds):
    result = json.loads((root / 'artifacts/colab_timed_result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'completed', result.get('error', result['status'])
    assert result['requested_seconds'] == seconds
    assert result['measurement']['run_id'] == result['run_id']
    assert 'T4' in result['measurement']['gpu_name'], result['measurement']['gpu_name']
    assert result['measurement']['elapsed_seconds'] >= seconds
    assert result['shutdown']['status'] == 'disconnected'
    assert result['shutdown_verified'] is True, 'No independent disconnected-state evidence'
    assert result['shutdown_state'].get('runtime_stopped') is True, 'Stopped VM state was not observed'
    assert result['shutdown_state'].get('active_session_count') == 0, 'Active sessions were not checked'
    notebook = root / 'notebooks/colab_t4_timeline.ipynb'
    assert hashlib.sha256(notebook.read_bytes()).hexdigest() == result['notebook_sha256']
    nb = json.loads(notebook.read_text(encoding='utf-8'))
    text = '\n'.join(text_outputs(c) for c in nb['cells'])
    assert result['run_id'] in text and 'WORKFLOW_RESULT=' in text
    assert 'received_at_utc' in text and 'current_time_utc' in text
    print('PASS: real T4, matching run ID, saved timeline/time output, elapsed duration and verified disconnect')


def verify_shutdown(root, seconds):
    """Resolve an ambiguous status check without allocating or executing again."""
    load_dotenv(CONTROLLER / '.env'); load_dotenv(CONTROLLER / 'config/.env')
    artifact = root / 'artifacts/colab_timed_result.json'
    record = json.loads(artifact.read_text(encoding='utf-8'))
    assert record['status'] in {'executed', 'completed'}, 'Execution must finish before shutdown verification'
    assert record['measurement']['run_id'] == record['run_id']
    assert record['measurement']['elapsed_seconds'] >= seconds
    assert record.get('shutdown', {}).get('status') == 'disconnected'
    tools = CloudTools(root, load_config())
    state = tools.colab_call({'name':'runtime_status','arguments':{}})
    record.setdefault('shutdown_checks', []).append(record.get('shutdown_state', {}))
    record['shutdown_state'] = state
    record['shutdown_verified'] = state.get('runtime_stopped') is True and state.get('active_session_count') == 0
    if record['shutdown_verified']:
        record['status'] = 'completed'
        record['shutdown_verified_at_utc'] = utc()
    artifact.write_text(json.dumps(record, indent=2), encoding='utf-8')
    check(root, seconds)


def execute(root, seconds):
    load_dotenv(CONTROLLER / '.env')
    load_dotenv(CONTROLLER / 'config/.env')
    tools = CloudTools(root, load_config())
    artifact = root / 'artifacts/colab_timed_result.json'
    notebook = root / 'notebooks/colab_t4_timeline.ipynb'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    notebook.parent.mkdir(parents=True, exist_ok=True)
    if artifact.exists():
        previous = json.loads(artifact.read_text(encoding='utf-8'))
        if previous.get('status') == 'completed':
            return check(root, seconds)
        raise RuntimeError('A previous cloud attempt exists; inspect it before allocating again')
    timeline = json.loads((root / '.workflow/timeline.json').read_text(encoding='utf-8'))
    run_id = uuid.uuid4().hex
    record = {'run_id': run_id, 'status': 'starting', 'requested_seconds': seconds,
              'requested_hardware': 'T4', 'controller_started_at_utc': utc(), 'timeline': timeline}
    def save():
        artifact.write_text(json.dumps(record, indent=2), encoding='utf-8')
    save()
    sources = [
        "import json, platform, subprocess, time\nfrom datetime import datetime, timezone\n"
        "print('hello from antigravity-playground')\n"
        f"workflow_timeline = json.loads({json.dumps(json.dumps(timeline))})\n"
        "print('Workflow timestamps (UTC):')\nprint(json.dumps(workflow_timeline, indent=2))\n"
        "print('Gateway receives the request first; the communicator runs after validation.')\n"
        "print('current_time_utc=' + datetime.now(timezone.utc).isoformat())\n"
        "print('remote_platform=' + platform.platform())\n"
        "gpu_name = subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'], text=True).strip()\n"
        "print('gpu_name=' + gpu_name)\nassert 'T4' in gpu_name, 'Requested T4 was not allocated'\n",
        f"workflow_run_id = {run_id!r}\nworkflow_duration = {seconds}\n"
        "workflow_started = time.monotonic()\nworkflow_started_utc = datetime.now(timezone.utc).isoformat()\n"
        "print('RUN_STARTED=' + workflow_started_utc, flush=True)\n"
        "while time.monotonic() - workflow_started < workflow_duration:\n"
        "    print('running_seconds=' + str(round(time.monotonic()-workflow_started, 2)), flush=True)\n"
        "    time.sleep(min(15, max(0, workflow_duration - (time.monotonic()-workflow_started))))\n"
        "print('WORKFLOW_RESULT=' + json.dumps({'run_id':workflow_run_id,'gpu_name':gpu_name,"
        "'started_at_utc':workflow_started_utc,'current_time_utc':datetime.now(timezone.utc).isoformat(),"
        "'elapsed_seconds':time.monotonic()-workflow_started}), flush=True)\n"
    ]
    nb = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'workflow_hardware_intent': 'T4',
          'workflow_run_id': run_id, 'kernelspec': {'display_name':'Python 3','language':'python','name':'python3'}},
          'cells': [{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[], 'source':s.splitlines(True)} for s in sources]}
    notebook.write_text(json.dumps(nb, indent=2), encoding='utf-8')
    owned = False
    try:
        # Connecting itself does not claim that compute is running.
        tools.colab_connect({})
        names = [x['name'] for x in tools.colab_tools({}).get('tools', [])]
        assert 'run_code_cell' in names, 'Colab browser authentication/connection did not unlock execution'
        owned = True
        result = tools.colab_call({'name':'run_notebook','arguments':{
            'notebook_path':str(notebook.relative_to(root)), 'hardware':'T4','background_last_cell':True}})
        record['submission'] = result
        assert result['status'] == 'running_background', result
        assert result['background_cell'].get('status') == 'clicked_run_button', result['background_cell']
        record['status'] = 'running'
        save()
        deadline = time.monotonic() + seconds + 150
        while time.monotonic() < deadline:
            cells = unpack(tools.colab_call({'name':'get_cells','arguments':{'includeOutputs':True}})).get('cells', [])
            joined = '\n'.join(text_outputs(c) for c in cells)
            matches = [line.split('WORKFLOW_RESULT=', 1)[1] for line in joined.splitlines() if line.startswith('WORKFLOW_RESULT=')]
            if matches:
                record['measurement'] = json.loads(matches[-1])
                record['remote_cells'] = cells
                for idx, cell in enumerate(cells[:len(nb['cells'])]):
                    nb['cells'][idx]['outputs'] = cell.get('outputs', [])
                    nb['cells'][idx]['execution_count'] = cell.get('execution_count', idx + 1)
                notebook.write_text(json.dumps(nb, indent=2), encoding='utf-8')
                record['notebook_sha256'] = hashlib.sha256(notebook.read_bytes()).hexdigest()
                assert record['measurement']['run_id'] == run_id
                assert 'T4' in record['measurement']['gpu_name']
                assert record['measurement']['elapsed_seconds'] >= seconds
                record['status'] = 'executed'
                save()
                break
            if any(o.get('output_type') == 'error' for c in cells for o in c.get('outputs', [])):
                raise RuntimeError('Remote notebook cell raised an error')
            print(json.dumps({'state':'running','checked_at_utc':utc()}), flush=True)
            time.sleep(5)
        else:
            raise TimeoutError('Remote cell did not complete within its bounded execution window')
    except Exception as exc:
        record['status'] = 'error'
        record['error'] = str(exc)
        raise
    finally:
        try:
            if owned:
                record['shutdown'] = tools.colab_call({'name':'disconnect_runtime','arguments':{'release_bridge':False}})
                # Runtime status inspection is added by the controller tool; it
                # must observe the VM gone rather than trust a menu click.
                for _ in range(10):
                    state = tools.colab_call({'name':'runtime_status','arguments':{}})
                    if state.get('connected') is False:
                        break
                    time.sleep(1)
                record['shutdown_state'] = state
                record['shutdown_verified'] = state.get('connected') is False
                if record['status'] == 'executed' and record['shutdown_verified']:
                    record['status'] = 'completed'
            record['controller_finished_at_utc'] = utc()
        finally:
            save()
            tools.colab_release({})
    check(root, seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default=os.environ.get('WORKFLOW_WORKSPACE_ROOT', '.'))
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-shutdown', action='store_true', help='Observe stopped state for an already-executed run; never allocate again')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('Duration must be between 1 and 300 seconds')
    root = Path(args.workspace).resolve()
    if args.check:
        check(root, args.seconds)
    elif args.verify_shutdown:
        verify_shutdown(root, args.seconds)
    else:
        execute(root, args.seconds)


if __name__ == '__main__':
    main()
