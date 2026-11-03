# Ralph follow-up: Unicode and completion notice clarity

User reported the completion message for probe #29 contained question marks and
did not say whether another action was needed. The message was informational only;
the real user's status query and approval had already been verified.

The corruption is present in the stored final_response and the ad-hoc probe
finalization script, before Discord transport. That script was generated through
a PowerShell here-string piped to Python; the legacy PowerShell pipeline encoding
lost Vietnamese characters. Encoding the already damaged string as UTF-8 cannot
restore those characters. This is not evidence that Discord rejects Vietnamese.

Operational scripts should be UTF-8 files edited directly, or use Unicode escapes
for literals crossing a legacy shell pipeline. Do not generate localized source
through an unspecified shell pipe encoding. Existing production sources use UTF-8
and JSON transports; add a round-trip regression at the actual notification path.

For this probe, issue a single clear English completion revision through the normal
outbox: "Routing test #29 passed. No action is needed from you." Retain the old
corrupted response as an event for diagnosis; do not erase the user's history.
This change does not claim native model execution or GPU acceptance passed.

Completed: the normal revisioned outbox queued the English correction and a test
confirmed exact Vietnamese text survives database storage, outbox delivery and
Discord request JSON. Old damaged text is retained in a diagnostic event.
