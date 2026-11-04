"""Prepare private durable Colab checkpoint destination (D04) and run controlled live Colab loss/restore probe (D05).

Uses CPU runtime on the authorized personal Colab session to avoid consuming GPU compute units,
verifies durable checkpoint survival across runtime disconnect/loss, and verifies clean shutdown
with zero active sessions and no ownership overlap.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.checkpoints import CheckpointStore, artifact, sha256
from core.cloud import CloudTools
from core.resource_gate import ColabGate
from main import load_config, load_dotenv
from scripts.colab_timed_probe import text_outputs, unpack, utc


STATE_FILES = ["model.json", "optimizer.json", "rng_state.json", "data_cursor.json"]


def run_deterministic_steps(state_dir: Path, start_step: int, end_step: int, dataset: list[float], lr: float):
    state_dir.mkdir(parents=True, exist_ok=True)
    if start_step == 1:
        model = {"weights": [0.25, -0.5, 0.75, 0.1], "bias": 0.0}
        optimizer = {"momentum": [0.0, 0.0, 0.0, 0.0], "lr": lr, "step": 0}
        rng_state = {"seed": 20261104, "state_value": 135792468}
        cursor = {"index": 0, "completed_step": 0, "epoch": 1}
    else:
        model = json.loads((state_dir / "model.json").read_text(encoding="utf-8"))
        optimizer = json.loads((state_dir / "optimizer.json").read_text(encoding="utf-8"))
        rng_state = json.loads((state_dir / "rng_state.json").read_text(encoding="utf-8"))
        cursor = json.loads((state_dir / "data_cursor.json").read_text(encoding="utf-8"))
        assert cursor["completed_step"] == start_step - 1

    for step in range(start_step, end_step + 1):
        idx = cursor["index"] % len(dataset)
        x = dataset[idx]
        rng_state["state_value"] = (rng_state["state_value"] * 1103515245 + 12345) & 0x7FFFFFFF
        jitter = ((rng_state["state_value"] % 1000) / 1000000.0)
        for i in range(len(model["weights"])):
            grad = (model["weights"][i] * x - (i + 1) * 0.1) + jitter
            optimizer["momentum"][i] = round(0.9 * optimizer["momentum"][i] + grad, 10)
            model["weights"][i] = round(model["weights"][i] - lr * optimizer["momentum"][i], 10)
        model["bias"] = round(model["bias"] - lr * (x * 0.05 + jitter), 10)
        optimizer["step"] = step
        cursor["index"] = idx + 1
        cursor["completed_step"] = step

    (state_dir / "model.json").write_text(json.dumps(model, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    (state_dir / "optimizer.json").write_text(json.dumps(optimizer, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    (state_dir / "rng_state.json").write_text(json.dumps(rng_state, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    (state_dir / "data_cursor.json").write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    return {name: sha256(state_dir / name) for name in STATE_FILES}


def observe_shutdown(tools: CloudTools, attempts: int = 10):
    observations = []
    for _ in range(attempts):
        state = tools.colab_call({"name": "runtime_status", "arguments": {}})
        observations.append(state)
        if state.get("runtime_stopped") is True and state.get("active_session_count") == 0:
            break
        time.sleep(1)
    return observations


def prepare_d04(workspace: Path, config: dict) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    tools = CloudTools(workspace, config)
    store = tools._checkpoint_store()

    # Validate containment and credential rejection without exposing secrets
    rejected_paths = []
    for bad in [".env", ".ssh/id_rsa", ".kaggle/kaggle.json", "credentials.json", "token.json", "../escape.json"]:
        try:
            artifact(workspace, bad)
        except ValueError as exc:
            rejected_paths.append({"path": bad, "rejected": True, "reason": str(exc)})

    assert len(rejected_paths) == 6, "All credential and escaping paths must be rejected"

    # Reconcile ColabGate and inspect browser readiness without printing tokens
    from core.colab_bridge import browser_action
    gate = ColabGate(ROOT / config.get("cloud", {}).get("resource_directory", "storage/resource-control"),
                     lambda: browser_action("runtime_status", {}))
    reconciled = gate.reconcile()
    bridge_ready = tools.prepare_colab_bridge()

    receipt = {
        "task_id": "D04",
        "status": "live_verified",
        "verified_at_utc": utc(),
        "destination": {
            "kind": "controller_durable_checkpoint_store",
            "store_dir_relative": store.root.relative_to(ROOT).as_posix(),
            "workspace_relative": workspace.relative_to(ROOT).as_posix(),
            "ephemeral_vm_disk_excluded": True,
            "public_publication": False,
            "tokens_printed": False,
        },
        "containment_checks": rejected_paths,
        "bridge_preparation": bridge_ready,
        "resource_gate_reconciled": {
            "name": reconciled["name"],
            "state": reconciled["state"],
            "owner": reconciled["owner"],
            "generation": reconciled["generation"],
            "observation": json.loads(reconciled["observation"] or "{}"),
        },
    }
    d04_probe_dir = ROOT / "storage/ralph-next/probes/D04"
    d04_probe_dir.mkdir(parents=True, exist_ok=True)
    (d04_probe_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def run_d05(workspace: Path, config: dict) -> dict:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    dataset = [1.2, -0.4, 0.85, 2.1, -1.1, 0.65]
    lr = 0.05
    config_obj = {"lr": lr, "total_steps": 6, "checkpoint_step": 3, "seed": 20261104}
    code_bytes = Path(__file__).read_bytes()
    code_hash = hashlib.sha256(code_bytes).hexdigest()
    data_hash = hashlib.sha256(json.dumps(dataset).encode("utf-8")).hexdigest()
    config_hash = hashlib.sha256(json.dumps(config_obj, sort_keys=True).encode("utf-8")).hexdigest()
    expected_hashes = {
        "code_sha256": code_hash,
        "data_sha256": data_hash,
        "config_sha256": config_hash,
    }

    # Compute uninterrupted reference 1..6 for bit-for-bit comparison
    ref_dir = workspace / "reference_uninterrupted"
    ref_step3_dir = workspace / "reference_step3"
    ref_step3_digests = run_deterministic_steps(ref_step3_dir, 1, 3, dataset, lr)
    ref_step6_digests = run_deterministic_steps(ref_dir, 1, 6, dataset, lr)

    tools = CloudTools(workspace, config)
    run_id = uuid.uuid4().hex[:12]
    remote_dir_1 = f"/content/workflow-d05-vm1-{run_id}"
    remote_dir_2 = f"/content/workflow-d05-vm2-{run_id}"
    ckpt_helper_b64 = base64.b64encode((ROOT / "core/checkpoints.py").read_bytes()).decode("ascii")

    record = {
        "task_id": "D05",
        "run_id": run_id,
        "status": "starting",
        "started_at_utc": utc(),
        "expected_hashes": expected_hashes,
        "reference_step3_sha256": ref_step3_digests,
        "reference_step6_sha256": ref_step6_digests,
    }

    # --- PHASE 1: Connect Runtime #1, execute steps 1..3 on Colab, checkpoint & publish ---
    phase1_code = f"""
