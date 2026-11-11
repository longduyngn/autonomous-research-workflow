"""Honest local notebook execution, Kaggle jobs and personal Colab browser tools."""
import base64
import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from core.processes import run_process
from core.tools import AgentTools
from core.executables import find_executable

ACCELERATORS = {"CPU": None, "T4": "NvidiaTeslaT4", "T4X2": "NvidiaTeslaT4",
                "L4": "NvidiaL4", "A100": "NvidiaTeslaA100", "TPU": "TpuV5E8"}


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _extract_cell_id(res: dict):
    if not isinstance(res, dict):
        return None
    sc = res.get("structuredContent") or {}
    if sc.get("newCellId"):
        return sc["newCellId"]
    if sc.get("cellId"):
        return sc["cellId"]
    if res.get("content"):
        txt = res["content"][0].get("text", "")
        try:
            d = json.loads(txt)
            return d.get("newCellId") or d.get("cellId") or d.get("id")
        except Exception:
            return txt.strip().strip('"')
    return None


def _extract_outputs_text(res: dict) -> str:
    sc = (res or {}).get("structuredContent") or {}
    outputs = sc.get("outputs", [])
    lines = []
    for out in outputs:
        txt = out.get("text", [])
        if isinstance(txt, list):
            lines.append("".join(txt))
        elif isinstance(txt, str):
            lines.append(txt)
    if not lines and res and res.get("content"):
        lines.append(res["content"][0].get("text", ""))
    return "".join(lines).strip()


def kaggle_log_error_tail(directory, max_lines=12):
    """Last exception lines from a downloaded Kaggle kernel log (JSON list of {stream_name, data})."""
    lines = []
    for log in sorted(Path(directory).glob("*.log")):
        try:
            entries = json.loads(log.read_text(encoding="utf-8", errors="replace"))
            text = "".join(e.get("data", "") for e in entries if isinstance(e, dict))
        except (ValueError, OSError):
            text = log.read_text(encoding="utf-8", errors="replace")
        lines += [l.rstrip() for l in text.splitlines()
                  if re.search(r"(Error|Exception)\b.*:|^Traceback|^\s*-+>", l)]
    return lines[-max_lines:]


