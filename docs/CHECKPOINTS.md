# Computation checkpoints and VM loss

A task checkpoint is not a model checkpoint. Git/handoff files preserve decisions
and code; training must save its own model, optimizer/scheduler/scaler, RNG state,
step/epoch and data cursor. Neither kind recovers unsaved tensors from a lost VM.

The workflow-checkpoints skill is installed in **new task workspaces**, including
the same stdlib helper under `.claude/skills`. The controller exposes
`checkpoint_publish`, `checkpoint_list`, `checkpoint_restore` through the existing
cloud CLI/MCP tools. These copy files already downloaded to the task workspace
into `storage/checkpoints/<workspace hash>/` on the controller. No cloud credentials
or allocation are involved. Restores require code/data/config hashes and a fresh
destination directory; they do not automatically load Python pickle/model files.

For automatic saving **while training on Colab**, stage the skill's standalone
`checkpoints.py` with the training code and configure an already authorized private
durable mount, e.g. an already mounted Google Drive directory. The SDK does not
mount/login to Drive, upload to an unspecified account, or assume `/content` and
`/kaggle/working` survive VM loss. Training calls it at consistent safe boundaries:

```python
from checkpoints import CheckpointStore, AutoCheckpoint

# Replace this with the actual configured persistent mount. Not /content alone.
store = CheckpointStore('/content/drive/MyDrive/private-research/checkpoints/run-123')
saver = AutoCheckpoint(store, interval_seconds=300)

# Within the training loop, after application code has written a consistent state:
receipt = saver.save(
    '.', ['training_state.pt'],
    {'step': completed_step, 'code_sha256': code_hash,
     'data_sha256': data_hash, 'config_sha256': config_hash},
    force=milestone_finished,
)
```

Choose the interval according to state size, IO cost and acceptable lost work;
300 seconds is a configurable example, not a runtime promise. Every file is copied
and hashed, then a completion manifest is published. Interrupted staging entries
are ignored; corrupt committed entries are excluded from verified retrieval.
Durable mount semantics still depend on its filesystem/provider. The library
checks identity and compatibility, not semantic completeness of saved ML state.

After reconnecting a fresh VM, `store.list()` returns verified manifests newest
first. Choose a compatible one, then:

```python
store.restore(checkpoint_id, 'recovered', {
    'code_sha256': code_hash, 'data_sha256': data_hash, 'config_sha256': config_hash,
})
# Application code now loads trusted recovered/training_state.pt and resumes.
```

On the local controller the explicit management CLI is:

```powershell
python scripts/checkpoints.py --store PATH_TO_DURABLE_STORE list
python scripts/checkpoints.py --store PATH_TO_DURABLE_STORE restore CHECKPOINT_ID --destination FRESH_DIRECTORY --expected expected-hashes.json
```

Keep the private store backed up; controller disk loss is outside VM-loss recovery.
No automatic pruning is performed yet, so old recovery points are retained.

The regression launches a real child process, saves RNG/progress/accumulator state,
terminates it with exit17, removes its simulated VM directory, restores into a new
directory and resumes in another process. It compares exactly with uninterrupted
execution. This proves local interruption/retrieval semantics, **not** a live
Colab/Drive interruption, Torch optimizer compatibility or cross-GPU reproducibility.