import base64, hashlib, json, os, pathlib, platform, time, uuid, zlib
work = pathlib.Path({remote_dir_1!r})
work.mkdir(parents=True, exist_ok=True)
(work / "checkpoints.py").write_bytes(base64.b64decode({ckpt_helper_b64!r}))
import sys
sys.path.insert(0, str(work))
from checkpoints import CheckpointStore, sha256

boot_id = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip() if pathlib.Path("/proc/sys/kernel/random/boot_id").exists() else "unknown"
ephemeral_sentinel = work / "ephemeral_only_marker.txt"
ephemeral_sentinel.write_text("ephemeral-vm1-" + {run_id!r}, encoding="utf-8")

dataset = {dataset!r}
lr = {lr!r}
model = {{"weights": [0.25, -0.5, 0.75, 0.1], "bias": 0.0}}
optimizer = {{"momentum": [0.0, 0.0, 0.0, 0.0], "lr": lr, "step": 0}}
rng_state = {{"seed": 20261104, "state_value": 135792468}}
cursor = {{"index": 0, "completed_step": 0, "epoch": 1}}

for step in range(1, 4):
    idx = cursor["index"] % len(dataset)
    x = dataset[idx]
    rng_state["state_value"] = (rng_state["state_value"] * 1103515245 + 12345) & 0x7FFFFFFF
    jitter = ((rng_state["state_value"] % 1000) / 1000000.0)
    for i in range(len(model["weights"])):
        grad = (model["weights"][i] * x - (i + 1) * 0.1) + jitter
        optimizer["momentum"][i] = round(0.9 * optimizer["momentum"][i] + grad, 10)
        model["weights"][i] = round(model["weights"][i] - lr * optimizer["momentum"][i], 10)
    model["bias"] = round(model["bias"] - lr * (x * 0.05 + jitter), 10)
    optimizer["step"] = step
    cursor["index"] = idx + 1
    cursor["completed_step"] = step

