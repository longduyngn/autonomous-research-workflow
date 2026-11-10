"""One fenced, bounded snippet operation on an existing idle Colab runtime."""
import json
import time
from core.cloud import _extract_cell_id, _extract_outputs_text


def run_snippet(client, observe, code, hardware='T4', timeout_seconds=300):
    if not isinstance(code, str) or not code.strip() or len(code) > 32000:
        raise ValueError('code must be nonempty and <=32000 characters')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError('timeout_seconds must be between 1 and 300')
    if hardware.upper() not in {'CPU', 'T4', 'L4', 'A100'}:
        raise ValueError('Unsupported existing hardware')
    state = observe()
    if state.get('connected') is not True or state.get('idle') is not True:
        raise PermissionError('Existing connected idle runtime not proven; snippet cannot allocate or interrupt compute')
    names = {t['name'] for t in client.tools()}
    if not {'get_cells', 'add_code_cell', 'run_code_cell', 'delete_cell'} <= names:
        raise ValueError('Connect MCP to the existing browser before running a snippet')
    # Validate hardware and detached GPU activity *before* executing user code.
    # This preflight does not select hardware, connect a VM or terminate a job.
    wrapped = ("import subprocess as _ws_sp\n"
        "_ws_gpu = _ws_sp.run(['nvidia-smi','--query-gpu=name','--format=csv,noheader'], capture_output=True, text=True) if __import__('shutil').which('nvidia-smi') else None\n"
        f"_ws_hw = {hardware.upper()!r}\n"
        "if _ws_hw != 'CPU' and (not _ws_gpu or _ws_hw not in _ws_gpu.stdout.upper()): raise RuntimeError('Requested existing hardware not present; no allocation attempted')\n"
        "if _ws_gpu:\n"
        " _ws_apps = _ws_sp.run(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'], capture_output=True, text=True)\n"
        " if _ws_apps.returncode or _ws_apps.stdout.strip(): raise RuntimeError('GPU busy or unknown; snippet refused')\n"
        f"exec(compile({code!r}, '<workflow-snippet>', 'exec'))\n")
    cid, completed = None, False
    started = time.monotonic()
    result = None
    try:
        listed = client.call('get_cells', {'includeOutputs': False}, timeout=15)
        if listed.get('isError'):
            raise RuntimeError('Cannot inspect notebook cells')
        cells = (listed.get('structuredContent') or {}).get('cells')
        if not isinstance(cells, list):
            raise RuntimeError('Cell list unavailable; snippet refused')
        added = client.call('add_code_cell', {'cellIndex': len(cells), 'language': 'python', 'code': wrapped}, timeout=15)
        cid = _extract_cell_id(added)
        if not cid or added.get('isError'):
            raise RuntimeError('Temporary snippet cell creation failed')
        raw = client.call('run_code_cell', {'cellId': cid}, timeout=timeout_seconds)
        # Preserve stdout/stderr from the result where available; never return whole logs.
        outputs = (raw.get('structuredContent') or {}).get('outputs', [])
        if raw.get('isError') and not outputs:
            raise RuntimeError('Run call failed without final cell output')
        completed = True
        stderr = ''.join(''.join(o.get('text', [])) if isinstance(o.get('text'), list) else str(o.get('text', ''))
                         for o in outputs if o.get('name') == 'stderr')
        result = {'status': 'error' if raw.get('isError') or any(o.get('output_type') == 'error' for o in outputs) else 'completed',
                  'cell_id': cid, 'stdout': _extract_outputs_text(raw)[-8000:], 'stderr': stderr[-2000:],
                  'elapsed_seconds': round(time.monotonic() - started, 3)}
    except (TimeoutError, OSError, RuntimeError) as exc:
        if not cid:
            raise
        # A timed-out MCP call may still be executing. Never delete/interrupt it
        # or replay it automatically; preserve the cell identity for reconciliation.
        result = {'status': 'unknown', 'cell_id': cid, 'error': 'Snippet execution uncertain; reconcile cell before retry: ' + str(exc)[:200],
                  'stdout': '', 'stderr': '', 'cleanup': 'retained; execution state unknown'}
    finally:
        if cid and completed:
            try:
                cleanup = client.call('delete_cell', {'cellId': cid}, timeout=15)
                if result is not None:
                    result['cleanup'] = 'error' if cleanup.get('isError') else 'deleted'
            except Exception as exc:
                if result is not None:
                    result['cleanup'] = 'error: ' + str(exc)[:200]
    return result
