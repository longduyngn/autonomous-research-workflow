"""Independent stdlib-only acceptance for the explicit full-run experiment."""
import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(root, phase):
    spec = load(root / "inputs/experiment_spec.json")
    for name, digest in spec["input_sha256"].items():
        assert hashlib.sha256((root / "inputs" / name).read_bytes()).hexdigest() == digest, name
    compile((root / "train.py").read_text(encoding="utf-8"), "train.py", "exec")
    if phase == "source":
        result = subprocess.run([sys.executable, "local_checks.py"], cwd=root, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        print("PASS: supplied inputs, compiled experiment and local data checks")
        return
    out = root / "artifacts/experiment"
    receipt = load(root / "artifacts/remote_receipt.json")
    assert receipt["status"] == "completed"
    assert "T4" in receipt["measurement"]["gpu_name"]
    assert receipt["shutdown_verified"] is True
    assert receipt["shutdown_state"]["runtime_stopped"] is True
    assert receipt["shutdown_state"]["active_session_count"] == 0
    for name, digest in receipt["export_sha256"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
    assert hashlib.sha256((root / "artifacts/executed_experiment.ipynb").read_bytes()).hexdigest() == receipt["notebook_sha256"]
    hardware = load(out / "hardware.json")
    assert hardware["run_id"] == receipt["run_id"] and "T4" in hardware["gpu_name"]
    assert hardware["device"].startswith("cuda") and hardware["torch_version"]
    for name, digest in spec["input_sha256"].items():
        assert hardware["input_sha256"][name] == digest
    metrics = load(out / "metrics.json")["runs"]
    weights = load(out / "weights.json")["runs"]
    predictions = load(out / "predictions.json")["runs"]
    curves = load(out / "learning_curves.json")["runs"]
    expected = {(model, seed) for model in spec["models"] for seed in spec["seeds"]}
    def index(rows):
        assert len(rows) == len(expected)
        result = {(r["model"], r["seed"]): r for r in rows}
        assert set(result) == expected
        return result
    mi, wi, pi, ci = map(index, (metrics, weights, predictions, curves))
    with (root / "inputs/test.csv").open(newline="", encoding="utf-8") as f:
        test = list(csv.DictReader(f))
    accuracy = {}
    for key in sorted(expected):
        layers = wi[key]["layers"]
        assert len(layers) == (1 if key[0] == "linear" else 3)
        pred = pi[key]
        assert len(pred["probabilities"]) == len(test) == spec["test_count"]
        assert len(pred["predictions"]) == len(test)
        correct = 0
        max_error = 0
        for i, row in enumerate(test):
            x = [float(row["x1"]), float(row["x2"])]
            for j, layer in enumerate(layers):
                assert len(layer["weight"]) == len(layer["bias"])
                y = []
                for vector, bias in zip(layer["weight"], layer["bias"]):
                    assert len(vector) == len(x)
                    value = math.fsum(a * b for a, b in zip(vector, x)) + bias
                    y.append(max(0, value) if j < len(layers) - 1 else value)
                x = y
            assert len(x) == 1 and math.isfinite(x[0])
            logit = max(-700, min(700, x[0]))
            probability = 1 / (1 + math.exp(-logit))
            max_error = max(max_error, abs(probability - pred["probabilities"][i]))
            predicted = int(probability >= .5)
            assert predicted == pred["predictions"][i], (key, i)
            correct += predicted == int(row["label"])
        assert max_error < 0.0001, (key, max_error)
        observed = correct / len(test)
        assert abs(observed - mi[key]["accuracy"]) < 1e-10
        assert mi[key]["device"].startswith("cuda")
        assert mi[key]["elapsed_seconds"] > 0 and math.isfinite(mi[key]["loss"])
        assert len(ci[key]["losses"]) == spec["epochs"]
        assert all(math.isfinite(v) and v >= 0 for v in ci[key]["losses"])
        accuracy[f"{key[0]}:{key[1]}"] = observed
    mlp = sum(mi[("mlp", seed)]["accuracy"] for seed in spec["seeds"]) / len(spec["seeds"])
    linear = sum(mi[("linear", seed)]["accuracy"] for seed in spec["seeds"]) / len(spec["seeds"])
    assert mlp >= spec["minimum_mlp_mean_accuracy"], (mlp, "MLP target not met")
    assert mlp - linear >= spec["minimum_accuracy_gap"], (mlp, linear)
    assert (out / "learning_curves.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    record = {"run_id": receipt["run_id"], "predictions_recomputed": len(test) * len(expected),
              "accuracy": accuracy, "mlp_mean_accuracy": mlp, "linear_mean_accuracy": linear,
              "shutdown_verified": True, "method": "independent stdlib CPU inference from exported weights and supplied CSV"}
    (root / ".workflow/independent_verification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if phase == "final":
        report = (root / "REPORT.md").read_text(encoding="utf-8")
        assert len(report) > 500 and receipt["run_id"] in report and "T4" in report
        assert (root / "REPRODUCE.md").is_file()
        task_id = os.environ.get("WORKFLOW_ACCEPTANCE_TASK_ID")
        if task_id and spec.get("controlled_local_failures"):
            gate = ROOT / "storage/full-run-20261102/gates" / f"task_{int(task_id):05d}.json"
            gate.parent.mkdir(parents=True, exist_ok=True)
            state = load(gate) if gate.exists() else {"failures_emitted": 0}
            if state["failures_emitted"] < 2:
                state["failures_emitted"] += 1
                gate.write_text(json.dumps(state, indent=2), encoding="utf-8")
                raise AssertionError(f"CONTROLLED_E2E_FAULT {state['failures_emitted']}/2: intentional local acceptance probe. GPU outputs are verified; do not rerun training. Preserve results and add a brief repair/replan note.")
    print("PASS: " + json.dumps(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--phase", choices=["source", "experiment", "final"], required=True)
    args = parser.parse_args()
    check(Path(args.workspace).resolve(), args.phase)
