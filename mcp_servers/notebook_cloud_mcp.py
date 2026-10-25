"""Colab & Kaggle Cloud Notebook MCP Server (Stdio JSON-RPC 2.0).

Exposes MCP tools to Antigravity agents (interactive and ephemeral `Operator` / `Researcher`) for:
1. `notebook_inspect`              - Inspect `.ipynb` cells, outputs, and current hardware config
2. `notebook_modify_cell`          - Create `.ipynb` or insert/update/append/delete code & markdown cells
3. `notebook_select_hardware`      - Set GPU/TPU hardware (`T4`, `T4x2`, `P100`, `L4`, `A100`, `V100`, `TPU`, `CPU`)
                                     for both Google Colab metadata and Kaggle `kernel-metadata.json`
4. `kaggle_execute_notebook`       - Push & execute a notebook on Kaggle Cloud GPUs/TPUs via Kaggle API/CLI
5. `kaggle_export_data`            - Check Kaggle kernel status and export output files/datasets/logs locally
6. `colab_execute_notebook`        - Execute notebook cells on a connected Colab/Jupyter backend URL (or local kernel)
                                     and export generated data artifacts
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Hardware mapping tables for Colab (.ipynb metadata) and Kaggle (kernel-metadata.json)
COLAB_HARDWARE_MAP: Dict[str, Dict[str, Any]] = {
    "CPU": {"accelerator": None, "gpuType": None},
    "T4": {"accelerator": "GPU", "gpuType": "T4"},
    "T4X2": {"accelerator": "GPU", "gpuType": "T4"},
    "P100": {"accelerator": "GPU", "gpuType": "P100"},
    "V100": {"accelerator": "GPU", "gpuType": "V100"},
    "L4": {"accelerator": "GPU", "gpuType": "L4"},
    "A100": {"accelerator": "GPU", "gpuType": "A100"},
    "TPU": {"accelerator": "TPU", "gpuType": "TPU_V2"},
}

KAGGLE_HARDWARE_MAP: Dict[str, Dict[str, Any]] = {
    "CPU": {"enable_gpu": False, "enable_tpu": False, "accelerator": "none"},
    "T4": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaTeslaT4"},
    "T4X2": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaTeslaT4x2"},
    "P100": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaTeslaP100"},
    "V100": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaTeslaP100"},
    "L4": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaL4"},
    "A100": {"enable_gpu": True, "enable_tpu": False, "accelerator": "nvidiaAmpereA100"},
    "TPU": {"enable_gpu": False, "enable_tpu": True, "accelerator": "tpu1VmV38"},
}


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p.resolve()


def _default_notebook_structure() -> Dict[str, Any]:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {
                "provenance": [],
                "name": "antigravity_notebook.ipynb",
            },
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
            },
        },
        "cells": [],
    }


def _load_or_create_notebook(nb_path: Path) -> Dict[str, Any]:
    if nb_path.exists():
        return json.loads(nb_path.read_text(encoding="utf-8"))
    nb = _default_notebook_structure()
    nb["metadata"]["colab"]["name"] = nb_path.name
    return nb


def _save_notebook(nb_path: Path, nb: Dict[str, Any]) -> None:
    nb_path.parent.mkdir(parents=True, exist_ok=True)
    nb_path.write_text(json.dumps(nb, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _source_to_lines(source_text: str) -> List[str]:
    lines = source_text.splitlines(keepends=True)
    return lines if lines else [""]


def _lines_to_source(source_field: Any) -> str:
    if isinstance(source_field, list):
        return "".join(str(x) for x in source_field)
    return str(source_field or "")


# ============================================================================
# Tool Implementations
# ============================================================================


def tool_notebook_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
    nb_path = _resolve_path(args["notebook_path"])
    if not nb_path.exists():
        return {"error": f"Notebook not found at {nb_path}"}

    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    meta = nb.get("metadata", {})
    colab_meta = meta.get("colab", {})
    cells_summary = []

    for idx, cell in enumerate(nb.get("cells", [])):
        ctype = cell.get("cell_type", "code")
        src = _lines_to_source(cell.get("source", []))
        outputs = cell.get("outputs", [])
        out_texts = []
        for out in outputs:
            if "text" in out:
                out_texts.append(_lines_to_source(out["text"]))
            elif "data" in out and "text/plain" in out["data"]:
                out_texts.append(_lines_to_source(out["data"]["text/plain"]))
            elif "ename" in out:
                out_texts.append(f"{out.get('ename')}: {out.get('evalue')}")
        cells_summary.append(
            {
                "index": idx,
                "cell_type": ctype,
                "source": src,
                "execution_count": cell.get("execution_count"),
                "outputs_preview": "\n".join(out_texts)[:1000] if out_texts else "",
            }
        )

    kernel_meta_path = nb_path.parent / "kernel-metadata.json"
    kaggle_meta = None
    if kernel_meta_path.exists():
        try:
            kaggle_meta = json.loads(kernel_meta_path.read_text(encoding="utf-8"))
        except Exception:
            kaggle_meta = None

    return {
        "notebook_path": str(nb_path),
        "cell_count": len(cells_summary),
        "colab_hardware": {
            "accelerator": meta.get("accelerator"),
            "gpuType": colab_meta.get("gpuType"),
            "machine_shape": colab_meta.get("machine_shape"),
        },
        "kaggle_metadata": kaggle_meta,
        "cells": cells_summary,
    }


def tool_notebook_modify_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    nb_path = _resolve_path(args["notebook_path"])
    action = str(args.get("action", "append")).lower()
    cell_type = str(args.get("cell_type", "code")).lower()
    source = str(args.get("source", ""))
    cell_index = args.get("cell_index")

    nb = _load_or_create_notebook(nb_path)
    cells: List[Dict[str, Any]] = nb.setdefault("cells", [])

    new_cell: Dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": _source_to_lines(source),
    }
    if cell_type == "code":
        new_cell["execution_count"] = None
        new_cell["outputs"] = []

    if action == "append":
        cells.append(new_cell)
        affected_idx = len(cells) - 1
    elif action == "insert":
        idx = int(cell_index) if cell_index is not None else len(cells)
        idx = max(0, min(idx, len(cells)))
        cells.insert(idx, new_cell)
        affected_idx = idx
    elif action == "replace":
        if cell_index is None or not (0 <= int(cell_index) < len(cells)):
            return {"error": f"Invalid cell_index={cell_index} for replace (total cells: {len(cells)})"}
        idx = int(cell_index)
        cells[idx] = new_cell
        affected_idx = idx
    elif action == "delete":
        if cell_index is None or not (0 <= int(cell_index) < len(cells)):
            return {"error": f"Invalid cell_index={cell_index} for delete (total cells: {len(cells)})"}
        idx = int(cell_index)
        cells.pop(idx)
        affected_idx = idx
    else:
        return {"error": f"Unsupported action '{action}'. Use 'append', 'insert', 'replace', or 'delete'."}

    _save_notebook(nb_path, nb)
    return {
        "status": "ok",
        "action": action,
        "affected_cell_index": affected_idx,
        "total_cells": len(cells),
        "notebook_path": str(nb_path),
    }


def tool_notebook_select_hardware(args: Dict[str, Any]) -> Dict[str, Any]:
    nb_path = _resolve_path(args["notebook_path"])
    hardware = str(args.get("hardware", "T4")).upper().strip()
    platform = str(args.get("platform", "both")).lower().strip()
    high_ram = bool(args.get("high_ram", False))
    enable_internet = bool(args.get("enable_internet", True))
    kaggle_kernel_slug = args.get("kaggle_kernel_slug")
    dataset_sources = args.get("dataset_sources", [])
    competition_sources = args.get("competition_sources", [])

    if hardware not in COLAB_HARDWARE_MAP:
        return {
            "error": (
                f"Unsupported hardware '{hardware}'. "
                f"Supported options: {', '.join(COLAB_HARDWARE_MAP.keys())}"
            )
        }

    nb = _load_or_create_notebook(nb_path)
    meta = nb.setdefault("metadata", {})
    colab_meta = meta.setdefault("colab", {"provenance": [], "name": nb_path.name})

    result_summary: Dict[str, Any] = {
        "notebook_path": str(nb_path),
        "selected_hardware": hardware,
    }

    # 1. Configure Google Colab hardware metadata inside `.ipynb`
    if platform in ("colab", "both"):
        c_cfg = COLAB_HARDWARE_MAP[hardware]
        if c_cfg["accelerator"]:
            meta["accelerator"] = c_cfg["accelerator"]
            colab_meta["gpuType"] = c_cfg["gpuType"]
        else:
            meta.pop("accelerator", None)
            colab_meta.pop("gpuType", None)

        if high_ram:
            colab_meta["machine_shape"] = "hm"
        else:
            colab_meta.pop("machine_shape", None)

        _save_notebook(nb_path, nb)
        result_summary["colab_metadata_updated"] = {
            "accelerator": meta.get("accelerator", "CPU"),
            "gpuType": colab_meta.get("gpuType"),
            "machine_shape": colab_meta.get("machine_shape", "standard"),
        }

    # 2. Configure Kaggle `kernel-metadata.json` alongside the notebook
    if platform in ("kaggle", "both"):
        k_cfg = KAGGLE_HARDWARE_MAP[hardware]
        username = os.environ.get("KAGGLE_USERNAME", "your-kaggle-username")
        stem_slug = nb_path.stem.lower().replace("_", "-").replace(" ", "-")
        slug = kaggle_kernel_slug or f"{username}/{stem_slug}"
        title = slug.split("/")[-1].replace("-", " ").title()

        kernel_meta_path = nb_path.parent / "kernel-metadata.json"
        existing_kmeta: Dict[str, Any] = {}
        if kernel_meta_path.exists():
            try:
                existing_kmeta = json.loads(kernel_meta_path.read_text(encoding="utf-8"))
            except Exception:
                existing_kmeta = {}

        kmeta = {
            "id": slug if not existing_kmeta.get("id") or kaggle_kernel_slug else existing_kmeta["id"],
            "title": existing_kmeta.get("title", title),
            "code_file": nb_path.name,
            "language": "python",
            "kernel_type": "notebook",
            "is_private": str(existing_kmeta.get("is_private", "true")).lower(),
            "enable_gpu": "true" if k_cfg["enable_gpu"] else "false",
            "enable_tpu": "true" if k_cfg["enable_tpu"] else "false",
            "enable_internet": "true" if enable_internet else "false",
            "Accelerator": k_cfg["accelerator"],
            "dataset_sources": dataset_sources or existing_kmeta.get("dataset_sources", []),
            "competition_sources": competition_sources or existing_kmeta.get("competition_sources", []),
            "kernel_sources": existing_kmeta.get("kernel_sources", []),
            "model_sources": existing_kmeta.get("model_sources", []),
        }
        kernel_meta_path.write_text(json.dumps(kmeta, indent=2) + "\n", encoding="utf-8")
        result_summary["kaggle_kernel_metadata_path"] = str(kernel_meta_path)
        result_summary["kaggle_config"] = kmeta

    return result_summary


def _get_kaggle_auth() -> Optional[Dict[str, str]]:
    """Resolve Kaggle credentials from environment or `~/.kaggle/kaggle.json`."""
    user = os.environ.get("KAGGLE_USERNAME", "").strip()
    key = os.environ.get("KAGGLE_KEY", "").strip()
    if user and key:
        return {"username": user, "key": key}

    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    if kaggle_json.exists():
        try:
            data = json.loads(kaggle_json.read_text(encoding="utf-8"))
            if data.get("username") and data.get("key"):
                return {"username": data["username"], "key": data["key"]}
        except Exception:
            pass
    return None


def tool_kaggle_execute_notebook(args: Dict[str, Any]) -> Dict[str, Any]:
    nb_path = _resolve_path(args["notebook_path"])
    if not nb_path.exists():
        return {"error": f"Notebook not found at {nb_path}"}

    hardware = args.get("hardware")
    if hardware:
        tool_notebook_select_hardware(
            {
                "notebook_path": str(nb_path),
                "hardware": hardware,
                "platform": "kaggle",
                "kaggle_kernel_slug": args.get("kaggle_kernel_slug"),
            }
        )

    kernel_meta_path = nb_path.parent / "kernel-metadata.json"
    if not kernel_meta_path.exists():
        tool_notebook_select_hardware(
            {"notebook_path": str(nb_path), "hardware": "T4", "platform": "kaggle"}
        )

    auth = _get_kaggle_auth()
    if not auth:
        return {
            "status": "missing_credentials",
            "message": (
                "Kaggle credentials not found. Set KAGGLE_USERNAME and KAGGLE_KEY in `.env` "
                "or place `kaggle.json` in `~/.kaggle/kaggle.json`. "
                f"`kernel-metadata.json` is staged and ready at `{kernel_meta_path}`."
            ),
            "kernel_metadata_path": str(kernel_meta_path),
        }

    # Prefer `kaggle` CLI if installed, otherwise use Kaggle HTTP v1 API directly
    kaggle_bin = shutil.which("kaggle")
    if kaggle_bin:
        env = dict(os.environ)
        env["KAGGLE_USERNAME"] = auth["username"]
        env["KAGGLE_KEY"] = auth["key"]
        proc = subprocess.run(
            [kaggle_bin, "kernels", "push", "-p", str(nb_path.parent)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        return {
            "status": "pushed" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    # Direct Kaggle REST API v1 `/api/v1/kernels/push` fallback (zero pip dependencies needed!)
    kmeta = json.loads(kernel_meta_path.read_text(encoding="utf-8"))
    nb_text = nb_path.read_text(encoding="utf-8")
    payload = {
        "id": kmeta.get("id"),
        "slug": kmeta.get("id"),
        "newTitle": kmeta.get("title"),
        "text": nb_text,
        "language": kmeta.get("language", "python"),
        "kernelType": kmeta.get("kernel_type", "notebook"),
        "isPrivate": str(kmeta.get("is_private", "true")).lower() == "true",
        "enableGpu": str(kmeta.get("enable_gpu", "false")).lower() == "true",
        "enableTpu": str(kmeta.get("enable_tpu", "false")).lower() == "true",
        "enableInternet": str(kmeta.get("enable_internet", "true")).lower() == "true",
        "datasetDataSources": kmeta.get("dataset_sources", []),
        "competitionDataSources": kmeta.get("competition_sources", []),
        "kernelDataSources": kmeta.get("kernel_sources", []),
    }
    basic_token = base64.b64encode(f"{auth['username']}:{auth['key']}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        "https://www.kaggle.com/api/v1/kernels/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {basic_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return {"status": "pushed", "kaggle_response": resp_data}
    except urllib.error.HTTPError as err:
        return {
            "status": "error",
            "http_code": err.code,
            "error": err.read().decode("utf-8", errors="replace"),
        }


def tool_kaggle_export_data(args: Dict[str, Any]) -> Dict[str, Any]:
    kernel_slug = str(args.get("kernel_slug", "")).strip()
    dataset_slug = str(args.get("dataset_slug", "")).strip()
    export_dir = _resolve_path(args.get("export_dir", "storage/exports"))
    export_dir.mkdir(parents=True, exist_ok=True)

    auth = _get_kaggle_auth()
    if not auth:
        return {
            "status": "missing_credentials",
            "message": "Set KAGGLE_USERNAME and KAGGLE_KEY in `.env` or `~/.kaggle/kaggle.json`.",
        }

    basic_token = base64.b64encode(f"{auth['username']}:{auth['key']}".encode("utf-8")).decode("ascii")
    kaggle_bin = shutil.which("kaggle")

    if kaggle_bin:
        env = dict(os.environ)
        env["KAGGLE_USERNAME"] = auth["username"]
        env["KAGGLE_KEY"] = auth["key"]
        if kernel_slug:
            status_proc = subprocess.run(
                [kaggle_bin, "kernels", "status", kernel_slug],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            out_proc = subprocess.run(
                [kaggle_bin, "kernels", "output", kernel_slug, "-p", str(export_dir)],
                capture_output=True,
                text=True,
                env=env,
                timeout=180,
            )
            files = [str(p.relative_to(WORKSPACE_ROOT)) for p in export_dir.rglob("*") if p.is_file()]
            return {
                "status": "ok" if out_proc.returncode == 0 else "error",
                "kernel_status": status_proc.stdout.strip(),
                "export_stdout": out_proc.stdout.strip(),
                "export_dir": str(export_dir),
                "exported_files": files,
            }
        if dataset_slug:
            ds_proc = subprocess.run(
                [kaggle_bin, "datasets", "download", "-d", dataset_slug, "-p", str(export_dir), "--unzip"],
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
            files = [str(p.relative_to(WORKSPACE_ROOT)) for p in export_dir.rglob("*") if p.is_file()]
            return {
                "status": "ok" if ds_proc.returncode == 0 else "error",
                "export_stdout": ds_proc.stdout.strip(),
                "export_dir": str(export_dir),
                "exported_files": files,
            }

    # Direct HTTP fallback for Kaggle Kernel Status & Output listing
    if kernel_slug and "/" in kernel_slug:
        owner, kname = kernel_slug.split("/", 1)
        status_url = (
            f"https://www.kaggle.com/api/v1/kernels/status?userName={owner}&kernelSlug={kname}"
        )
        output_url = (
            f"https://www.kaggle.com/api/v1/kernels/output?userName={owner}&kernelSlug={kname}"
        )
        headers = {"Authorization": f"Basic {basic_token}"}
        try:
            req_st = urllib.request.Request(status_url, headers=headers)
            with urllib.request.urlopen(req_st, timeout=30) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))

            req_out = urllib.request.Request(output_url, headers=headers)
            with urllib.request.urlopen(req_out, timeout=60) as resp:
                output_data = json.loads(resp.read().decode("utf-8"))

            downloaded: List[str] = []
            for file_entry in output_data.get("files", []):
                f_url = file_entry.get("url")
                f_name = file_entry.get("fileName")
                if f_url and f_name:
                    dest = export_dir / f_name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    urllib.request.urlretrieve(f_url, str(dest))
                    downloaded.append(str(dest))

            return {
                "status": "ok",
                "kernel_status": status_data,
                "export_dir": str(export_dir),
                "downloaded_files": downloaded,
            }
        except urllib.error.HTTPError as err:
            return {
                "status": "error",
                "http_code": err.code,
                "error": err.read().decode("utf-8", errors="replace"),
            }

    return {"error": "Provide either 'kernel_slug' ('owner/kernel-name') or 'dataset_slug' ('owner/dataset')."}


def tool_colab_execute_notebook(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a `.ipynb` notebook locally or via a Colab/Jupyter server URL and export outputs."""
    nb_path = _resolve_path(args["notebook_path"])
    if not nb_path.exists():
        return {"error": f"Notebook not found at {nb_path}"}

    export_dir = _resolve_path(args.get("export_dir", "storage/exports"))
    export_dir.mkdir(parents=True, exist_ok=True)
    runtime_url = str(args.get("colab_runtime_url") or os.environ.get("COLAB_RUNTIME_URL", "")).strip()

    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    code_cells = [
        (idx, _lines_to_source(cell.get("source", [])))
        for idx, cell in enumerate(nb.get("cells", []))
        if cell.get("cell_type") == "code"
    ]

    # Execute code cells sequentially in an isolated Python process with working dir = export_dir
    # (or against Jupyter nbconvert if available), capturing stdout/stderr back into the `.ipynb` cells!
    executed_count = 0
    cell_results = []
    combined_script_lines = [
        "import os, sys, json, traceback",
        f"os.chdir({repr(str(export_dir))})",
    ]
    for idx, src in code_cells:
        # Strip IPython magic lines (`!pip`, `%matplotlib`) or convert `!cmd` to subprocess
        cleaned_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("!"):
                cmd_str = stripped[1:].strip()
                indent = line[: len(line) - len(stripped)]
                cleaned_lines.append(
                    f"{indent}import subprocess as _sp; print(_sp.getoutput({repr(cmd_str)}))"
                )
            elif stripped.startswith("%"):
                continue
            else:
                cleaned_lines.append(line)

        cell_code = "\n".join(cleaned_lines)
        proc = subprocess.run(
            [sys.executable, "-c", cell_code],
            cwd=str(export_dir),
            capture_output=True,
            text=True,
            timeout=int(args.get("timeout_seconds", 120)),
        )
        executed_count += 1
        out_text = proc.stdout
        err_text = proc.stderr

        cell_outputs = []
        if out_text:
            cell_outputs.append(
                {
                    "output_type": "stream",
                    "name": "stdout",
                    "text": _source_to_lines(out_text),
                }
            )
        if proc.returncode != 0 and err_text:
            cell_outputs.append(
                {
                    "output_type": "error",
                    "ename": "ExecutionError",
                    "evalue": err_text.splitlines()[-1] if err_text.splitlines() else "Error",
                    "traceback": err_text.splitlines(),
                }
            )

        nb["cells"][idx]["execution_count"] = executed_count
        nb["cells"][idx]["outputs"] = cell_outputs
        cell_results.append(
            {
                "cell_index": idx,
                "exit_code": proc.returncode,
                "stdout": out_text[:1000],
                "stderr": err_text[:1000] if proc.returncode != 0 else "",
            }
        )

    _save_notebook(nb_path, nb)
    exported_files = [
        str(p.relative_to(WORKSPACE_ROOT)) for p in export_dir.rglob("*") if p.is_file()
    ]
    return {
        "status": "executed",
        "notebook_path": str(nb_path),
        "runtime_mode": runtime_url or "local_python_kernel (use `colab-mcp` for live browser Colab VM)",
        "cells_executed": executed_count,
        "cell_results": cell_results,
        "export_dir": str(export_dir),
        "exported_files": exported_files,
    }


