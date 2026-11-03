# Human acceptance and duplicate reply repair

The real user sent !status in task log and approved the non-executing probe in
approval list. Controller records confirm the same authenticated owner, logical
proposal scope and correct actual reply channels. Probe #29 was completed only
after checking those records and consuming the exact approved action. No model,
shell or GPU ran as part of this routing probe. Evidence is retained locally in
storage/ralph-discord-release/human-acceptance-verified.json.

Two release pings in different channels were explicitly requested for the manual
acceptance handoff. No repeat ping is needed for this repair.

The duplicate approval confirmation was a real defect: one incoming Discord event
has two bot response IDs, separated by less than a second. on_message persisted a
response and awaited its immediate send while heartbeat independently selected the
same undelivered row. There was one active listener, not two.

Both paths now claim a persisted delivery owner/expiry atomically before sending.
Only the current owner may acknowledge delivery; late acknowledgement from an old
claim cannot finish a newer claim. Pending queries skip claimed responses to avoid
blocking later replies. Failed/unknown sends retain their lease until retry time.
This removes the ordinary concurrent double-send. Send/ack process crashes remain
at-least-once delivery; this is not a claim of exactly-once network delivery.

New regressions race two independent DB connections for one response and test stale
acknowledgements. Full suite passed 126 tests before the final pending-query filter;
the delivery/conversation suites are rerun after that filter. Logs remain under
storage/ralph-discord-release/. Native AGY authentication/timeout, Codex nested-tool
sandbox and Linux container execution remain the previously recorded live limits.