class CloudTools:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        self.controller = Path(os.environ.get("WORKFLOW_CONTROLLER_ROOT", str(Path(__file__).resolve().parent.parent))).resolve()
        self.config = (config or {}).get("cloud", {})
        self.workflow_config = config or {}
        self.paths = AgentTools(self.root, "operator")

    def path(self, value):
        return self.paths.path(value)

    def _browser_skill(self):
        relative = ".agents/skills/colab-coccoc-playwright/scripts/colab_playwright.py"
        local = self.root / relative
        return local if local.is_file() else self.controller / relative

    def _load_env_if_needed(self):
        if os.environ.get("WORKFLOW_NO_ENV") == "1":
            return
        if "unittest" in sys.modules and self.root != self.controller:
            env_files = [self.root / ".env", self.root / "config/.env"]
        else:
            env_files = [self.controller / ".env", self.controller / "config/.env"]
        for env_file in env_files:
            if env_file.exists():
                for raw in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    def _ensure_colab_bridge(self, host: str, port: int):
        if _is_port_open(port, host):
            return
        if os.environ.get('WORKFLOW_ROLE'):
            raise RuntimeError('Colab bridge must be prepared by the host controller; use backend=colab in the research plan')
        py_exe = sys.executable
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        subprocess.Popen(
            [py_exe, str(self.controller / "main.py"), "colab-bridge", "--port", str(port)],
            cwd=str(self.controller),
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(15):
            if _is_port_open(port, host):
                return
            time.sleep(0.2)

    def prepare_colab_bridge(self):
        """Controller-side preparation; does not connect a browser or allocate compute."""
        self._load_env_if_needed()
        if not os.environ.get(self.config.get('colab_bridge_token_env', 'COLAB_BRIDGE_TOKEN')):
            raise ValueError('Set COLAB_BRIDGE_TOKEN before preparing the Colab backend')
        parsed = urllib.parse.urlparse(self.config.get('colab_bridge_url', 'http://127.0.0.1:8765'))
        if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.scheme != 'http':
            raise ValueError('Colab bridge must be loopback HTTP')
        self._ensure_colab_bridge(parsed.hostname, parsed.port or 8765)
        # Initialize the official MCP dependency in the host service before an
        # agent terminal can start a restricted child copy of that service.
        tools = self.colab_tools({})
        return {'status':'ready', 'tool_count':len(tools.get('tools', []))}

    def schemas(self):
        def schema(name, description, properties, required=()):
            return {"name": name, "description": description,
                "inputSchema": {"type": "object", "properties": properties, "required": list(required)}}
        string = {"type": "string"}
        notebook = {"notebook_path": string}
        return [
            schema('compute_capabilities','Inspect future compute adapters; disabled platforms cannot allocate machines',{}),
            schema('checkpoint_publish', 'Copy explicit downloaded state files to this task\'s controller-side durable store; source must already be saved consistently',
                   {'files':{'type':'array','items':string},'metadata':{'type':'object'}}, ['files','metadata']),
            schema('checkpoint_list', 'List verified durable checkpoints for this task; does not run code', {}),
            schema('checkpoint_restore', 'Restore a compatible checkpoint into a fresh task subdirectory',
                   {'checkpoint_id':string,'destination':string,'expected':{'type':'object'}},['checkpoint_id','destination','expected']),
            schema("notebook_inspect", "Inspect local notebook source, outputs and metadata", notebook, ["notebook_path"]),
            schema("notebook_modify_cell", "Create/edit a notebook cell", {**notebook, "action": {"enum": ["append", "insert", "replace", "delete"]}, "cell_index": {"type": "integer"}, "cell_type": {"enum": ["code", "markdown"]}, "source": string}, ["notebook_path", "action"]),
            schema("notebook_select_hardware", "Set notebook hardware intent; does not allocate a machine", {**notebook, "hardware": string, "platform": {"enum": ["colab", "kaggle", "both"]}, "kaggle_kernel_slug": string}, ["notebook_path", "hardware"]),
            schema("local_execute_notebook", "Run in one persistent local Jupyter kernel; returns error on failed cells", {**notebook, "export_dir": string, "timeout_seconds": {"type": "integer"}}, ["notebook_path"]),
            schema("colab_tools", "Discover official Colab browser tools; schemas change after connecting", {}),
            schema('colab_run_snippet', 'Run bounded code on an existing idle runtime; never allocate or switch hardware',
                   {'code': string, 'hardware': string, 'timeout_seconds': {'type': 'integer'}}, ['code']),
            schema("colab_connect", "Open official Colab browser connection (auto-confirms via Coc Coc Playwright if CDP port 9222 is open; reuses active session if already connected)", {"force": {"type": "boolean"}}),
            schema("colab_call", "Call a tool from colab_tools or high-level workflow action (set_runtime, disconnect_runtime, run_notebook, read_live_output)", {"name": string, "arguments": {"type": "object"}}, ["name"]),
            schema("colab_release", "Close this task's browser bridge transport AFTER shutting down unused remote compute; pass force=true to close shared session", {"force": {"type": "boolean"}}),
            schema("kaggle_execute_notebook", "Stage an isolated Kaggle kernel and submit a real cloud run; records job ID", {**notebook, "hardware": string, "kaggle_kernel_slug": string, "input_files": {"type": "array", "items": string}, "dataset_sources": {"type": "array", "items": string}, "competition_sources": {"type": "array", "items": string}, "timeout_seconds": {"type": "integer"}, 'total':{'type':'number'},'stall_seconds':{'type':'number'}, 'job_id':{'type':'string','description':'Job from experiment_job_register to bind (default: the single unbound Kaggle job of this task)'}, 'checkpoint_uri':{'type':'string','description':'Off-VM store, e.g. rclone:gdrive:workflow-runs/<task>/<run>; adds the RCLONE_GDRIVE_CONF secret bootstrap'}}, ["notebook_path"]),
            schema("kaggle_job_status", "Poll a submitted Kaggle run once; no blocking GPU wait", {"job_id": string}, ["job_id"]),
            schema("kaggle_export_data", "Download only the named light outputs (metrics/logs/reports/samples) of a completed Kaggle job; heavy checkpoints stay on the shared store", {"job_id": string, "files": {"type": "array", "items": string}, "file_pattern": string, "all": {"type": "boolean"}}, ["job_id"]),
            schema("kaggle_job_cancel", "Request cancellation of exactly the submitted Kaggle version; verify afterwards with kaggle_job_status", {"job_id": string, "reason": string}, ["job_id", "reason"]),
            schema('experiment_job_register', 'Register job identity BEFORE launch. Reusing request_key never resubmits. Use one job for sequential stages; completed counts must be monotonic.',
                   {'request_key':string,'backend':{'enum':['colab','kaggle','local']},'external_id':string,
                    'wandb_path':string,'total':{'type':'number'},'stall_seconds':{'type':'number'},
                    'budget_seconds':{'type':'number'}}, ['request_key','backend']),
            schema('experiment_job_status', 'Read tracked progress; no compute launch', {'job_id':string}, ['job_id']),
            schema('experiment_job_track', 'Bind the W&B entity/project/run_id returned by the job-side SDK to this registered job',
                   {'job_id':string,'wandb_path':string},['job_id','wandb_path']),
            schema('agent_checkin', 'Answer .workflow/checkin-request.json nonce. This is a self-report, not verified progress.',
                   {'nonce':string,'stage':string,'completed':{'type':'number'},
                    'blocking_on':{'enum':['none','model_download','tool','gpu','network','unknown']}},['nonce','stage','completed','blocking_on']),
            schema('experiment_job_progress', 'Report monotonic useful work, not heartbeat-only progress',
                   {'job_id':string,'completed':{'type':'number'},'total':{'type':'number'},'stage':string,
                    'state':{'enum':['RUNNING','COMPLETED','FAILED','UNKNOWN']},'metrics':{'type':'object'}},['job_id']),
            schema('colab_job_start', 'Start a registered Colab cell ONCE, then return immediately. Cell must log WORKFLOW_PROGRESS markers using workflow-progress helper.',
                   {'job_id':string},['job_id']),
            schema('experiment_job_artifacts', 'Record outputs after compute completion: explicitly downloaded files, or a fetched shared-store manifest (heavy files stay remote). Existing acceptance checks still decide validity.',
                   {'job_id':string,'files':{'type':'array','items':string},'manifest':string,'external_job_id':string},['job_id']),
            schema('experiment_job_resume_checkpoint', 'Reconcile a confirmed FAILED job using a verified controller checkpoint whose metadata.job_id matches. UNKNOWN jobs cannot be retried.',
                   {'job_id':string,'checkpoint_id':string},['job_id','checkpoint_id']),
        ]

    def call(self, name, args):
        if name == "colab_execute_notebook":
            return {"status": "error", "error": "Use colab_connect/colab_tools/colab_call for real Colab. For local execution use local_execute_notebook. Never label local execution as Colab."}
        if name not in {s["name"] for s in self.schemas()}:
            raise ValueError(f"Unknown cloud tool: {name}")
        return getattr(self, name)(args or {})

    def notebook_inspect(self, args):
        path = self.path(args["notebook_path"])
        return {"notebook_path": str(path), **json.loads(path.read_text(encoding="utf-8"))}

    def _progress_context(self):
        from core.control import check_worker_lease
        check_worker_lease(os.environ)
        import sqlite3
        from contextlib import closing
        from core.database import WorkspaceDatabase
        from core.monitoring import ProgressStore
        path=self.controller / self.workflow_config.get('database_path','storage/events.db')
        if not path.is_file(): raise PermissionError('No registered task database')
        # Check without migrating/touching an unrelated controller DB during
        # standalone cloud-tool use or isolated tests.
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row
            rows = c.execute('SELECT id,workspace_path FROM tasks WHERE workspace_path=?',(str(self.root),)).fetchall()
        if len(rows) != 1:
            raise PermissionError('Progress tools require a registered task workspace')
        return ProgressStore(WorkspaceDatabase(path)), rows[0]['id']

    def experiment_job_register(self,args):
        store,task_id = self._progress_context()
        job,new = store.register(task_id,args['request_key'],args['backend'],args.get('external_id',''),
            args.get('wandb_path',''),args.get('total',0),args.get('stall_seconds',900),args.get('budget_seconds',0))
        return {'job':job,'newly_registered':new,'submission_performed':False,
                'tracking_defaults':{k:self.workflow_config.get('monitoring',{}).get(k,default)
                                     for k,default in [('project','antigravity-research'),('entity',None)]}}

    def experiment_job_status(self,args):
        store,task_id = self._progress_context()
        return store.get(args['job_id'],task_id)

    def experiment_job_track(self,args):
        store,task_id=self._progress_context()
        store.get(args['job_id'],task_id)
        return store.attach_wandb(args['job_id'],args['wandb_path'])

    def agent_checkin(self,args):
        request=json.loads((self.root/'.workflow/checkin-request.json').read_text(encoding='utf-8'))
        if args['nonce']!=request['nonce']: raise ValueError('Stale check-in request')
        from core.experiment_progress import numeric_metrics
        numeric_metrics({'completed':args['completed']})
        if not re.fullmatch(r'[a-zA-Z0-9 _./-]{1,80}',args['stage']): raise ValueError('Invalid stage')
        if args['blocking_on'] not in {'none','model_download','tool','gpu','network','unknown'}: raise ValueError('Invalid block reason')
        from core.role_memory import atomic_json
        record={k:args[k] for k in ('nonce','stage','completed','blocking_on')}
        record.update(observed_at=time.time(),source='agent_self_report_unverified')
        atomic_json(self.root/'.workflow/checkin-response.json',record)
        return {'status':'checkin_recorded_unverified'}

    def experiment_job_progress(self,args):
        store,task_id = self._progress_context()
        store.get(args['job_id'],task_id)
        return store.observe(args['job_id'],{k:v for k,v in args.items() if k!='job_id'})

    def colab_job_start(self,args):
        store,task_id = self._progress_context()
        job = store.get(args['job_id'],task_id)
        if job['backend']!='colab' or not job['external_id']:
            raise ValueError('Register the prepared Colab cell ID before launch')
        if job['budget_until'] and time.time()>job['budget_until']:
            raise ValueError('Job budget expired before launch; reconcile instead of starting compute')
        with store.db._connect() as c:
            # Commit uncertain submission BEFORE the browser effect. Crash -> reconcile,
            # never blindly click Run a second time.
            cur=c.execute("UPDATE experiment_jobs SET state='SUBMISSION_UNKNOWN' WHERE id=? AND state='REGISTERED'",(job['id'],))
        if cur.rowcount==0:
            return {'status':'not_resubmitted','job':store.get(job['id'])}
        result=self._colab('/browser',{'name':'trigger_cell_run','arguments':{'cellId':job['external_id']}},timeout=20)
        return {'status':'submitted_unverified','job_id':job['id'],'browser_result':result}

    def experiment_job_artifacts(self,args):
        store,task_id = self._progress_context()
        job=store.get(args['job_id'],task_id)
        if job['state']!='COMPLETED':
            raise ValueError('Compute completion must be observed before recording downloads')
        if job['total'] and job['completed'] < job['total']:
            raise ValueError('Job ended before its declared cohort was completed')
        if args.get('manifest'):
            # D7: heavy artifacts stay on the shared store; a verified manifest + artifact index is the evidence.
            from core.artifact_store import validate_manifest
            manifest=validate_manifest(json.loads(self.path(args['manifest']).read_text(encoding='utf-8')))
            if manifest.get('job_id') not in (job['id'], args.get('external_job_id')) or manifest['publication_state']!='PUBLISHED':
                raise ValueError('Manifest does not belong to this job or is not published')
            index_path=self.path(args['manifest']).with_name('artifact_index.json')
            index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'artifacts':[]}
            bad=[a['uri'] for a in index.get('artifacts',[]) if a.get('state')=='unvalidated']
            if bad or not index.get('artifacts'):
                raise ValueError('Artifacts are not verified yet: '+', '.join(bad[:5] or ['no artifact index']))
            records=[{'uri':a['uri'],'state':a['state'],'bytes':a.get('bytes'),'sha256':a.get('sha256')} for a in index['artifacts']]
            files=[]
        else:
            files=args['files']
            records=[]
        if not args.get('manifest') and (not files or len(files)>10000):
            raise ValueError('List downloaded output files explicitly')
        for name in files:
            path=self.path(name)
            if not path.is_file() or path.is_symlink() or not path.stat().st_size:
                raise ValueError('Missing/empty output file')
            records.append({'path':str(path.relative_to(self.root)),'bytes':path.stat().st_size,
                            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        from core.role_memory import atomic_json
        atomic_json(self.root/'.workflow'/('job-'+job['id']+'-artifacts.json'),{'job_id':job['id'],'files':records})
        with store.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE experiment_jobs SET state='ARTIFACTS_READY' WHERE id=? AND state='COMPLETED'",(job['id'],))
            c.execute("UPDATE tasks SET remote_job_id='' WHERE id=? AND remote_job_id=?",(task_id,job['id']))
        return {'status':'artifacts_downloaded_unvalidated','files':records}

    def experiment_job_resume_checkpoint(self,args):
        store,task_id=self._progress_context()
        job=store.get(args['job_id'],task_id)
        if job['state']!='FAILED': raise ValueError('Only a confirmed failed job can be reconciled for retry')
        manifest=self._checkpoint_store().verify(args['checkpoint_id'],{'job_id':job['id']})
        with store.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE experiment_jobs SET state='RECONCILED' WHERE id=? AND state='FAILED'",(job['id'],))
            c.execute("UPDATE tasks SET remote_job_id='' WHERE id=? AND remote_job_id=?",(task_id,job['id']))
        store.db.log_event('JOB_CHECKPOINT_RECONCILED','operator','Confirmed failed job reconciled with verified checkpoint',task_id=task_id,
                           payload={'job_id':job['id'],'checkpoint_id':manifest['id']})
        return {'status':'checkpoint_verified','checkpoint_id':manifest['id'],
                'next':'Restore the matching checkpoint, then explicitly register a new request key. No job was launched.'}

    def _checkpoint_store(self):
        from core.checkpoints import CheckpointStore
        base=self.controller/self.config.get('checkpoint_directory','storage/checkpoints')
        return CheckpointStore(base/hashlib.sha256(str(self.root).encode()).hexdigest())

    def checkpoint_publish(self,args):
        for name in args['files']:
            self.path(name)  # Reuse task/credential containment policy.
        result=self._checkpoint_store().publish(self.root,args['files'],args['metadata'])
        return {'status':'published','checkpoint':result,'storage':'controller-side; not the remote VM disk'}

    def checkpoint_list(self,args):
        return {'checkpoints':self._checkpoint_store().list()}

    def checkpoint_restore(self,args):
        expected=args.get('expected',{})
        if not all(field in expected for field in ('code_sha256','data_sha256','config_sha256')):
            raise ValueError('Restore requires code/data/config compatibility hashes')
        result=self._checkpoint_store().restore(args['checkpoint_id'],self.path(args['destination']),expected)
        return {'status':'restored','checkpoint':result}

    def notebook_modify_cell(self, args):
        path = self.path(args["notebook_path"])
        nb = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}, "cells": []}
        cells = nb["cells"]
        action, index = args["action"], args.get("cell_index", len(cells))
        cell = {"id": uuid.uuid4().hex[:8], "cell_type": args.get("cell_type", "code"), "metadata": {}, "source": args.get("source", "").splitlines(keepends=True)}
        if cell["cell_type"] == "code":
            cell.update(execution_count=None, outputs=[])
        if action in {"replace", "delete"} and not 0 <= index < len(cells):
            raise ValueError("Invalid cell index")
        if action == "append":
            cells.append(cell)
        elif action == "insert":
            cells.insert(index, cell)
        elif action == "replace":
            cells[index] = cell
        elif action == "delete":
            cells.pop(index)
        else:
            raise ValueError("Unknown cell operation")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(nb, indent=2), encoding="utf-8")
        return {"status": "ok", "notebook_path": str(path), "cell_count": len(cells)}

    def notebook_select_hardware(self, args):
        path = self.path(args["notebook_path"])
        hardware = args["hardware"].upper()
        if hardware not in self.config.get("kaggle_accelerators", ACCELERATORS) and hardware not in {"V100", "P100"}:
            raise ValueError("Unknown hardware; configure an actual platform-supported accelerator")
        nb = json.loads(path.read_text(encoding="utf-8"))
        meta = nb.setdefault("metadata", {})
        meta["workflow_hardware_intent"] = hardware
        meta.setdefault("colab", {})["gpuType"] = hardware if hardware != "CPU" else None
        if hardware == "CPU":
            meta.pop("accelerator", None)
        else:
            meta["accelerator"] = "TPU" if hardware == "TPU" else "GPU"
        path.write_text(json.dumps(nb, indent=2), encoding="utf-8")
        return {"status": "configured", "hardware": hardware, "allocated": False,
                "note": "Metadata is intent only. Allocate using Kaggle submission or connected Colab runtime tools."}

    def local_execute_notebook(self, args):
        from jupyter_client import KernelManager
        path = self.path(args["notebook_path"])
        export = self.path(args.get("export_dir", "artifacts"))
        export.mkdir(parents=True, exist_ok=True)
        nb = json.loads(path.read_text(encoding="utf-8"))
        timeout = max(1, min(3600, int(args.get("timeout_seconds", 120))))
        manager = KernelManager(kernel_name="python3")
        manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
        client, results, failed = None, [], False
        try:
            kernel_env = dict(os.environ)
            kernel_env.pop("PYTHONSTARTUP", None)
            kernel_env["IPYTHONDIR"] = str(self.path(".workflow/ipython"))
            kernel_env["JUPYTER_RUNTIME_DIR"] = str(self.path(".workflow/jupyter"))
            Path(kernel_env["IPYTHONDIR"]).mkdir(parents=True, exist_ok=True)
            Path(kernel_env["JUPYTER_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)
            manager.start_kernel(cwd=str(export), env=kernel_env,
                                 extra_arguments=["--HistoryManager.enabled=False"])
            client = manager.client()
            client.start_channels()
            client.wait_for_ready(timeout=30)
            for cell in nb["cells"]:
                if cell["cell_type"] == "code":
                    cell.update(outputs=[], execution_count=None)
            for index, cell in enumerate(nb["cells"]):
                if cell["cell_type"] != "code":
                    continue
                source = cell.get("source", "")
                source = "".join(source) if isinstance(source, list) else source
                msg_id = client.execute(source, stop_on_error=True)
                deadline = time.monotonic() + timeout
                while True:
                    msg = client.get_iopub_msg(timeout=max(0.01, deadline - time.monotonic()))
                    if msg.get("parent_header", {}).get("msg_id") != msg_id:
                        continue
                    kind, content = msg["msg_type"], msg["content"]
                    if kind == "execute_input":
                        cell["execution_count"] = content["execution_count"]
                    elif kind == "stream":
                        cell["outputs"].append({"output_type": "stream", "name": content["name"], "text": content["text"]})
                    elif kind in {"display_data", "execute_result"}:
                        output = {"output_type": kind, "data": content["data"], "metadata": content.get("metadata", {})}
                        if kind == "execute_result":
                            output["execution_count"] = content["execution_count"]
                        cell["outputs"].append(output)
                    elif kind == "error":
                        failed = True
                        cell["outputs"].append({"output_type": "error", **{k: content[k] for k in ["ename", "evalue", "traceback"]}})
                    elif kind == "status" and content["execution_state"] == "idle":
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Notebook cell timed out")
                results.append({"cell_index": index, "status": "error" if failed else "completed"})
                if failed:
                    break
        except (queue.Empty, TimeoutError) as exc:
            failed = True
            results.append({"status": "error", "error": str(exc) or "Notebook cell timed out"})
        finally:
            if client:
                client.stop_channels()
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)
            nb.setdefault("metadata", {})["workflow_execution"] = {"backend": "local", "status": "error" if failed else "completed"}
            path.write_text(json.dumps(nb, indent=2), encoding="utf-8")
        return {"status": "error" if failed else "completed", "runtime_mode": "local_jupyter_kernel",
                "cell_results": results, "export_dir": str(export),
                "exported_files": [str(p.relative_to(self.root)) for p in export.rglob("*") if p.is_file()]}

    def _colab(self, endpoint, data, timeout=310):
        from core.control import check_worker_lease
        check_worker_lease(os.environ)
        from core.role_memory import atomic_json
        token_file=self.root/'.workflow/colab-ownership.json'
        if endpoint not in {'/claim','/release'} and not token_file.is_file():
            self._colab('/claim',{})
        ownership=json.loads(token_file.read_text(encoding='utf8')) if token_file.is_file() else {}
        self._load_env_if_needed()
        env_key = self.config.get("colab_bridge_token_env", "COLAB_BRIDGE_TOKEN")
        token = os.environ.get(env_key, "")
        if not token:
            raise ValueError("Set COLAB_BRIDGE_TOKEN and start `python main.py colab-bridge`")
        url = self.config.get("colab_bridge_url", "http://127.0.0.1:8765")
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.scheme != "http":
            raise ValueError("Colab bridge must be loopback HTTP")
        if endpoint == "/release":
            if not _is_port_open(parsed.port or 8765, parsed.hostname):
                return {"status": "bridge_not_running", "runtime_stopped": False}
        else:
            self._ensure_colab_bridge(parsed.hostname, parsed.port or 8765)
        req = urllib.request.Request(url.rstrip("/") + endpoint, data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                     "X-Workflow-Session": hashlib.sha256(str(self.root).encode()).hexdigest(),
                     "X-Workflow-Generation":str(ownership.get('generation',-1))})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result=json.loads(response.read().decode())
                if endpoint=='/claim':
                    if result.get('protocol')!=2 or not isinstance(result.get('generation'),int):
                        raise RuntimeError('Colab bridge requires protocol 2; restart the host bridge after current work is reconciled')
                    token_file.parent.mkdir(parents=True,exist_ok=True)
                    atomic_json(token_file,result)
                elif endpoint=='/release' and result.get('runtime_stopped') is True:
                    token_file.unlink(missing_ok=True)
                return result
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code==409:
                from core.resource_gate import ResourceBusy
                raise ResourceBusy(detail) from exc
            raise RuntimeError(f"Colab bridge HTTP {exc.code}: {detail}") from exc

    def compute_capabilities(self, args):
        from core.compute import ComputeRegistry
        return {'platforms':ComputeRegistry(self.config.get('compute_extensions',{})).capabilities(),
                'allocation_supported':False}

    def colab_tools(self, args):
        return self._colab("/tools", {})

    def colab_run_snippet(self, args):
        # Reuse only explicit existing ownership. _colab must not claim a free VM
        # or launch a new runtime to satisfy this convenience action.
        ownership = self.path('.workflow/colab-ownership.json')
        if not ownership.is_file():
            raise PermissionError('Claim/reconcile existing Colab ownership before snippet execution')
        from core.database import WorkspaceDatabase
        database = self.controller / self.workflow_config.get('database_path', 'storage/events.db')
        if database.is_file():
            import sqlite3
            with sqlite3.connect('file:' + database.as_posix() + '?mode=ro', uri=True) as c:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'experiment_jobs' in tables:
                    if c.execute("SELECT 1 FROM experiment_jobs WHERE backend='colab' AND state NOT IN ('COMPLETED','FAILED') LIMIT 1").fetchone():
                        raise PermissionError('Registered Colab job is running or unresolved; snippet refused')
        return self._colab('/snippet', args, timeout=int(args.get('timeout_seconds', 300)) + 40)

    def colab_connect(self, args):
        args = args or {}
        if not args.get("force"):
            existing = [t["name"] for t in self.colab_tools({}).get("tools", [])]
            if "run_code_cell" in existing:
                return {"status": "reused_existing_session", "result": True, "tools": existing}

        url_file = self.controller / "storage/tools/colab_connect_url.txt"
        if url_file.exists():
            try:
                url_file.unlink()
            except OSError:
                pass

        # Pre-warm colab-mcp stdio server before starting the connection call
        self.colab_tools({})

        cdp_port = int(os.environ.get("COLAB_CDP_PORT", "9222"))
        confirm_res = {}
        confirm_thread = None
        if _is_port_open(cdp_port):
            try:
                skill_script = self._browser_skill()
                if skill_script.exists():
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("colab_playwright", str(skill_script))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    confirm_thread = threading.Thread(
                        target=lambda: confirm_res.setdefault("auto_confirm", mod.auto_confirm_mcp(timeout_sec=50, open_from_url_file=True)),
                        daemon=True,
                    )
                    confirm_thread.start()
            except Exception as exc:
                confirm_res["auto_confirm_error"] = str(exc)

        connect_out = self._colab("/call", {"name": "open_colab_browser_connection", "arguments": {}})
        if confirm_thread:
            confirm_thread.join(timeout=5)
        unlocked = [t["name"] for t in self.colab_tools({}).get("tools", [])]
        if "run_code_cell" not in unlocked:
            # The upstream 60s wait cancels its initialization task on timeout.
            # Reusing that client leaves future calls stuck until their timeout.
            self.colab_release({"force": True})
            raise RuntimeError("Colab browser handshake did not unlock execution; transport reset for a fresh connection")
        return {**connect_out, **confirm_res, "unlocked_tools": unlocked}

    def colab_call(self, args):
        name = args["name"]
        call_args = args.get("arguments", {}) or {}
        if name in {'trigger_cell_run','run_code_cell'} and call_args.get('cellId'):
            try:
                store,task_id=self._progress_context()
            except PermissionError:
                store=None
            if store:
                registered=next((j for j in store.jobs(task_id) if j['backend']=='colab' and j['external_id']==call_args['cellId']),None)
                if registered:
                    return self.colab_job_start({'job_id':registered['id']})

        # High-level workflow actions integrated directly into colab_call
        if name in {"set_runtime", "disconnect_runtime", "trigger_cell_run", "runtime_status"}:
            result=self._colab('/browser',{'name':name,'arguments':call_args})
            if name=='disconnect_runtime' and call_args.get('release_bridge',True):
                result['bridge_release']=self.colab_release({'force':True})
            return result

        if name == "read_live_output":
            idx = int(call_args.get("cell_index", -1))
            tail = int(call_args.get("tail_lines", 30))
            query = {"includeOutputs": True}
            if idx >= 0:
                query["cellIndexStart"] = idx
                query["cellIndexEnd"] = idx
            raw = self._colab("/call", {"name": "get_cells", "arguments": query})
            cells = ((raw or {}).get("structuredContent") or {}).get("cells", [])
            if not cells:
                return {"status": "empty", "output": ""}
            target = cells[-1] if idx < 0 else cells[0]
            out_chunks = []
            for o in target.get("outputs", []):
                t = o.get("text", [])
                out_chunks.append("".join(t) if isinstance(t, list) else str(t))
            full_text = "".join(out_chunks).strip()
            tail_text = "\n".join(full_text.splitlines()[-tail:]) if full_text else ""
            return {"status": "ok", "cellId": target.get("id"), "tail_output": tail_text, "line_count": len(full_text.splitlines())}

        if name == "run_notebook":
            nb_path = self.path(call_args["notebook_path"])
            nb_data = json.loads(nb_path.read_text(encoding="utf-8"))
            hw = call_args.get("hardware") or nb_data.get("metadata", {}).get("workflow_hardware_intent")
            bg_last = call_args.get("background_last_cell", False)

            conn = self.colab_connect({})
            if hw:
                selected = self.colab_call({"name": "set_runtime", "arguments": {"hardware": hw}})
                if selected.get("status") == "error" or selected.get("saved") is False:
                    raise RuntimeError("Requested Colab hardware could not be selected: " + json.dumps(selected))

            code_cells = []
            for c in nb_data.get("cells", []):
                if c.get("cell_type") == "code":
                    src = c.get("source", "")
                    src_str = "".join(src) if isinstance(src, list) else str(src)
                    src_str = src_str.replace("/kaggle/working/", "/content/")
                    if src_str.strip():
                        code_cells.append(src_str)

            existing = self._colab("/call", {"name": "get_cells", "arguments": {"includeOutputs": False}})
            existing_cells = ((existing or {}).get("structuredContent") or {}).get("cells", [])
            cell_ids = []
            for idx, src_str in enumerate(code_cells):
                if idx < len(existing_cells):
                    cid = existing_cells[idx]["id"]
                    self._colab("/call", {"name": "update_cell", "arguments": {"cellId": cid, "content": src_str}})
                else:
                    r = self._colab("/call", {"name": "add_code_cell", "arguments": {"cellIndex": idx, "language": "python", "code": src_str}})
                    cid = _extract_cell_id(r)
                cell_ids.append(cid)

            sync_end = len(cell_ids) - 1 if (bg_last and cell_ids) else len(cell_ids)
            outputs = []
            for idx in range(sync_end):
                cid = cell_ids[idx]
                r = self._colab("/call", {"name": "run_code_cell", "arguments": {"cellId": cid}})
                outputs.append({"cell_index": idx, "cellId": cid, "output": _extract_outputs_text(r), "isError": bool(r.get("isError"))})
                if r.get("isError"):
                    return {"status": "error", "failed_cell_index": idx, "outputs": outputs}

            bg_trigger = None
            if bg_last and cell_ids:
                bg_trigger = self.colab_call({"name": "trigger_cell_run", "arguments": {"cellId": cell_ids[-1], "cellIndex": len(cell_ids) - 1}})

            return {
                "status": "running_background" if bg_last else "completed",
                "connection": conn.get("status", "connected"),
                "hardware_requested": hw,
                "cell_ids": cell_ids,
                "sync_outputs": outputs,
                "background_cell": bg_trigger,
            }

        return self._colab("/call", {"name": name, "arguments": call_args})

    def colab_release(self, args):
        return self._colab("/release", dict(args or {}), timeout=40)

    def _kaggle(self, argv, timeout=120):
        from core.control import check_worker_lease
        check_worker_lease(os.environ)
        self._load_env_if_needed()
        exe = find_executable("kaggle")
        if not exe:
            raise ValueError("Install Kaggle CLI in the workflow Python environment: pip install kaggle")
        environment = dict(os.environ)
        if ("KAGGLE_CONFIG_DIR" not in environment
                and not (Path.home() / ".kaggle/kaggle.json").exists()
                and not (Path.home() / ".kaggle/access_token").exists()):
            directory = self.path(".workflow/kaggle-config")
            directory.mkdir(parents=True, exist_ok=True)
            environment["KAGGLE_CONFIG_DIR"] = str(directory)
        result = run_process([exe, "kernels", *argv], self.root, timeout=timeout, env=environment)
        if result["exit_code"]:
            raise RuntimeError(f"Kaggle command failed ({result['exit_code']}): {result['stderr'][-2000:]}")
        return result["stdout"]

    def _job_path(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job ID")
        return self.path(f".workflow/cloud_jobs/{job_id}/job.json")

    def kaggle_execute_notebook(self, args):
        self._load_env_if_needed()
        path = self.path(args["notebook_path"])
        nb = json.loads(path.read_text(encoding="utf-8"))
        if nb.get("nbformat") != 4 or not isinstance(nb.get("cells"), list):
            raise ValueError("Kaggle requires a version 4 Python notebook")
        metadata = nb.setdefault("metadata", {})
        kernel = metadata.get("kernelspec", {})
        if kernel.get("language", "python").lower() != "python":
            raise ValueError("Kaggle workflow notebooks must use a Python kernel")
        metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
        if args.get("checkpoint_uri") and not any(c.get("id") == "wfboot01" for c in nb["cells"]):
            from core.compute_backends import rclone_bootstrap_lines
            lines = ["import os\n", f"os.environ['WORKFLOW_CHECKPOINT_URI'] = {str(args['checkpoint_uri'])!r}\n"]
            lines += rclone_bootstrap_lines(args["checkpoint_uri"], self.workflow_config.get("storage", {}))
            nb["cells"].insert(0, {"cell_type": "code", "id": "wfboot01", "metadata": {}, "source": lines,
                                   "outputs": [], "execution_count": None})
        for cell in nb["cells"]:
            cell.setdefault("id", uuid.uuid4().hex[:8])
        hardware = args.get("hardware", "CPU").upper()
        accelerators = self.config.get("kaggle_accelerators", ACCELERATORS)
        if hardware not in accelerators:
            raise ValueError("Unsupported Kaggle accelerator; inspect CLI/account availability")
        slug = args.get("kaggle_kernel_slug")
        username = os.environ.get("KAGGLE_USERNAME", "")
        if not username and not slug:
            raise ValueError("Set KAGGLE_USERNAME or provide kaggle_kernel_slug; configure Kaggle authentication")
        fingerprint_data = {"notebook": hashlib.sha256(path.read_bytes()).hexdigest(),
            "hardware": hardware, "requested_slug": slug,
            "inputs": {name: hashlib.sha256(self.path(name).read_bytes()).hexdigest() for name in args.get("input_files", [])},
            "datasets": args.get("dataset_sources", []), "competitions": args.get("competition_sources", []),
            "timeout_seconds": args.get("timeout_seconds", 0)}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
        try: progress,task_id=self._progress_context()
        except PermissionError: progress,task_id=None,None
        def bind(job):
            if not progress: return job
            # One tracked job per run: reuse the job registered before launch (experiment_job_register or
            # JobController) instead of registering a second one, which the store rejects.
            target=args.get('job_id') or progress.unbound(task_id,'kaggle')
            if target:
                tracked=progress.bind_external(target,task_id,'kaggle',job['id'])
            else:
                tracked,_=progress.register(task_id,'kaggle-'+fingerprint[:40],'kaggle',external_id=job['id'],
                    total=args.get('total',0),stall_seconds=args.get('stall_seconds',900),
                    budget_seconds=args.get('timeout_seconds',0))
            job['tracking_id']=tracked['id']
            return job
        for previous in self.path(".workflow/cloud_jobs").glob("*/job.json"):
            saved = json.loads(previous.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == fingerprint and not args.get("force"):
                return {**bind(saved), "reused_submission": True}
        job_id = uuid.uuid4().hex
        slug = slug or f"{username}/workflow-{path.stem[:25].lower().replace('_','-')}-{job_id[:8]}"
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", slug):
            raise ValueError("Expected owner/kernel-slug")
        if not 5 <= len(slug.split("/", 1)[1]) <= 50:
            raise ValueError("Kaggle kernel slug must contain 5 to 50 characters")
        job_file = self._job_path(job_id)
        stage = job_file.parent / "stage"
        stage.mkdir(parents=True)
        inputs = {}
        for name in args.get("input_files", []):
            src = self.path(name)
            if src.stat().st_size > 20_000_000:
                raise ValueError("Embedded inputs are limited to 20MB each; use a Kaggle dataset for larger assets")
            rel = src.relative_to(self.root).as_posix()
            inputs[rel] = base64.b64encode(src.read_bytes()).decode()
        if inputs:
            bootstrap = ("import base64, pathlib, os\nos.chdir('/kaggle/working')\n"
                         f"_workflow_inputs = {inputs!r}\n"
                         "for _name, _data in _workflow_inputs.items():\n"
                         "    _p = pathlib.Path(_name); _p.parent.mkdir(parents=True, exist_ok=True); _p.write_bytes(base64.b64decode(_data))\n")
            nb["cells"].insert(0, {"cell_type": "code", "id": uuid.uuid4().hex[:8], "metadata": {}, "source": bootstrap.splitlines(keepends=True), "outputs": [], "execution_count": None})
        (stage / path.name).write_text(json.dumps(nb), encoding="utf-8")
        metadata = {"id": slug, "title": slug.split("/", 1)[1], "code_file": path.name,
            "language": "python", "kernel_type": "notebook", "is_private": True,
            "enable_gpu": hardware not in {"CPU", "TPU"}, "enable_tpu": hardware == "TPU",
            "enable_internet": True, "dataset_sources": args.get("dataset_sources", []),
            "competition_sources": args.get("competition_sources", []), "kernel_sources": []}
        (stage / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        job = {"id": job_id, "backend": "kaggle", "slug": slug, "hardware": hardware,
               "state": "submitting", "created_at": time.time(), "fingerprint": fingerprint}
        job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
        job=bind(job)
        if progress:
            # Register task ownership and an uncertain submission before push.
            progress.observe(job['tracking_id'],{'state':'SUBMISSION_UNKNOWN'})
            bootstrap=("import os\nos.environ['WORKFLOW_EXPERIMENT_JOB_ID'] = "+repr(job['tracking_id'])+
                       "\nos.environ['WORKFLOW_JOB_BUDGET_SECONDS'] = "+repr(str(args.get('timeout_seconds',0)))+'\n')
            nb['cells'].insert(0,{'cell_type':'code','id':uuid.uuid4().hex[:8],'metadata':{},
                                  'source':bootstrap.splitlines(keepends=True),'outputs':[],'execution_count':None})
            (stage/path.name).write_text(json.dumps(nb,indent=2),encoding='utf-8')
            job_file.write_text(json.dumps(job,indent=2),encoding='utf-8')
        argv = ["push", "-p", str(stage)]
        if args.get("timeout_seconds"):
            # Only a user-stated budget is passed; otherwise the platform limit applies (D6).
            argv += ["--timeout", str(max(1, int(args["timeout_seconds"])))]
        if hardware != "CPU" and accelerators[hardware]:
            argv += ["--accelerator", accelerators[hardware]]
        try:
            job["submission_output"] = self._kaggle(argv)
            m = re.search(r"kaggle\.com/code/([A-Za-z0-9_-]+/[A-Za-z0-9_-]+)", job["submission_output"])
            if m:
                job["slug"] = m.group(1)
            v = re.search(r"\bversion\s+(\d+)\b", job["submission_output"], re.I)
            if v:
                job["version"] = int(v.group(1))
            job["state"] = "submitted"
        except Exception as exc:
            job["state"] = "submission_unknown"
            job["error"] = str(exc)
        job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def kaggle_job_status(self, args):
        """Observation only (P2.5-05): never cancels, reconnects or resubmits."""
        if args.get("cancel"):
            raise ValueError("kaggle_job_status never cancels; use kaggle_job_cancel with a reason")
        path = self._job_path(args["job_id"])
        job = json.loads(path.read_text(encoding="utf-8"))
        output = self._kaggle(["status", job["slug"]], timeout=30)
        matches = re.findall(r"\b(complete|running|queued|error|cancelled|canceled)\b", output.lower())
        job["state"] = matches[-1] if matches else "unknown"
        reported = re.search(r"\bversion\s+(\d+)\b", output, re.I)
        if job.get("version") and reported and int(reported.group(1)) != job["version"]:
            # The slug now points at another version: this observation is not about our job.
            job["state"] = "unknown"
            job["version_mismatch"] = int(reported.group(1))
        job["status_output"] = output
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def kaggle_job_cancel(self, args):
        """Request cancellation of the recorded version only; the result is unverified until status confirms it."""
        if not args.get("reason"):
            raise ValueError("A cancellation reason is required")
        path = self._job_path(args["job_id"])
        job = json.loads(path.read_text(encoding="utf-8"))
        if not job.get("version"):
            return {"status": "refused", "reason": "Submitted version unknown; reconcile in the Kaggle UI first"}
        if not _is_port_open(9222):
            return {"status": "unavailable", "reason": "Browser CDP port 9222 is not open"}
        import importlib.util
        script = self.root / ".agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py"
        spec = importlib.util.spec_from_file_location("kaggle_cancel", str(script))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        receipt = mod.cancel_kaggle_kernel(job["slug"], version=job["version"])
        job.setdefault("cancel_requests", []).append({"at": time.time(), "reason": args["reason"][:200], "receipt": receipt})
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return receipt

    def kaggle_export_data(self, args):
        job = self.kaggle_job_status({"job_id": args["job_id"]})
        if job["state"] not in {"complete", "error", "cancelled", "canceled"}:
            return {"status": "not_ready", "job": job}
        export = self.path(f"artifacts/kaggle/{job['id']}")
        export.mkdir(parents=True, exist_ok=True)
        target = f"{job['slug']}/{job['version']}" if job.get("version") else job["slug"]
        argv = ["output", target, "-p", str(export)]
        if args.get("files"):
            argv += ["--file-pattern", "^(?:" + "|".join(re.escape(str(f)) for f in args["files"]) + ")$"]
        elif args.get("file_pattern"):
            argv += ["--file-pattern", str(args["file_pattern"])]
        elif not args.get("all"):
            # D7: never download everything by default; heavy checkpoints belong on the shared store.
            argv += ["--file-pattern", r"(?i)^[^/]*\.(?:json|jsonl|csv|txt|log|md|png|jpg|jpeg|svg)$"]
        output = self._kaggle(argv, timeout=300)
        result = {"status": "completed", "export_dir": str(export), "output": output, "job_state": job["state"],
                  "files": [str(p.relative_to(self.root)) for p in export.rglob("*") if p.is_file()]}
        if job["state"] != "complete":
            # A failed run's kernel log carries the traceback; surface its tail so agents can diagnose.
            result["error_tail"] = kaggle_log_error_tail(export)
        return result
