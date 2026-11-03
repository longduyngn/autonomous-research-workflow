"""Per-task snapshots include current source changes and exclude secrets/state."""
import json
import os
import shutil
import uuid
import sys
from pathlib import Path
from core.processes import run_process

EXCLUDED = {".git", ".conda", ".venv", "venv", "env", "node_modules", "storage",
            "__pycache__", ".pytest_cache", ".agents", ".codex", ".aws", ".vscode", ".idea"}
EXCLUDED.update({".kaggle", ".ssh", ".config", ".claude", ".workflow"})
EXCLUDED.update({"archive", "artifacts", "build", "dist", ".ruff_cache"})


class WorkspaceManager:
    def __init__(self, source, config):
        self.source = Path(source).resolve()
        self.config = config.get("isolation", {})
        self.base = (self.source / self.config.get("directory", "storage/workspaces")).resolve()

    def prepare(self, task):
        if task.workspace_path:
            path = Path(task.workspace_path).resolve()
            if not path.is_dir():
                raise ValueError(f"Task workspace is missing: {path}")
            return path
        if not self.config.get("enabled", True):
            return self.source
        target = self.base / f"task_{task.id:05d}"
        if target.exists():
            raise ValueError(f"Unregistered workspace already exists: {target}; inspect before resuming")
        staging = self.base / f".preparing_{task.id}_{uuid.uuid4().hex}"
        self.base.mkdir(parents=True, exist_ok=True)

        def ignore(directory, names):
            ignored = []
            for name in names:
                p = Path(directory) / name
                if (name in EXCLUDED or name.startswith('.env') or name.endswith('.env')
                        or name in {"kaggle.json", "credentials.json", "token.json", "access_token"}
                        or name.lower().endswith((".pem", ".key", ".p12", ".pfx"))
                        or p.is_symlink() or p.resolve() == self.base):
                    ignored.append(name)
            return ignored

        source = Path(task.source_path).resolve() if task.source_path else self.source
        if source == self.base or source.is_relative_to(self.base):
            raise ValueError("Use original source files, not a managed workspace, as --source")
        shutil.copytree(source, staging, ignore=ignore)
        for item in json.loads(task.inputs_json):
            import hashlib
            stored = Path(item["snapshot"])
            if hashlib.sha256(stored.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Stored task input changed after submission")
            destination = staging / "inputs" / item["name"]
            if destination.exists():
                raise ValueError(f"Source already contains inputs/{item['name']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stored, destination)
        # Install controller-owned tool endpoints; no secrets or user-global config.
        agents = staging / ".agents"
        (agents / "rules").mkdir(parents=True)
        (agents / "rules/AGENTS.md").write_text(
            "All work and artifacts belong in this task workspace. Follow the assigned role. "
            "Do not modify the workflow controller or read credentials. A PASS requires evidence.\n", encoding="utf-8")
        plugin = agents / "plugins/notebook"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text('{"name":"notebook"}', encoding="utf-8")
        (plugin / "mcp_config.json").write_text(json.dumps({"mcpServers": {"notebook": {
            "command": sys.executable, "args": [str(self.source / "mcp_servers/notebook_cloud_mcp.py")],
            "env": {"WORKFLOW_WORKSPACE_ROOT": str(target), "PYTHONIOENCODING": "utf-8"}}}}), encoding="utf-8")
        from core.integrations import prepare_integrations
        prepare_integrations(self.source, staging, target)
        # No git linkage to the controller. Agents can checkpoint their own files.
        if shutil.which("git"):
            for args in (["git", "init", "-q"], ["git", "add", "."],
                         ["git", "-c", "user.name=Workflow", "-c", "user.email=workflow@localhost",
                          "commit", "--allow-empty", "-qm", "Task input snapshot"]):
                result = run_process(args, staging, timeout=120)
                if result["exit_code"]:
                    raise RuntimeError(f"Workspace checkpoint failed: {result['stderr']}")
        (staging / "WORKFLOW_TASK.json").write_text(json.dumps({"task_id": task.id,
            "source": str(self.source), "snapshot": "current files excluding secrets/runtime state"}, indent=2), encoding="utf-8")
        staging.rename(target)
        return target
