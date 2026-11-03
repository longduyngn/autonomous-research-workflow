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


class CloudTools:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        self.controller = Path(os.environ.get("WORKFLOW_CONTROLLER_ROOT", str(Path(__file__).resolve().parent.parent))).resolve()
        self.config = (config or {}).get("cloud", {})
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
            schema("colab_connect", "Open official Colab browser connection (auto-confirms via Coc Coc Playwright if CDP port 9222 is open; reuses active session if already connected)", {"force": {"type": "boolean"}}),
            schema("colab_call", "Call a tool from colab_tools or high-level workflow action (set_runtime, disconnect_runtime, run_notebook, read_live_output)", {"name": string, "arguments": {"type": "object"}}, ["name"]),
            schema("colab_release", "Close this task's browser bridge transport AFTER shutting down unused remote compute; pass force=true to close shared session", {"force": {"type": "boolean"}}),
            schema("kaggle_execute_notebook", "Stage an isolated Kaggle kernel and submit a real cloud run; records job ID", {**notebook, "hardware": string, "kaggle_kernel_slug": string, "input_files": {"type": "array", "items": string}, "dataset_sources": {"type": "array", "items": string}, "competition_sources": {"type": "array", "items": string}, "timeout_seconds": {"type": "integer"}}, ["notebook_path"]),
            schema("kaggle_job_status", "Poll a submitted Kaggle run once; no blocking GPU wait", {"job_id": string}, ["job_id"]),
            schema("kaggle_export_data", "Download outputs of a completed Kaggle job into its task workspace", {"job_id": string}, ["job_id"]),
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
            "timeout_seconds": args.get("timeout_seconds", 36000)}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
        for previous in self.path(".workflow/cloud_jobs").glob("*/job.json"):
            saved = json.loads(previous.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == fingerprint and not args.get("force"):
                return {**saved, "reused_submission": True}
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
        argv = ["push", "-p", str(stage), "--timeout", str(max(1, int(args.get("timeout_seconds", 36000))))]
        if hardware != "CPU" and accelerators[hardware]:
            argv += ["--accelerator", accelerators[hardware]]
        try:
            job["submission_output"] = self._kaggle(argv)
            m = re.search(r"kaggle\.com/code/([A-Za-z0-9_-]+/[A-Za-z0-9_-]+)", job["submission_output"])
            if m:
                job["slug"] = m.group(1)
            job["state"] = "submitted"
        except Exception as exc:
            job["state"] = "submission_unknown"
            job["error"] = str(exc)
        job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def kaggle_job_status(self, args):
        path = self._job_path(args["job_id"])
        job = json.loads(path.read_text(encoding="utf-8"))
        output = self._kaggle(["status", job["slug"]], timeout=30)
        matches = re.findall(r"\b(complete|running|queued|error|cancelled|canceled)\b", output.lower())
        job["state"] = matches[-1] if matches else "unknown"
        job["status_output"] = output
        if args.get("cancel") and job["state"] in {"running", "queued"} and _is_port_open(9222):
            try:
                cancel_script = self.root / ".agents/skills/kaggle-playwright-cancel/scripts/kaggle_cancel.py"
                if cancel_script.exists():
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("kaggle_cancel", str(cancel_script))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    job["playwright_cancel"] = mod.cancel_kaggle_kernel(job["slug"])
            except Exception as exc:
                job["playwright_cancel_error"] = str(exc)
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def kaggle_export_data(self, args):
        job = self.kaggle_job_status(args)
        if job["state"] != "complete":
            return {"status": "not_ready", "job": job}
        export = self.path(f"artifacts/kaggle/{job['id']}")
        export.mkdir(parents=True, exist_ok=True)
        output = self._kaggle(["output", job["slug"], "-p", str(export)], timeout=300)
        return {"status": "completed", "export_dir": str(export), "output": output,
                "files": [str(p.relative_to(self.root)) for p in export.rglob("*") if p.is_file()]}
