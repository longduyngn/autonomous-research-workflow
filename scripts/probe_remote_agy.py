"""Small host-only Colab inspection/bootstrap probe; no credentials are printed."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from core.cloud import CloudTools, _extract_cell_id, _extract_outputs_text
from main import load_config


def main():
    t = CloudTools(ROOT / 'storage/remote-agy/colab-personal', load_config())
    if len(sys.argv) > 1 and sys.argv[1] == 'inspect':
        print(json.dumps(t.colab_call({'name': 'get_cells', 'arguments': {'includeOutputs': True}})))
        return
    code = '''import platform, sys, shutil, json, pathlib, urllib.request, subprocess, hashlib
print(json.dumps({'system':platform.platform(), 'python':sys.version.split()[0], 'agy':shutil.which('agy'), 'gpu':shutil.which('nvidia-smi')}))
root = pathlib.Path('/content/agy-worker')
root.mkdir(exist_ok=True)
installer = urllib.request.urlopen('https://antigravity.google/cli/install.sh',timeout=30).read()
(root/'install.sh').write_bytes(installer)
print('installer_sha256', hashlib.sha256(installer).hexdigest())
result = subprocess.run(['bash',str(root/'install.sh')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
print('install_exit',result.returncode)
print(result.stdout[-4000:])
print('installed', [(str(p),p.exists()) for p in [pathlib.Path.home()/'.local/bin/agy',pathlib.Path('/usr/local/bin/agy')]])
'''
    cid = _extract_cell_id(t.colab_call({'name':'add_code_cell','arguments':{'cellIndex':0,'language':'python','code':code}}))
    print('cell', cid, flush=True)
    print(_extract_outputs_text(t.colab_call({'name':'run_code_cell','arguments':{'cellId':cid}})), flush=True)


if __name__ == '__main__':
    main()
