# Mini-DDPM workflow sample (P2.7)

A small DDPM on CIFAR-10 used to exercise the workflow end to end: submit → progress → checkpoint on the
shared store → interrupt/resume (also across Colab ↔ Kaggle) → light results home. It is a workflow smoke,
not a paper reproduction; no FID threshold is claimed.

- `train.py` — torch training loop; `--smoke` uses synthetic 8×8 data and finishes in seconds on CPU.
- `checkpointing.py` — torch-optional publish/restore protocol (files first, manifest last), the same layout
  the controller reads (`core/artifact_store.py`), plus `WORKFLOW_PROGRESS:` lines for the model-free poller.

Environment set by the job adapters: `WORKFLOW_CHECKPOINT_URI` (e.g. `rclone:gdrive:workflow-runs/<task>/<run>`
on Kaggle, or the mounted Drive path on Colab), `WORKFLOW_EXPERIMENT_JOB_ID`, `WORKFLOW_JOB_REQUEST_KEY`.
On Kaggle the rclone token must be added by the user as a Kaggle secret and written to the rclone config at
the start of the notebook; Colab uses `drive.mount`.

Checks: `python -m unittest discover -s tests -p "test_p2_smoke_cpu.py"` (torch part runs only where torch is
installed) and `python scripts/verify_p2_e2e.py --offline`.
