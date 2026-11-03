"""Generate explicit, auditable hook/skill bundles for each isolated task."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def toml_value(value):
    """Serialize reviewed hook objects as CLI TOML overrides without a shell."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{key}={toml_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_value(item) for item in value) + "]"
    return json.dumps(value)


def hook_command(controller, workspace, provider, event):
    return subprocess.list2cmdline([sys.executable, str(Path(controller) / "scripts/agent_hooks.py"),
        "--provider", provider, "--event", event, "--workspace", str(workspace)])


def prepare_integrations(controller, staging, target):
    controller, staging, target = map(Path, (controller, staging, target))
    state = staging / ".workflow"
    state.mkdir(exist_ok=True)
    skill_root = staging / ".agents/skills"
    skill_root.mkdir(parents=True, exist_ok=True)
    skills = []
    source = controller / ".agents/skills"
    if source.is_dir():
        for folder in sorted(source.iterdir()):
            if not folder.is_dir() or folder.is_symlink() or not (folder / "SKILL.md").is_file():
                continue
            destination = skill_root / folder.name
            shutil.copytree(folder, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".git"))
            # Browser helpers originally assumed a controller checkout. In task copies,
            # artifacts use the task root while imports/config and browser tokens use
            # the controller. Originals remain untouched.
            for script in destination.rglob("*.py"):
                text = script.read_text(encoding="utf-8")
                if "ROOT = Path(__file__).resolve().parents[4]" in text:
                    text = "import os\n" + text
                    text = text.replace("ROOT = Path(__file__).resolve().parents[4]",
                        'ROOT = Path(os.environ.get("WORKFLOW_WORKSPACE_ROOT", str(Path(__file__).resolve().parents[4]))).resolve()\n'
                        'CONTROLLER = Path(os.environ.get("WORKFLOW_CONTROLLER_ROOT", str(ROOT))).resolve()')
                    text = text.replace("sys.path.insert(0, str(ROOT))", "sys.path.insert(0, str(CONTROLLER))")
                    text = text.replace('URL_FILE = ROOT / "storage/tools/colab_connect_url.txt"',
                                        'URL_FILE = CONTROLLER / "storage/tools/colab_connect_url.txt"')
                    text = text.replace('load_dotenv(ROOT / ".env")', 'load_dotenv(CONTROLLER / ".env")')
                    compile(text, str(script), "exec")
                    script.write_text(text, encoding="utf-8")
            instructions = destination / "SKILL.md"
            text = instructions.read_text(encoding="utf-8")
            text = text.replace(".conda\\python.exe", f'"{sys.executable}"').replace(".conda/python.exe", f'"{sys.executable}"')
            text += "\nTask execution: keep artifacts in the assigned workspace. Use the interpreter in AGENTS.md; the task has no copied .conda environment.\n"
            instructions.write_text(text, encoding="utf-8")
            skills.append(folder.name)
    artifacts = skill_root / "workflow-artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "SKILL.md").write_text(
        "---\nname: workflow-artifacts\ndescription: Verify a local research artifact and record its real hash and size.\n---\n\n"
        "Read the artifact and validate its actual contents before reporting success. "
        "Use this command with the workflow Python from AGENTS.md:\n\n"
        "`python .agents/skills/workflow-artifacts/verify.py PATH`\n\n"
        "It hashes the actual file, prints its size/SHA256, and records evidence under .workflow. "
        "This verifies artifact identity; it does not establish experimental correctness.\n", encoding="utf-8")
    (artifacts / "verify.py").write_text(
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "root=Path(__file__).resolve().parents[3]\np=(root/sys.argv[1]).resolve()\n"
        "if not p.is_relative_to(root) or not p.is_file(): raise SystemExit('Artifact must be an existing task file')\n"
        "record={'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}\n"
        "state=root/'.workflow';state.mkdir(exist_ok=True)\n"
        "with (state/'artifact-receipts.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(record)+'\\n')\n"
        "print(json.dumps(record))\n", encoding="utf-8")
    skills.append("workflow-artifacts")
    checkpoints = skill_root / 'workflow-checkpoints'
    checkpoints.mkdir(exist_ok=True)
    # Use the generator's installed source, including in minimal source snapshots.
    shutil.copy2(Path(__file__).with_name('checkpoints.py'),checkpoints/'checkpoints.py')
    (checkpoints/'SKILL.md').write_text(
        '---\nname: workflow-checkpoints\ndescription: Save and restore portable computation state across VM loss.\n---\n\n'
        'Use the stdlib checkpoints.py beside this skill on Colab/local/Kaggle. '
        'Application code must save model, optimizer/scheduler/scaler, RNG states, '
        'step and data cursor at a consistent safe boundary. Create CheckpointStore '
        'on an explicitly configured durable mounted directory (for example an '
        'already mounted private Drive directory), not /content or /kaggle/working. '
        'Call AutoCheckpoint(store, interval_seconds=300).save(source, files, metadata) '
        'at safe boundaries; force=True at milestones. metadata needs step and '
        'code_sha256/data_sha256/config_sha256. publish returns a verified manifest. '
        'Do not claim a durable backup if the destination is only ephemeral VM disk. '
        'For controller-side copies after downloading state, discover checkpoint_publish, '
        'checkpoint_list and checkpoint_restore cloud tools. These do not automatically '
        'download unsaved state from a dead VM. Restore to a fresh directory with expected '
        'compatibility hashes; then application code loads its own trusted state files. '
        'Never deserialize untrusted pickle/checkpoint content. If no durable destination '
        'is configured, report that limitation before a long run.\n',encoding='utf8')
    skills.append('workflow-checkpoints')

    def native_hooks(provider):
        return {"hooks": {event: [{"hooks": [{"type": "command",
            "command": hook_command(controller, target, provider, event), "timeout": 10}]}]
            for event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop")}}

    codex = staging / ".codex"
    codex.mkdir(exist_ok=True)
    # Codex receives one reviewed source through CLI overrides. Keeping the same
    # handlers next to project config can run them twice when discovery is active.
    (state / "codex-hooks.json").write_text(json.dumps(native_hooks("codex"), indent=2), encoding="utf-8")
    notebook = {"command": sys.executable, "args": [str(controller / "mcp_servers/notebook_cloud_mcp.py")],
        "env": {"WORKFLOW_WORKSPACE_ROOT": str(target), "PYTHONIOENCODING": "utf-8"}}
    # Project trust is set per invocation for this freshly generated workspace.
    (codex / "config.toml").write_text('[features]\nhooks = true\n\n[mcp_servers.notebook]\n'
        + "command = " + json.dumps(sys.executable) + "\nargs = " + json.dumps(notebook["args"]) + "\n"
        + "[mcp_servers.notebook.env]\nWORKFLOW_WORKSPACE_ROOT = " + json.dumps(str(target))
        + '\nPYTHONIOENCODING = "utf-8"\n', encoding="utf-8")
    claude = staging / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.json").write_text(json.dumps(native_hooks("claude"), indent=2), encoding="utf-8")
    shutil.copytree(skill_root, claude / "skills", dirs_exist_ok=True)
    (staging / ".mcp.json").write_text(json.dumps({"mcpServers": {"notebook": notebook}}, indent=2), encoding="utf-8")

    agy_hooks = {"workflow-lifecycle": {"enabled": True,
        "SessionStart": [{"type": "command", "command": hook_command(controller, target, "agy", "SessionStart"), "timeout": 10}],
        "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": hook_command(controller, target, "agy", "PreToolUse"), "timeout": 10}]}],
        "PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": hook_command(controller, target, "agy", "PostToolUse"), "timeout": 10}]}],
        "Stop": [{"type": "command", "command": hook_command(controller, target, "agy", "Stop"), "timeout": 10}]}}
    (staging / ".agents/hooks.json").write_text(json.dumps(agy_hooks, indent=2), encoding="utf-8")
    instructions = (f"\n\n## Managed workflow integration\nTask workspace: {target}\nController: {controller}\n"
        f"Workflow Python: {sys.executable}\nSkills: {', '.join(skills)}\n"
        "Read the relevant SKILL.md before using its commands. The local workflow-artifacts skill records real file evidence.\n"
        "Native hooks record receipts in .workflow/hook-events.jsonl. Do not modify generated hook/config files.\n"
        "Do not spawn extra agents or access cloud/messaging unless the assigned task calls for it.\n")
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = staging / name
        path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + instructions, encoding="utf-8")
    owned = [".workflow/codex-hooks.json", ".codex/config.toml", ".claude/settings.json", ".mcp.json", ".agents/hooks.json"]
    manifest = {"version": 1, "controller": str(controller), "workspace": str(target), "skills": skills,
        "hook_script_sha256": digest(controller / "scripts/agent_hooks.py") if (controller / "scripts/agent_hooks.py").exists() else None,
        "files": {name: digest(staging / name) for name in owned}, "absent_files": [".codex/hooks.json"]}
    (state / "integration.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_bundle(workspace, controller):
    workspace, controller = Path(workspace).resolve(), Path(controller).resolve()
    manifest = json.loads((workspace / ".workflow/integration.json").read_text(encoding="utf-8"))
    if Path(manifest["controller"]).resolve() != controller or Path(manifest["workspace"]).resolve() != workspace:
        raise ValueError("Integration manifest points to a different workspace/controller")
    if manifest["hook_script_sha256"] != digest(controller / "scripts/agent_hooks.py"):
        raise ValueError("Hook script changed since workspace preparation")
    for relative in manifest.get("absent_files", []):
        if (workspace / relative).exists():
            raise ValueError(f"Unexpected native hook source: {relative}")
    for relative, expected in manifest["files"].items():
        path = (workspace / relative).resolve()
        if not path.is_relative_to(workspace) or digest(path) != expected:
            raise ValueError(f"Generated integration file changed: {relative}")
    return manifest