state_dir = work / "state_step3"
state_dir.mkdir(parents=True, exist_ok=True)
(state_dir / "model.json").write_text(json.dumps(model, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "optimizer.json").write_text(json.dumps(optimizer, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "rng_state.json").write_text(json.dumps(rng_state, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "data_cursor.json").write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8")

remote_store = CheckpointStore(work / "remote_ckpt_stage")
manifest = remote_store.publish(state_dir, {STATE_FILES!r}, {{
    "step": 3,
    "code_sha256": {code_hash!r},
    "data_sha256": {data_hash!r},
    "config_sha256": {config_hash!r},
}})

bundle = {{name: base64.b64encode((state_dir / name).read_bytes()).decode("ascii") for name in {STATE_FILES!r}}}
packed = base64.b64encode(zlib.compress(json.dumps(bundle).encode("utf-8"))).decode("ascii")
print("D05_PHASE1_BUNDLE=" + packed, flush=True)
print("D05_PHASE1_DONE=" + json.dumps({{
    "run_id": {run_id!r},
    "boot_id": boot_id,
    "platform": platform.platform(),
    "remote_checkpoint_id": manifest["id"],
    "step": 3,
    "ephemeral_sentinel_exists": ephemeral_sentinel.exists()
}}), flush=True)
""".strip()

    nb1_path = workspace / "notebooks/phase1_step3.ipynb"
    nb1_path.parent.mkdir(parents=True, exist_ok=True)
    nb1 = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"workflow_hardware_intent": "CPU", "workflow_run_id": run_id},
        "cells": [{"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": phase1_code.splitlines(True)}],
    }
    nb1_path.write_text(json.dumps(nb1, indent=2), encoding="utf-8")

    owned = False
    try:
        conn1 = tools.colab_connect({})
        owned = True
        ownership1 = json.loads((workspace / ".workflow/colab-ownership.json").read_text(encoding="utf-8"))
        run1 = tools.colab_call({
            "name": "run_notebook",
            "arguments": {"notebook_path": "notebooks/phase1_step3.ipynb", "hardware": "CPU", "background_last_cell": False},
        })
        assert run1.get("status") == "completed", f"Phase 1 notebook failed: {run1}"
        out1 = "\n".join(item.get("output", "") for item in run1.get("sync_outputs", []))
        done1_lines = [l.split("D05_PHASE1_DONE=", 1)[1] for l in out1.splitlines() if l.startswith("D05_PHASE1_DONE=")]
        bundle1_lines = [l.split("D05_PHASE1_BUNDLE=", 1)[1] for l in out1.splitlines() if l.startswith("D05_PHASE1_BUNDLE=")]
        assert done1_lines and bundle1_lines, f"Missing Phase 1 markers in output: {out1}"
        phase1_meta = json.loads(done1_lines[-1])
        bundle1 = json.loads(zlib.decompress(base64.b64decode(bundle1_lines[-1])))

        downloaded_step3 = workspace / "downloaded_step3"
        downloaded_step3.mkdir(parents=True, exist_ok=True)
        rel_files = []
        for name, b64data in bundle1.items():
            p = downloaded_step3 / name
            p.write_bytes(base64.b64decode(b64data))
            rel_files.append(p.relative_to(workspace).as_posix())

        # Publish to controller durable store via CloudTools.checkpoint_publish
        pub_res = tools.checkpoint_publish({
            "files": rel_files,
            "metadata": {
                "step": 3,
                "code_sha256": code_hash,
                "data_sha256": data_hash,
                "config_sha256": config_hash,
            },
        })
        ckpt_id = pub_res["checkpoint"]["id"]

        # Simulate ephemeral VM disk loss before disconnect + disconnect runtime cleanly
        wipe_cell = tools.colab_call({
            "name": "add_code_cell",
            "arguments": {
                "cellIndex": 1,
                "language": "python",
                "code": f"import shutil, pathlib\nshutil.rmtree({remote_dir_1!r}, ignore_errors=True)\nprint('WIPED_VM1_EPHEMERAL=' + str(not pathlib.Path({remote_dir_1!r}).exists()))\n",
            },
        })
        from core.cloud import _extract_cell_id, _extract_outputs_text
        wipe_cid = _extract_cell_id(wipe_cell)
        wipe_out = _extract_outputs_text(tools.colab_call({"name": "run_code_cell", "arguments": {"cellId": wipe_cid}}))

        disc1 = tools.colab_call({"name": "disconnect_runtime", "arguments": {"release_bridge": False}})
        obs1 = observe_shutdown(tools)
        rel1 = tools.colab_release({})
        owned = False

        record["phase1"] = {
            "connection": conn1.get("status", "connected"),
            "ownership_generation": ownership1.get("generation"),
            "remote_meta": phase1_meta,
            "published_checkpoint_id": ckpt_id,
            "published_manifest": pub_res["checkpoint"],
            "ephemeral_wipe_output": wipe_out,
            "disconnect": disc1,
            "shutdown_observation": obs1[-1],
            "bridge_release": rel1,
        }
        assert obs1[-1].get("runtime_stopped") is True and obs1[-1].get("active_session_count") == 0
        assert rel1.get("runtime_stopped") is True
    finally:
        if owned:
            try:
                tools.colab_call({"name": "disconnect_runtime", "arguments": {"release_bridge": False}})
                observe_shutdown(tools)
            finally:
                tools.colab_release({})

    # Verify durable checkpoint exists while runtime is completely stopped and unowned
    listed = tools.checkpoint_list({})["checkpoints"]
    assert any(c["id"] == ckpt_id for c in listed), "Published checkpoint missing from durable store"
    restore_res = tools.checkpoint_restore({
        "checkpoint_id": ckpt_id,
        "destination": "restored_from_durable",
        "expected": expected_hashes,
    })
    restored_root = workspace / "restored_from_durable/downloaded_step3"
    restored_step3_digests = {name: sha256(restored_root / name) for name in STATE_FILES}
    assert restored_step3_digests == ref_step3_digests, "Restored step 3 digests do not match reference step 3"

    # --- PHASE 2: Reconnect fresh runtime, stage restored state, run steps 4..6, verify bit-for-bit ---
    restored_staged = {name: base64.b64encode((restored_root / name).read_bytes()).decode("ascii") for name in STATE_FILES}
    phase2_code = f"""
import base64, json, os, pathlib, platform, zlib
old_work = pathlib.Path({remote_dir_1!r})
assert not old_work.exists(), "Old ephemeral workspace should not exist after VM loss"

work = pathlib.Path({remote_dir_2!r})
work.mkdir(parents=True, exist_ok=True)
state_dir = work / "state_resumed"
state_dir.mkdir(parents=True, exist_ok=True)
restored = json.loads({json.dumps(json.dumps(restored_staged))})
for name, b64data in restored.items():
    (state_dir / name).write_bytes(base64.b64decode(b64data))

dataset = {dataset!r}
lr = {lr!r}
model = json.loads((state_dir / "model.json").read_text(encoding="utf-8"))
optimizer = json.loads((state_dir / "optimizer.json").read_text(encoding="utf-8"))
rng_state = json.loads((state_dir / "rng_state.json").read_text(encoding="utf-8"))
cursor = json.loads((state_dir / "data_cursor.json").read_text(encoding="utf-8"))
assert cursor["completed_step"] == 3, f"Expected step 3, got {{cursor}}"

for step in range(4, 7):
    idx = cursor["index"] % len(dataset)
    x = dataset[idx]
    rng_state["state_value"] = (rng_state["state_value"] * 1103515245 + 12345) & 0x7FFFFFFF
    jitter = ((rng_state["state_value"] % 1000) / 1000000.0)
    for i in range(len(model["weights"])):
        grad = (model["weights"][i] * x - (i + 1) * 0.1) + jitter
        optimizer["momentum"][i] = round(0.9 * optimizer["momentum"][i] + grad, 10)
        model["weights"][i] = round(model["weights"][i] - lr * optimizer["momentum"][i], 10)
    model["bias"] = round(model["bias"] - lr * (x * 0.05 + jitter), 10)
    optimizer["step"] = step
    cursor["index"] = idx + 1
    cursor["completed_step"] = step

(state_dir / "model.json").write_text(json.dumps(model, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "optimizer.json").write_text(json.dumps(optimizer, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "rng_state.json").write_text(json.dumps(rng_state, indent=2, sort_keys=True), encoding="utf-8")
(state_dir / "data_cursor.json").write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8")

bundle = {{name: base64.b64encode((state_dir / name).read_bytes()).decode("ascii") for name in {STATE_FILES!r}}}
packed = base64.b64encode(zlib.compress(json.dumps(bundle).encode("utf-8"))).decode("ascii")
print("D05_PHASE2_BUNDLE=" + packed, flush=True)
print("D05_PHASE2_DONE=" + json.dumps({{
    "run_id": {run_id!r},
    "old_ephemeral_absent": not old_work.exists(),
    "resumed_from_step": 3,
    "completed_step": cursor["completed_step"],
    "platform": platform.platform(),
}}), flush=True)
""".strip()

    nb2_path = workspace / "notebooks/phase2_resume_step6.ipynb"
    nb2 = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"workflow_hardware_intent": "CPU", "workflow_run_id": run_id},
        "cells": [{"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": phase2_code.splitlines(True)}],
    }
    nb2_path.write_text(json.dumps(nb2, indent=2), encoding="utf-8")

    owned = False
    try:
        conn2 = tools.colab_connect({})
        owned = True
        ownership2 = json.loads((workspace / ".workflow/colab-ownership.json").read_text(encoding="utf-8"))
        assert ownership2["generation"] > ownership1["generation"], "Second runtime session must use a strictly newer fencing generation"

        run2 = tools.colab_call({
            "name": "run_notebook",
            "arguments": {"notebook_path": "notebooks/phase2_resume_step6.ipynb", "hardware": "CPU", "background_last_cell": False},
        })
        assert run2.get("status") == "completed", f"Phase 2 notebook failed: {run2}"
        out2 = "\n".join(item.get("output", "") for item in run2.get("sync_outputs", []))
        done2_lines = [l.split("D05_PHASE2_DONE=", 1)[1] for l in out2.splitlines() if l.startswith("D05_PHASE2_DONE=")]
        bundle2_lines = [l.split("D05_PHASE2_BUNDLE=", 1)[1] for l in out2.splitlines() if l.startswith("D05_PHASE2_BUNDLE=")]
        assert done2_lines and bundle2_lines, f"Missing Phase 2 markers in output: {out2}"
        phase2_meta = json.loads(done2_lines[-1])
        bundle2 = json.loads(zlib.decompress(base64.b64decode(bundle2_lines[-1])))

        final_step6_dir = workspace / "final_resumed_step6"
        final_step6_dir.mkdir(parents=True, exist_ok=True)
        for name, b64data in bundle2.items():
            (final_step6_dir / name).write_bytes(base64.b64decode(b64data))

        resumed_step6_digests = {name: sha256(final_step6_dir / name) for name in STATE_FILES}
        assert resumed_step6_digests == ref_step6_digests, (
            f"Resumed step 6 digests {resumed_step6_digests} != uninterrupted reference {ref_step6_digests}"
        )

        disc2 = tools.colab_call({"name": "disconnect_runtime", "arguments": {"release_bridge": False}})
        obs2 = observe_shutdown(tools)
        rel2 = tools.colab_release({})
        owned = False

        record["phase2"] = {
            "connection": conn2.get("status", "connected"),
            "ownership_generation": ownership2.get("generation"),
            "no_generation_overlap": ownership2["generation"] > ownership1["generation"],
            "remote_meta": phase2_meta,
            "restored_checkpoint_id": restore_res["checkpoint"]["id"],
            "resumed_step6_sha256": resumed_step6_digests,
            "matches_uninterrupted_reference": resumed_step6_digests == ref_step6_digests,
            "disconnect": disc2,
            "shutdown_observation": obs2[-1],
            "bridge_release": rel2,
        }
        assert obs2[-1].get("runtime_stopped") is True and obs2[-1].get("active_session_count") == 0
        assert rel2.get("runtime_stopped") is True
        record["status"] = "live_verified"
        record["finished_at_utc"] = utc()
    finally:
        if owned:
            try:
                tools.colab_call({"name": "disconnect_runtime", "arguments": {"release_bridge": False}})
                observe_shutdown(tools)
            finally:
                tools.colab_release({})

    d05_probe_dir = ROOT / "storage/ralph-next/probes/D05"
    d05_probe_dir.mkdir(parents=True, exist_ok=True)
    (d05_probe_dir / "receipt.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["d04", "d05", "both"], default="both")
    parser.add_argument("--workspace", default="storage/ralph-next/probes/D05/workspace")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "config/.env")
    config = load_config()
    ws = (ROOT / args.workspace).resolve()

    out = {}
    if args.mode in {"d04", "both"}:
        out["d04"] = prepare_d04(ws, config)
    if args.mode in {"d05", "both"}:
        out["d05"] = run_d05(ws, config)
    print(json.dumps({"status": "ok", "modes": list(out.keys())}, indent=2))


if __name__ == "__main__":
    main()
