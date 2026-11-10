# Compute: Colab/Kaggle job adapters and future rented machines

Managed notebook/batch jobs use `core/compute_jobs.py` (P2.5): `JobSpec`, `JobController` (submission journal:
SUBMITTING before the effect, SUBMISSION_UNKNOWN never resubmitted), adapters in `core/compute_backends.py`
(`ColabJobAdapter`, `KaggleJobAdapter`), the model-free poller `core/compute_poller.py`, the shared store and
manifest in `core/artifact_store.py`, light results in `core/results.py`, and cross-backend resume via
`plan_successor`. Kaggle status never cancels; `kaggle_job_cancel` targets the pushed version only.

## Future rented machines (provisioning, disabled)

Shadeform, VastAI, Runpod and remote SSH are registered capability names, all
disabled. No vendor SDK, account access, provisioning or SSH connection is included.
`compute_capabilities` reports this explicitly. Existing local/Colab/Kaggle paths
are unchanged; a plan cannot silently fall back to a paid provider.

Implement `core.compute.ProvisioningAdapter` (legacy name `ComputeAdapter`) in a separate adapter module, then register
it explicitly in host code. Adding `enabled: true` alone cannot enable provisioning.
The lifecycle contract is provision(request, durable_request_key), observe(identity),
stop(identity), and download(identity, files, destination). `ComputeRequest` requires
finite duration, a total cost cap and an explicit durable checkpoint destination.

Before wiring an adapter into orchestration, add tests for ambiguous submission,
restart and reconciliation, fenced ownership, cost/deadline enforcement, verified
shutdown, private artifact download and checkpoint recovery. Reuse exact-action
approvals for provisioning with price, region, image, hardware and maximum cost
included in the approved action. A changed quote needs a new decision. API failure
means unknown state, not proof of a stopped VM. Existing SSH hosts need the same
ownership/checkpoint lifecycle even when provisioning costs zero.

This is an extension seam, not a claim that these services currently run tasks.