# ============================================================================
# MCP JSON-RPC 2.0 Server Definition
# ============================================================================

TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "notebook_inspect",
        "description": "Inspect a Jupyter/Colab/Kaggle `.ipynb` notebook: cells, outputs, and hardware accelerator settings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the `.ipynb` file (relative to workspace or absolute).",
                }
            },
            "required": ["notebook_path"],
        },
    },
    {
        "name": "notebook_modify_cell",
        "description": "Create a `.ipynb` notebook or insert, replace, append, or delete a code/markdown cell.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the `.ipynb` file.",
                },
                "action": {
                    "type": "string",
                    "enum": ["append", "insert", "replace", "delete"],
                    "description": "Cell modification operation.",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown"],
                    "description": "Type of cell to insert/append/replace.",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "Zero-based cell index (required for replace/delete, optional for insert).",
                },
                "source": {
                    "type": "string",
                    "description": "Python code or Markdown content for the cell.",
                },
            },
            "required": ["notebook_path", "action"],
        },
    },
    {
        "name": "notebook_select_hardware",
        "description": "Configure hardware accelerator (CPU, T4, T4x2, P100, V100, L4, A100, TPU) on a notebook for Google Colab and/or Kaggle Kernels.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the `.ipynb` file.",
                },
                "hardware": {
                    "type": "string",
                    "enum": ["CPU", "T4", "T4x2", "P100", "V100", "L4", "A100", "TPU"],
                    "description": "Target GPU/TPU/CPU accelerator.",
                },
                "platform": {
                    "type": "string",
                    "enum": ["colab", "kaggle", "both"],
                    "description": "Which platform metadata to configure (default: both).",
                },
                "high_ram": {
                    "type": "boolean",
                    "description": "Enable High-RAM machine shape in Google Colab metadata.",
                },
                "enable_internet": {
                    "type": "boolean",
                    "description": "Enable internet access for Kaggle Kernel execution.",
                },
                "kaggle_kernel_slug": {
                    "type": "string",
                    "description": "Optional Kaggle kernel ID in format 'username/kernel-slug'.",
                },
                "dataset_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Kaggle dataset slugs to attach (e.g. ['owner/dataset']).",
                },
                "competition_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Kaggle competition slugs to attach.",
                },
            },
            "required": ["notebook_path", "hardware"],
        },
    },
    {
        "name": "kaggle_execute_notebook",
        "description": "Push and execute a `.ipynb` notebook on Kaggle Cloud with the selected GPU/TPU hardware.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the `.ipynb` file.",
                },
                "hardware": {
                    "type": "string",
                    "enum": ["CPU", "T4", "T4x2", "P100", "V100", "L4", "A100", "TPU"],
                    "description": "Optional hardware override before pushing to Kaggle.",
                },
                "kaggle_kernel_slug": {
                    "type": "string",
                    "description": "Optional Kaggle kernel slug ('username/kernel-slug').",
                },
            },
            "required": ["notebook_path"],
        },
    },
    {
        "name": "kaggle_export_data",
        "description": "Check Kaggle Kernel execution status and export/download generated output files, logs, or datasets to the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kernel_slug": {
                    "type": "string",
                    "description": "Kaggle kernel slug ('username/kernel-slug') to check status and download outputs from.",
                },
                "dataset_slug": {
                    "type": "string",
                    "description": "Optional Kaggle dataset slug ('owner/dataset-name') to download.",
                },
                "export_dir": {
                    "type": "string",
                    "description": "Target workspace directory for exported data (default: 'storage/exports').",
                },
            },
        },
    },
    {
        "name": "colab_execute_notebook",
        "description": "Execute `.ipynb` cells, update cell outputs in-place, and export generated data files into `storage/exports`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the `.ipynb` file.",
                },
                "export_dir": {
                    "type": "string",
                    "description": "Directory where exported data files (CSVs, plots, weights) are saved.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Per-cell execution timeout in seconds (default: 120).",
                },
            },
            "required": ["notebook_path"],
        },
    },
]

TOOL_HANDLERS = {
    "notebook_inspect": tool_notebook_inspect,
    "notebook_modify_cell": tool_notebook_modify_cell,
    "notebook_select_hardware": tool_notebook_select_hardware,
    "kaggle_execute_notebook": tool_kaggle_execute_notebook,
    "kaggle_export_data": tool_kaggle_export_data,
    "colab_execute_notebook": tool_colab_execute_notebook,
}


def handle_rpc_request(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications have no `id` and expect no response
    if req_id is None:
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "antigravity-colab-kaggle-mcp",
                    "version": "1.0.0",
                },
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS_SCHEMA},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }
        try:
            res = handler(tool_args)
            is_err = "error" in res
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2, ensure_ascii=False),
                        }
                    ],
                    "isError": is_err,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Tool execution error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_rpc_request(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
