# CPU-only accelerator audit ? 2026-11-02

## Diagnosis

CPU-only execution normally has no GPU/TPU accelerator profile. Kaggle CPU
submission correctly omits --accelerator and sets enable_gpu=false and
enable_tpu=false. The completed historical recovery job
6a4bcb8799304625a0fe0dc82a609efa has those flags, no machine_shape/accelerator
profile, actual downloaded results and a successful independent numerical check.
Its absent accelerator profile is expected, not an execution error.

The first historical Kaggle run failed with the explicit remote error
"No kernel name found in notebook and no override provided" (missing kernelspec).
The recovery also encountered native sandbox networking denial. A host push then
received HTTP400; shortening the slug preceded a successful push, but the saved
400 response alone does not prove which server validation condition caused it.
None of these logs identifies absent CPU accelerator profile as a submission error.
The final validator rejected unmeasured physical accelerator absence; the CPU
calculation itself passed. Historical task statuses/logs are preserved.

## Fixes

Validator guidance now applies to both Kaggle and Colab CPU: missing GPU name,
CUDA, nvidia-smi or nonempty accelerator profile alone is not a failure. Verify
CPU selection, actual remote execution and outputs. Do not turn metadata into a
claim of observed physical hardware. Explicit requirements to prove no accelerator
was allocated still need execution-specific evidence. GPU/TPU requests still need
actual requested-device observations; Colab CPU also needs shutdown verification.

Kaggle CPU submissions now omit --accelerator even if a custom accelerator map
incorrectly gives CPU a nonempty value. Added regressions cover GPU-to-CPU
metadata clearing, CPU Kaggle flags and Colab CPU notebook execution without an
accelerator profile. The Colab browser helper already maps CPU to the empty radio
value, independently of GPU identifiers. The T4-specific colab_experiment.py is a
purpose-built two-moons fixture, not the generic CPU execution endpoint; its T4
requirement remains unchanged.

## Verification and boundary

74 unit/integration tests passed in40.269s; git diff --check passed. Colab CPU
execution was tested through mocked transport, not a new authenticated CPU VM.
Attempted read-only current browser inspection failed: ECONNREFUSED127.0.0.1:9222.
No new browser/runtime, GPU or Kaggle job was started; no task was marked completed.
The running daemon may retain an already-loaded cloud module; apply code changes
with a normal idle service restart, not during an active task. Role instructions
are read when preparing future role runs.

Sources:
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
