# Full Discord / mixed-agent / Colab T4 verification

- [x] Prepare an explicit nontrivial experiment, supplied data and independent
  acceptance checks. Record original config and current queue/runtime state.
- [x] Submit clearly labelled bot-authored test requests with real attachments;
  simulate authorized human ingress only inside the production command handler.
  Preserve the actual message IDs and disclose the simulation boundary.
- [x] Exercise mixed Codex research/validation and Antigravity execution/reporting,
  native hooks/skills, isolated inputs, milestones, two-worker overflow queue and
  idempotent request handling.
- [x] Execute six seeded model trainings on actual Colab T4; export raw predictions,
  weights, learning curves, measurements and an executed notebook. Independently
  recompute predictions/accuracy from exported weights and supplied test data.
- [x] Exercise bounded repairs/replanning with two explicitly labelled local-only
  acceptance faults after cloud results are safely exported and runtime stopped.
  Never rerun a successful GPU experiment to exercise a controller branch.
- [x] Inspect live dashboard/stages/run transcripts and actual Discord delivery.
  Verify zero active Colab sessions, retain failures, restore original config and
  provide a feature-by-feature report separating live/mocked/unexercised behavior.

The synthetic two-moons experiment is a full run of its supplied specification,
not a claim to reproduce a published paper. API-only models, OpenCode and Claude
were not live-tested. Kaggle CPU was tested separately; successful recovery required controller assistance.
Unimplemented approval/resource queues remain outside scope.

Results and limitations: [FULL_RUN_RESULTS_20261102.md](FULL_RUN_RESULTS_20261102.md).
Original failures are retained in tasks20/24/25; linked local-report recovery26 completed.

Task27 numerical verification passed but final hardware-evidence validation escalated;
no new cloud job was started. Original config restored and listener reconnected.
