"""Execute a task's Python experiment on real Colab T4 and export its files.

Explicit invocation only. Reuses completed evidence; never silently substitutes
local execution or repeats an uncertain GPU run. Does not implement the task.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.cloud import CloudTools
from main import load_config, load_dotenv
from scripts.colab_timed_probe import unpack, text_outputs, utc


def observe_shutdown(root, record, tools):
    observations = []
    for _ in range(10):
        state = tools.colab_call({"name": "runtime_status", "arguments": {}})
        observations.append(state)
        if state.get("runtime_stopped") is True and state.get("active_session_count") == 0:
            break
        time.sleep(1)
    record["shutdown_observations"] = record.get("shutdown_observations", []) + observations
    record["shutdown_state"] = observations[-1]
    record["shutdown_verified"] = bool(observations[-1].get("runtime_stopped") is True
                                       and observations[-1].get("active_session_count") == 0)


def execute(root, script, timeout):
    tools = CloudTools(root, load_config())
    receipt = root / "artifacts/remote_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if receipt.exists():
        old = json.loads(receipt.read_text(encoding="utf-8"))
        if old.get("status") == "completed":
            print("Reused verified exported run " + old["run_id"])
            return
        raise RuntimeError("Prior cloud attempt exists; inspect it before allocating again")
    run_id = uuid.uuid4().hex
    record = {"run_id": run_id, "status": "starting", "started_at_utc": utc()}
    def save():
        receipt.write_text(json.dumps(record, indent=2), encoding="utf-8")
    save()
    files = [script, *sorted((root / "inputs").glob("*"))]
    staged = {p.relative_to(root).as_posix(): base64.b64encode(p.read_bytes()).decode() for p in files if p.is_file()}
    remote_dir = "/content/workflow-" + run_id
    sources = [
        "import os,json,subprocess,sys,time,base64,pathlib,zlib,hashlib\n"
        "gpu_name=subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],text=True).strip()\n"
        "assert 'T4' in gpu_name, 'Requested T4 unavailable: '+gpu_name\n"
        f"workflow_run_id={run_id!r}\nprint('WORKFLOW_GPU='+json.dumps({{'run_id':workflow_run_id,'gpu_name':gpu_name}}),flush=True)\n",
        f"work=pathlib.Path({remote_dir!r});work.mkdir(parents=True,exist_ok=True)\nos.chdir(work)\n"
        f"staged=json.loads({json.dumps(json.dumps(staged))})\n"
        "for name,data in staged.items():\n p=work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(data))\n"
        "os.environ['WORKFLOW_EXPERIMENT_RUN_ID']=workflow_run_id\n"
        "os.environ['CUBLAS_WORKSPACE_CONFIG']=':4096:8'\nprint('INPUTS_STAGED',flush=True)\n",
        "started=time.monotonic()\n"
        f"result=subprocess.run([sys.executable,{script.relative_to(root).as_posix()!r},'--spec','inputs/experiment_spec.json','--output','outputs'],text=True,capture_output=True,timeout={timeout})\n"
        "print(result.stdout,flush=True)\nprint(result.stderr,flush=True)\nassert result.returncode==0, 'Experiment exited '+str(result.returncode)\n"
        "bundle={}\n"
        "for p in sorted(pathlib.Path('outputs').rglob('*')):\n"
        " if p.is_file():\n"
        "  assert p.stat().st_size<2000000\n  bundle[p.relative_to('outputs').as_posix()]=base64.b64encode(p.read_bytes()).decode()\n"
        "packed=base64.b64encode(zlib.compress(json.dumps(bundle).encode())).decode()\n"
        "print('WORKFLOW_BUNDLE='+packed,flush=True)\n"
        "print('WORKFLOW_DONE='+json.dumps({'run_id':workflow_run_id,'gpu_name':gpu_name,'elapsed_seconds':time.monotonic()-started,'files':list(bundle)}),flush=True)\n",
    ]
    notebook = {"nbformat": 4, "nbformat_minor": 5,
        "metadata": {"workflow_run_id": run_id, "workflow_hardware_intent": "T4"},
        "cells": [{"cell_type": "code", "metadata": {}, "execution_count": None,
                   "outputs": [], "source": s.splitlines(True)} for s in sources]}
    nb_path = root / "artifacts/executed_experiment.ipynb"
    nb_path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    owned = False
    try:
        tools.colab_connect({})
        owned = True
        submission = tools.colab_call({"name": "run_notebook", "arguments": {
            "notebook_path": "artifacts/executed_experiment.ipynb", "hardware": "T4", "background_last_cell": True}})
        record["submission"] = submission
        assert submission.get("status") == "running_background", submission
        assert submission.get("background_cell", {}).get("status") == "clicked_run_button", submission
        record["status"] = "running"
        save()
        deadline = time.monotonic() + timeout + 150
        while time.monotonic() < deadline:
            cells = unpack(tools.colab_call({"name": "get_cells", "arguments": {"includeOutputs": True}})).get("cells", [])
            output = "\n".join(text_outputs(c) for c in cells)
            done = [s.split("WORKFLOW_DONE=", 1)[1] for s in output.splitlines() if s.startswith("WORKFLOW_DONE=")]
            if done:
                measurement = json.loads(done[-1])
                assert measurement["run_id"] == run_id and "T4" in measurement["gpu_name"]
                packed = [s.split("WORKFLOW_BUNDLE=", 1)[1] for s in output.splitlines() if s.startswith("WORKFLOW_BUNDLE=")][-1]
                bundle = json.loads(zlib.decompress(base64.b64decode(packed)))
                destination = root / "artifacts/experiment"
                destination.mkdir(exist_ok=True)
                record["export_sha256"] = {}
                for name, encoded in bundle.items():
                    path = (destination / name).resolve()
                    assert path.is_relative_to(destination.resolve()) and not path.exists()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(encoded))
                    record["export_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
                for i, cell in enumerate(cells[:len(notebook["cells"])]):
                    notebook["cells"][i].update(outputs=cell.get("outputs", []), execution_count=cell.get("execution_count", i + 1))
                nb_path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
                record.update(status="exported", measurement=measurement,
                              notebook_sha256=hashlib.sha256(nb_path.read_bytes()).hexdigest())
                save()
                break
            if any(o.get("output_type") == "error" for c in cells for o in c.get("outputs", [])):
                (root / "artifacts/failed_remote_cells.json").write_text(json.dumps(cells, indent=2), encoding="utf-8")
                raise RuntimeError("Remote cell failed; see failed_remote_cells.json")
            print(json.dumps({"state": "running", "time": utc()}), flush=True)
            time.sleep(5)
        else:
            raise TimeoutError("Remote experiment did not finish within its budget")
    except Exception as exc:
        record.update(status="error", error=str(exc))
        raise
    finally:
        try:
            if owned:
                record["shutdown"] = tools.colab_call({"name": "disconnect_runtime", "arguments": {"release_bridge": False}})
                observe_shutdown(root, record, tools)
                if record["status"] == "exported" and record["shutdown_verified"]:
                    record["status"] = "completed"
        finally:
            record["finished_at_utc"] = utc()
            save()
            if owned:
                tools.colab_release({})
    assert record["status"] == "completed", "Execution/export/shutdown must all be verified"
    print(json.dumps({k: record[k] for k in ("run_id", "status", "measurement", "shutdown_state")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--script", default="train.py")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    script = (root / args.script).resolve()
    assert script.is_relative_to(root) and script.is_file()
    assert 1 <= args.timeout <= 900
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "config/.env")
    execute(root, script, args.timeout)


if __name__ == "__main__":
    main()
